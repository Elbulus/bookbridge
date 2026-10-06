"""Grimmory "reading watch": auto-match books the user starts reading.

The BookOrbit feature (test_bookorbit_reading_watch.py) extended to Grimmory.
Grimmory's Continue Reading list (GET /api/v1/app/books/continue-reading)
holds the user's READING / RE_READING books, and a Kobo sync marks a book
READING once it passes the Kobo reading threshold (1% by default) — so a book
started on a Kobo is picked up as soon as the Kobo syncs.

Grimmory's endpoint returns nothing for an admin account (it filters on the
admin's library list, which is null for "all libraries"; observed live: the
admin's list was empty while reading a book at 35%, a regular user's was full),
so the client falls back to the books marked Reading in GET /api/v1/books.

Covers `BookloreClient.list_continue_reading_books` (shape, progress and format
filtering, the library fallback and its throttle) and the reading-watch pass
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


def _library_book(book_id=897, title="A Court of Silver Flames", status="READING",
                  last_read="2026-10-05T18:22:39Z", file_name="A Court of Silver Flames.epub",
                  book_type="EPUB", kobo=None, epub=35.0, koreader=None, pdf=None):
    """A Book from GET /api/v1/books as Grimmory serialises it (only the fields used)."""
    def prog(pct):
        return {"percentage": pct} if pct is not None else None
    return {
        "id": book_id, "title": title, "readStatus": status, "lastReadTime": last_read,
        "metadata": {"title": title, "authors": ["Sarah J. Maas"]},
        "primaryFile": {"fileName": file_name, "bookType": book_type},
        "koreaderProgress": prog(koreader), "koboProgress": prog(kobo),
        "epubProgress": prog(epub), "pdfProgress": prog(pdf),
    }


class _ClientTest(unittest.TestCase):
    def setUp(self):
        self.client = BookloreClient()
        self.calls = []

    def _serve(self, endpoint=(200, []), library=(200, [])):
        """Answer the Continue Reading endpoint and the full book list separately."""
        def make_request(method, path, *args, **kwargs):
            self.calls.append(path)
            status, payload = endpoint if "continue-reading" in path else library
            return None if status is None else _response(status, payload)
        self.client._make_request = MagicMock(side_effect=make_request)


class TestListContinueReadingBooks(_ClientTest):
    def _list(self, payload, status_code=200, **kwargs):
        self._serve(endpoint=(status_code, payload))
        return self.client.list_continue_reading_books(**kwargs)

    def test_shapes_a_summary_like_bookorbit(self):
        out = self._list([_summary(title=" A Court of Silver Flames ",
                                   authors=("Sarah J. Maas", "Co Author"))], min_progress=1.0)
        self.assertEqual(out, [{
            "id": 897, "title": "A Court of Silver Flames", "author": "Sarah J. Maas, Co Author",
            "fileName": "A Court of Silver Flames.epub", "progress": 35.0,
        }])

    def test_asks_for_the_continue_reading_list_with_a_limit(self):
        self._list([_summary()], limit=25)
        self.assertEqual(self.calls, ["/api/v1/app/books/continue-reading?limit=25"])

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
        out = self._list([_summary(book_id=None), _summary(file_name=None), _summary(file_name="  "),
                          _summary(book_id=7)])
        self.assertEqual([b["id"] for b in out], [7])


class TestLibraryFallback(_ClientTest):
    """Grimmory's endpoint returns [] for an admin (its library filter is null
    for "all libraries"), and 404 on a Grimmory without it. The same list is
    then built from GET /api/v1/books."""

    def test_an_admin_with_an_empty_list_falls_back_to_the_library(self):
        self._serve(endpoint=(200, []), library=(200, [_library_book()]))
        out = self.client.list_continue_reading_books(min_progress=1.0)
        self.assertEqual(out, [{
            "id": 897, "title": "A Court of Silver Flames", "author": "Sarah J. Maas",
            "fileName": "A Court of Silver Flames.epub", "progress": 35.0,
        }])
        self.assertEqual(self.calls[-1], "/api/v1/books")

    def test_an_older_grimmory_falls_back_too(self):
        self._serve(endpoint=(404, None), library=(200, [_library_book()]))
        self.assertEqual([b["id"] for b in self.client.list_continue_reading_books()], [897])

    def test_a_list_from_the_endpoint_is_used_without_the_library(self):
        self._serve(endpoint=(200, [_summary()]), library=(200, [_library_book(book_id=1)]))
        self.assertEqual([b["id"] for b in self.client.list_continue_reading_books()], [897])
        self.assertNotIn("/api/v1/books", self.calls)

    def test_only_books_being_read_count(self):
        self._serve(library=(200, [
            _library_book(book_id=1, status="READING"),
            _library_book(book_id=2, status="RE_READING"),
            _library_book(book_id=3, status="READ"),
            _library_book(book_id=4, status="UNREAD"),
            _library_book(book_id=5, status=None),
            _library_book(book_id=6, last_read=None),
            _library_book(book_id=7, book_type="AUDIOBOOK", file_name="x.m4b"),
        ]))
        self.assertEqual(sorted(b["id"] for b in self.client.list_continue_reading_books()), [1, 2])

    def test_progress_comes_from_koreader_then_kobo_then_web_reader_then_pdf(self):
        self._serve(library=(200, [
            _library_book(book_id=1, koreader=12.0, kobo=20.0, epub=30.0),
            _library_book(book_id=2, kobo=20.0, epub=30.0),
            _library_book(book_id=3, epub=30.0),
            _library_book(book_id=4, epub=None, pdf=40.0, book_type="PDF", file_name="d.pdf"),
        ]))
        progress = {b["id"]: b["progress"] for b in self.client.list_continue_reading_books()}
        self.assertEqual(progress, {1: 12.0, 2: 20.0, 3: 30.0, 4: 40.0})

    def test_most_recently_read_first_and_limited(self):
        self._serve(library=(200, [
            _library_book(book_id=1, last_read="2026-10-01T08:00:00Z"),
            _library_book(book_id=2, last_read="2026-10-05T18:22:39.512Z"),
            _library_book(book_id=3, last_read="2026-10-03T21:00:00Z"),
        ]))
        out = self.client.list_continue_reading_books(limit=2)
        self.assertEqual([b["id"] for b in out], [2, 3])

    def test_the_library_is_read_at_most_every_half_hour(self):
        self._serve(library=(200, [_library_book()]))
        with patch("src.api.booklore_client.time.time", return_value=1_000.0):
            self.client.list_continue_reading_books()
            self.client.list_continue_reading_books()
        self.assertEqual(self.calls.count("/api/v1/books"), 1)
        with patch("src.api.booklore_client.time.time", return_value=1_000.0 + 31 * 60):
            self.client.list_continue_reading_books()
        self.assertEqual(self.calls.count("/api/v1/books"), 2)

    def test_nothing_anywhere_gives_nothing(self):
        for endpoint, library in [((404, None), (500, None)), ((None, None), (None, None)),
                                  ((200, {"content": []}), (200, {"content": []}))]:
            with self.subTest(endpoint=endpoint, library=library):
                self.client._reading_watch_library_cache = None
                self._serve(endpoint=endpoint, library=library)
                self.assertEqual(self.client.list_continue_reading_books(), [])


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
