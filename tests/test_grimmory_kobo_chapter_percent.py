"""A Grimmory EPUB write carries the chapter percentage the Kobo bookmark is built from.

Grimmory turns the web-reader position BookBridge writes into a Kobo bookmark: a
span in the chapter, picked by contentSourceProgressPercent. Without that value
it works the percentage out from the CFI or, failing that, from the book-wide
percentage, which it measures on another scale (the chapters' raw HTML sizes)
and does not clamp.

Observed live (House of Earth and Blood, 2026-10-23): the listen ended at
4:46:07, chapter 12 (text/part0020.html) 34% through, written to Grimmory as
epubcfi(/6/44!/4/2/56:0) at 18.0%. Grimmory handed the Kobo span kobo.79.1 at
"119%" into the chapter, and the Kobo jumped five pages, to the chapter's end.
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.api.booklore_client import BookloreClient
from src.sync_clients.sync_client_interface import LocatorResult

CFI = "epubcfi(/6/44!/4/2/56:0)"
HREF = "text/part0020.html"


def _client(book_type="EPUB"):
    detail = {
        "id": 888,
        "fileName": "House of Earth and Blood.epub",
        "primaryFile": {"id": 8880, "fileName": "House of Earth and Blood.epub", "bookType": book_type},
        "epubProgress": {"percentage": 17.2},
    }
    client = BookloreClient.__new__(BookloreClient)
    client.db = None
    client._cache_lock = threading.RLock()
    client._book_cache = {detail["fileName"].lower(): detail}
    client._book_id_cache = {888: detail}
    client._exact_filename_miss_cache = {}
    client._refresh_cooldown = 300
    client._cache_timestamp = time.time()
    client._creds = {}
    client._epub_cfi_write_disabled_for_books = set()
    ok = MagicMock(status_code=204)
    ok.__bool__.return_value = True
    verify = MagicMock(status_code=200)
    verify.__bool__.return_value = True
    client._make_request = MagicMock(side_effect=[ok, verify])
    client._parse_json_response = MagicMock(return_value={**detail, "epubProgress": {"percentage": 18.0}})
    return client


def _written(client, locator, pct=0.1803):
    client.update_progress_by_book_id(888, pct, locator)
    method, path, payload = client._make_request.call_args_list[0].args[:3]
    assert (method, path) == ("POST", "/api/v1/books/progress")
    return payload["fileProgress"]


class TestTheChapterPercentIsSent:
    def test_house_of_earth_and_blood(self):
        locator = LocatorResult(percentage=0.1803, cfi=CFI, href=HREF, chapter_progress=0.3413)
        written = _written(_client(), locator)
        assert written["positionData"] == CFI
        assert written["positionHref"] == HREF
        assert written["contentSourceProgressPercent"] == pytest.approx(34.13)

    def test_the_start_of_a_chapter_is_a_real_value(self):
        locator = LocatorResult(percentage=0.17, cfi=CFI, href=HREF, chapter_progress=0.0)
        assert _written(_client(), locator)["contentSourceProgressPercent"] == 0.0

    def test_the_end_of_a_chapter(self):
        locator = LocatorResult(percentage=0.19, cfi=CFI, href=HREF, chapter_progress=1.0)
        assert _written(_client(), locator)["contentSourceProgressPercent"] == 100.0


class TestNothingIsSentWithoutAChapter:
    @pytest.mark.parametrize("chapter_progress", [None, 1.19, -0.1, float("nan"), True, "abc"])
    def test_a_missing_or_impossible_fraction(self, chapter_progress):
        locator = LocatorResult(percentage=0.18, cfi=CFI, href=HREF, chapter_progress=chapter_progress)
        assert "contentSourceProgressPercent" not in _written(_client(), locator)

    def test_no_href_means_no_chapter_to_place_it_in(self):
        locator = LocatorResult(percentage=0.18, cfi=CFI, chapter_progress=0.34)
        assert "contentSourceProgressPercent" not in _written(_client(), locator)

    def test_a_bare_percentage(self):
        assert "contentSourceProgressPercent" not in _written(_client(), LocatorResult(percentage=0.18))

    def test_only_for_epubs(self):
        locator = LocatorResult(percentage=0.18, href="page-12", chapter_progress=0.34)
        written = _written(_client(book_type="PDF"), locator)
        assert "contentSourceProgressPercent" not in written


class TestTheRoundTrip:
    def test_what_is_written_reads_back_as_the_same_position(self):
        """0003 reads contentSourceProgressPercent back as the chapter fraction."""
        from src.sync_clients.booklore_sync_client import BookloreSyncClient
        locator = LocatorResult(percentage=0.1803, cfi=CFI, href=HREF, chapter_progress=0.3413)
        sent = _written(_client(), locator)["contentSourceProgressPercent"]
        read_back = BookloreSyncClient._chapter_progress_from_rich({"content_source_pct": sent})
        assert read_back == pytest.approx(0.3413)
