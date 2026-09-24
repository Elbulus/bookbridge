"""A Kobo sync clears Grimmory's CFI, leaving only the chapter href.

`_resolve_href_to_char_offset` can take a bare href no further than the START of
that chapter file ('href_only'), so a device three quarters of the way through a
chapter lands the audiobook at its opening line — the reported symptom being
"read to the end of chapter 16, ABS synced to the end of 15".

Grimmory reports the within-chapter position all along, as
contentSourceProgressPercent. Captured as `content_source_pct` and, until now,
read by nothing. Passed through as `chapter_progress` it feeds the resolver's
existing 'href_progression' branch — the same one Storyteller has always used —
which resolves the real position AND counts as high-confidence, so a corroborated
device rewind is trusted instead of held.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.booklore_client import BookloreClient
from src.db.models import Book
from src.sync_clients.booklore_sync_client import BookloreSyncClient


def epub_book():
    return Book(
        abs_id="book-1",
        abs_title="A Court of Wings and Ruin",
        ebook_filename="wings.epub",
        original_ebook_filename="wings.epub",
        ebook_source="Grimmory",
        ebook_source_id="897",
        status="active",
    )


def sync_client_reading(rich):
    """A Grimmory API double whose rich read returns `rich`."""
    api = MagicMock(spec=BookloreClient)
    api.get_progress_rich_by_book_id = MagicMock(return_value=rich)
    api.is_configured = MagicMock(return_value=True)
    return BookloreSyncClient(api, MagicMock())


# A live Kobo sync, taken from a real install: CFI cleared, href kept,
# within-chapter position reported separately.
KOBO_SYNCED = {
    "pct": 0.24,
    "percentage_present": True,
    "cfi": None,
    "href": "OEBPS/xhtml/chapter17.xhtml",
    "book_type": "EPUB",
    "last_read_time": "2026-09-24T08:28:17Z",
    "status": "READING",
    "content_source_pct": 21.0,
}


class TestChapterProgressConversion:
    def test_grimmory_percent_becomes_a_fraction(self):
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": 21.0}
        ) == pytest.approx(0.21)

    def test_start_of_chapter_is_a_real_value_not_absence(self):
        # 0.0 is falsy; the caller must distinguish it from None or a device at
        # the top of a chapter silently degrades back to href_only.
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": 0.0}
        ) == 0.0

    def test_end_of_chapter(self):
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": 100.0}
        ) == pytest.approx(1.0)

    def test_absent_field(self):
        assert BookloreSyncClient._chapter_progress_from_rich({}) is None
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": None}
        ) is None

    def test_out_of_range_is_refused(self):
        for bad in (-1.0, 100.1, 1000.0):
            assert BookloreSyncClient._chapter_progress_from_rich(
                {"content_source_pct": bad}
            ) is None, bad

    def test_nan_and_infinities_are_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            assert BookloreSyncClient._chapter_progress_from_rich(
                {"content_source_pct": bad}
            ) is None

    def test_non_numeric_is_refused(self):
        for bad in ("21%", object(), [21]):
            assert BookloreSyncClient._chapter_progress_from_rich(
                {"content_source_pct": bad}
            ) is None

    def test_bool_is_refused(self):
        # True would otherwise scale to 1% rather than being rejected.
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": True}
        ) is None

    def test_numeric_string_is_accepted(self):
        assert BookloreSyncClient._chapter_progress_from_rich(
            {"content_source_pct": "21.0"}
        ) == pytest.approx(0.21)


class TestServiceStateCarriesIt:
    def test_kobo_synced_state_carries_chapter_progress(self):
        state = sync_client_reading(KOBO_SYNCED).get_service_state(epub_book(), None)

        assert state is not None
        assert state.current["chapter_progress"] == pytest.approx(0.21)
        # The href is what the resolver needs alongside it.
        assert state.current["href"] == "OEBPS/xhtml/chapter17.xhtml"
        assert state.current["cfi"] is None

    def test_absent_field_leaves_the_key_off(self):
        rich = dict(KOBO_SYNCED, content_source_pct=None)
        state = sync_client_reading(rich).get_service_state(epub_book(), None)

        assert state is not None
        assert "chapter_progress" not in state.current

    def test_start_of_chapter_still_sets_the_key(self):
        rich = dict(KOBO_SYNCED, content_source_pct=0.0)
        state = sync_client_reading(rich).get_service_state(epub_book(), None)

        assert state.current["chapter_progress"] == 0.0


class TestResolverBranch:
    """The payoff: which branch `_resolve_href_to_char_offset` takes."""

    def _manager(self, spine):
        from src.sync_manager import SyncManager

        mgr = SyncManager.__new__(SyncManager)
        parser = MagicMock()
        parser.resolve_book_path = MagicMock(return_value="/books/wings.epub")
        parser.extract_text_and_map = MagicMock(return_value=("x" * 400000, spine))
        mgr.ebook_parser = parser
        return mgr

    SPINE = [{"href": "OEBPS/xhtml/chapter17.xhtml", "start": 270000, "end": 290000}]

    def test_bare_href_lands_on_the_chapter_start(self):
        offset, source = self._manager(self.SPINE)._resolve_href_to_char_offset(
            "wings.epub", "OEBPS/xhtml/chapter17.xhtml", None
        )
        assert source == "href_only"
        assert offset == 270000

    def test_chapter_progress_lands_inside_the_chapter(self):
        offset, source = self._manager(self.SPINE)._resolve_href_to_char_offset(
            "wings.epub", "OEBPS/xhtml/chapter17.xhtml", 0.21
        )
        assert source == "href_progression"
        assert offset == 270000 + int(20000 * 0.21)

    def test_the_progression_source_is_trusted_by_the_rewind_guard(self):
        from src.sync_manager import _HIGH_CONFIDENCE_NORMALIZATION_SOURCES

        assert "href_progression" in _HIGH_CONFIDENCE_NORMALIZATION_SOURCES
        assert "href_only" not in _HIGH_CONFIDENCE_NORMALIZATION_SOURCES


if __name__ == "__main__":
    import unittest
    unittest.main()
