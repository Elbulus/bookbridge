"""Grimmory "reading watch": auto-match books the user starts reading.

The BookOrbit feature (test_bookorbit_reading_watch.py) extended to Grimmory.
Grimmory's Continue Reading list (GET /api/v1/app/books/continue-reading)
holds the user's READING / RE_READING books, and a Kobo sync marks a book
READING once it passes the Kobo reading threshold (1% by default) — so a book
started on a Kobo is picked up as soon as the Kobo syncs.

Covers `BookloreClient.list_continue_reading_books` (shape, progress and format
filtering, an older Grimmory without the endpoint) and the reading-watch pass
running for the Grimmory watcher (source 'BookLore', prefix BOOKLORE).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.booklore_client import BookloreClient
from tests.test_bookorbit_reading_watch import _build_service, _make_audio_match


def _response(status_code=200, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _summary(book_id=897, title="A Court of Silver Flames", authors=("Sarah J. Maas",),
             file_name="A Court of Silver Flames.epub", file_type="EPUB", progress=35.0):
    """An AppBookSummary as Grimmory serialises it (only the fields used)."""
    return {
        "id": book_id, "title": title, "authors": list(authors),
        "primaryFileName": file_name, "primaryFileType": file_type,
        "readProgress": progress, "readStatus": "READING",
    }


class TestListContinueReadingBooks(unittest.TestCase):
    def setUp(self):
        self.client = BookloreClient()

    def _list(self, payload, status_code=200, **kwargs):
        self.client._make_request = MagicMock(return_value=_response(status_code, payload))
        return self.client.list_continue_reading_books(**kwargs)

    def test_shapes_a_summary_like_bookorbit(self):
        out = self._list([_summary(title=" A Court of Silver Flames ",
                                   authors=("Sarah J. Maas", "Co Author"))], min_progress=1.0)
        self.assertEqual(out, [{
            "id": 897, "title": "A Court of Silver Flames", "author": "Sarah J. Maas, Co Author",
            "fileName": "A Court of Silver Flames.epub", "progress": 35.0,
        }])

    def test_asks_for_the_continue_reading_list_with_a_limit(self):
        self._list([], limit=25)
        method, endpoint = self.client._make_request.call_args.args[:2]
        self.assertEqual(method, "GET")
        self.assertEqual(endpoint, "/api/v1/app/books/continue-reading?limit=25")

    def test_progress_below_the_minimum_is_dropped(self):
        out = self._list([
            _summary(book_id=1, progress=0.5),
            _summary(book_id=2, progress=1.0),
            _summary(book_id=3, progress=None),
            _summary(book_id=4, progress=62.0),
        ], min_progress=1.0)
        self.assertEqual([b["id"] for b in out], [2, 4])

    def test_only_epub_and_pdf_are_routed(self):
        out = self._list([
            _summary(book_id=1, file_type="EPUB"),
            _summary(book_id=2, file_type="PDF", file_name="b.pdf"),
            _summary(book_id=3, file_type="CBX", file_name="c.cbz"),
            _summary(book_id=4, file_type="AUDIOBOOK", file_name="d.m4b"),
            _summary(book_id=5, file_type=None),
        ])
        self.assertEqual([b["id"] for b in out], [1, 2])

    def test_a_book_without_an_id_or_file_name_is_skipped(self):
        out = self._list([_summary(book_id=None), _summary(file_name=None), _summary(file_name="  ")])
        self.assertEqual(out, [])

    def test_an_older_grimmory_without_the_endpoint_gives_nothing(self):
        self.assertEqual(self._list(None, status_code=404), [])

    def test_a_failed_request_gives_nothing(self):
        self.assertEqual(self._list(None, status_code=500), [])
        self.client._make_request = MagicMock(return_value=None)
        self.assertEqual(self.client.list_continue_reading_books(), [])

    def test_an_unexpected_body_gives_nothing(self):
        self.assertEqual(self._list({"content": [_summary()]}), [])


READING_WATCH_ON = {
    "BOOKLORE_READING_WATCH_ENABLED": "true",
    "BOOKLORE_SHELF_WATCH_ENABLED": "false",
}


def _grimmory_service(**kwargs):
    return _build_service(source_name="BookLore", env_prefix="BOOKLORE", **kwargs)


class TestGrimmoryReadingWatch(unittest.TestCase):
    def test_off_by_default(self):
        svc, client, _db, _bms, _ss = _grimmory_service(suggestions_result=None)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOKLORE_READING_WATCH_ENABLED", None)
            stats = svc._process_reading_watch(user_id=None)
        client.list_continue_reading_books.assert_not_called()
        self.assertFalse(stats["enabled"])

    def test_runs_with_the_shelf_watch_pass_even_when_shelf_watch_is_off(self):
        svc, client, _db, _bms, _ss = _grimmory_service(suggestions_result=None, reading_books=[])
        with patch.dict(os.environ, READING_WATCH_ON, clear=False):
            svc.process_watch_shelf()
        client.list_continue_reading_books.assert_called_once()

    def test_no_audiobook_makes_a_grimmory_ebook_only_mapping(self):
        svc, client, _db, bms, _ss = _grimmory_service(suggestions_result=None)
        with patch.dict(os.environ, READING_WATCH_ON, clear=False):
            stats = svc._process_reading_watch(user_id=None)
        self.assertEqual(stats["ebook_only"], 1)
        kwargs = bms.create_ebook_only_mapping.call_args.kwargs
        self.assertEqual(kwargs["ebook_source"], "BookLore")
        self.assertEqual(kwargs["ebook_source_id"], "5")
        self.assertEqual(kwargs["booklore_ebook_id"], "5")
        client.move_between_shelves.assert_not_called()

    def test_an_audiobook_match_is_only_ever_a_suggestion(self):
        svc, client, db, bms, _ss = _grimmory_service(
            suggestions_result={"matches": [_make_audio_match(score=100.0)]},
        )
        with patch.dict(os.environ, READING_WATCH_ON, clear=False):
            stats = svc._process_reading_watch(user_id=None)
        self.assertEqual(stats["suggested"], 1)
        bms.create_audio_mapping_from_match.assert_not_called()
        saved = db.save_pending_suggestion.call_args.args[0]
        self.assertEqual(saved.origin, "reading_watch")

    def test_a_book_already_mapped_is_left_alone(self):
        svc, _client, _db, bms, ss = _grimmory_service(suggestions_result=None, already_mapped=True)
        with patch.dict(os.environ, READING_WATCH_ON, clear=False):
            stats = svc._process_reading_watch(user_id=None)
        self.assertEqual(stats["skipped_existing"], 1)
        ss._scan_single_ebook.assert_not_called()
        bms.create_ebook_only_mapping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
