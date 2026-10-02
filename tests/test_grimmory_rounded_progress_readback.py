"""Grimmory stores EPUB progress rounded to one decimal place.

BookBridge used to persist the percentage it *sent*, so every write left a
standing delta against what Grimmory reports back (24.5598% written ->
24.6000% read). On the next cycle that artefact looks like a fresh change on
Grimmory's side, which either re-writes the same position forever or — once the
own-write marker has expired, or been lost to a restart — promotes the rounding
artefact to leader and drags the audiobook position backwards.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.booklore_client import BookloreClient
from src.db.models import Book
from src.sync_clients.booklore_sync_client import BookloreSyncClient
from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest


WRITTEN = 0.24559822086097714   # what the alignment layer asks for
REPORTED = 0.246                # what Grimmory holds afterwards (24.6%)


def epub_book():
    return Book(
        abs_id="book-1",
        abs_title="This Inevitable Ruin",
        ebook_filename="ruin.epub",
        original_ebook_filename="ruin.epub",
        ebook_source="Grimmory",
        ebook_source_id="945",
        status="active",
    )


def write_request(pct=WRITTEN, cfi="epubcfi(/6/66!/4/10:0)"):
    return UpdateProgressRequest(
        locator_result=LocatorResult(percentage=pct, cfi=cfi),
        current_state=SimpleNamespace(current={"pct": pct}),
    )


def sync_client_writing(observed_pct):
    """A Grimmory API double whose write reports back `observed_pct`."""
    api = MagicMock(spec=BookloreClient)

    def update(book_id, percentage, rich_locator=None, observed=None):
        if observed is not None and observed_pct is not None:
            observed["pct"] = observed_pct
        return True

    api.update_progress_by_book_id = update
    return BookloreSyncClient(api, MagicMock())


def test_persisted_state_is_what_grimmory_reports_not_what_we_sent():
    result = sync_client_writing(REPORTED).update_progress(epub_book(), write_request())

    assert result.success
    assert result.updated_state["pct"] == pytest.approx(REPORTED)
    assert result.location == pytest.approx(REPORTED)


def test_next_read_of_the_same_position_shows_no_delta():
    """The regression itself: write, then read back, and see no change."""
    sync = sync_client_writing(REPORTED)

    persisted = sync.update_progress(epub_book(), write_request()).updated_state["pct"]

    # What Grimmory reports on the following cycle, nothing having moved.
    assert persisted == pytest.approx(REPORTED), "persisted state must match the service"


def test_own_write_marker_records_the_reported_position():
    from src.services import write_tracker

    book = epub_book()
    sync = sync_client_writing(REPORTED)
    sync.update_progress(book, write_request())

    marker = write_tracker.get_recent_write("BookLore", book.abs_id)
    assert marker is not None
    assert marker["pct"] == pytest.approx(REPORTED)


def test_unverified_write_falls_back_to_the_requested_percentage():
    """No readback (verification unavailable) must not lose the position."""
    result = sync_client_writing(None).update_progress(epub_book(), write_request())

    assert result.success
    assert result.updated_state["pct"] == pytest.approx(WRITTEN)


def test_cfi_is_still_persisted_alongside_the_reported_percentage():
    result = sync_client_writing(REPORTED).update_progress(epub_book(), write_request())

    assert result.updated_state["cfi"] == "epubcfi(/6/66!/4/10:0)"


def test_writer_without_the_parameter_is_called_positionally():
    """A three-argument writer must not be handed an unexpected keyword."""
    api = MagicMock(spec=BookloreClient)

    def legacy(book_id, percentage, rich_locator=None):
        return True

    api.update_progress_by_book_id = legacy
    sync = BookloreSyncClient(api, MagicMock())

    result = sync.update_progress(epub_book(), write_request())

    assert result.success
    assert result.updated_state["pct"] == pytest.approx(WRITTEN)


# --- API level: the client must hand the post-write readback back to callers ---

@pytest.fixture
def grimmory_api():
    with patch.dict(os.environ, {
        "BOOKLORE_SERVER": "http://mock-booklore",
        "BOOKLORE_USER": "testuser",
        "BOOKLORE_PASSWORD": "testpass",
        "DATA_DIR": "/tmp/data",
    }):
        return BookloreClient(database_service=MagicMock())


def _post_then_verify(api, verified_percent):
    """Wire the client's transport to accept a write and report `verified_percent`."""
    api.find_book_by_filename = MagicMock(return_value={
        "id": 945, "bookType": "EPUB", "fileName": "ruin.epub",
    })
    post = MagicMock(status_code=204)
    verify = MagicMock(status_code=200)
    verify.json.return_value = {
        "primaryFile": {"bookType": "EPUB"},
        "epubProgress": {"percentage": verified_percent, "cfi": "epubcfi(/6/66!/4/10:0)"},
    }
    api._make_request = MagicMock(side_effect=[post, verify])


def test_client_reports_the_rounded_percentage_it_observed(grimmory_api):
    _post_then_verify(grimmory_api, 24.6)
    observed = {}

    ok = grimmory_api.update_progress(
        "ruin.epub", WRITTEN, LocatorResult(percentage=WRITTEN), observed=observed
    )

    assert ok is True
    # Sent 24.5598%, Grimmory rounded it to 24.6% — that is what callers must keep.
    assert observed["pct"] == pytest.approx(REPORTED)


def test_client_still_writes_the_full_precision_percentage(grimmory_api):
    """Rounding is the service's business; we must not pre-round the payload."""
    _post_then_verify(grimmory_api, 24.6)

    grimmory_api.update_progress(
        "ruin.epub", WRITTEN, LocatorResult(percentage=WRITTEN), observed={}
    )

    payload = grimmory_api._make_request.call_args_list[0][0][2]
    assert payload["epubProgress"]["percentage"] == pytest.approx(WRITTEN * 100)


def test_client_leaves_observed_untouched_when_the_write_fails(grimmory_api):
    grimmory_api.find_book_by_filename = MagicMock(return_value={
        "id": 945, "bookType": "EPUB", "fileName": "ruin.epub",
    })
    grimmory_api._make_request = MagicMock(return_value=MagicMock(status_code=500))
    observed = {}

    ok = grimmory_api.update_progress(
        "ruin.epub", WRITTEN, LocatorResult(percentage=WRITTEN), observed=observed
    )

    assert ok is False
    assert observed == {}


def test_in_place_cache_holds_the_reported_percentage(grimmory_api):
    """A cache hit must not reintroduce the delta the readback just removed."""
    grimmory_api._book_id_cache = {945: {"epubProgress": {"percentage": 7.0, "cfi": ""}}}
    _post_then_verify(grimmory_api, 24.6)

    grimmory_api.update_progress(
        "ruin.epub", WRITTEN, LocatorResult(percentage=WRITTEN), observed={}
    )

    cached = grimmory_api._book_id_cache[945]["epubProgress"]["percentage"]
    assert cached == pytest.approx(24.6)
