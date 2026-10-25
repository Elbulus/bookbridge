"""Filename-mode hashes of device copies named after any of a book's authors."""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api import kosync_server

LIBRARY_NAME = "Roadside Picnic - Arkady Strugatsky.epub"
AUTHORS = ("Arkady Strugatsky", "Boris Strugatsky")


def _md5(name):
    return hashlib.md5(name.encode("utf-8")).hexdigest()


@pytest.fixture
def library(monkeypatch):
    book = SimpleNamespace(abs_id="abs-rp", abs_title="Roadside Picnic",
                           original_ebook_filename=LIBRARY_NAME, ebook_filename=None)
    db = MagicMock()
    db.get_books_by_status.return_value = [book]
    db.get_book.side_effect = lambda abs_id: book if abs_id == book.abs_id else None
    monkeypatch.setattr(kosync_server, "_database_service", db)
    kosync_server._invalidate_filename_variant_index()
    kosync_server._epub_author_cache.clear()
    yield book
    kosync_server._invalidate_filename_variant_index()
    kosync_server._epub_author_cache.clear()


def test_second_author_variant_matches():
    _exact, reordered = kosync_server._filename_variant_hashes(LIBRARY_NAME, AUTHORS)

    assert _md5("Boris Strugatsky - Roadside Picnic.epub") in reordered
    assert _md5("Arkady Strugatsky, Boris Strugatsky - Roadside Picnic.epub") in reordered


def test_without_authors_is_unchanged():
    assert kosync_server._filename_variant_hashes(LIBRARY_NAME, ()) == kosync_server._filename_variant_hashes(LIBRARY_NAME)
    _exact, reordered = kosync_server._filename_variant_hashes(LIBRARY_NAME)
    assert _md5("Boris Strugatsky - Roadside Picnic.epub") not in reordered


def test_resolves_coauthor_copy_to_the_book(library):
    with patch.object(kosync_server, "_epub_authors_for", return_value=AUTHORS):
        book = kosync_server._resolve_book_by_filename_variants(
            _md5("Boris Strugatsky - Roadside Picnic.epub"), user_id=1,
        )

    assert book is library


def test_unreadable_epub_falls_back(library, monkeypatch):
    container = MagicMock()
    container.ebook_parser.return_value.get_book_authors.side_effect = OSError("unreadable")
    monkeypatch.setattr(kosync_server, "_container", container)

    assert kosync_server._epub_authors_for(LIBRARY_NAME) == ()
    book = kosync_server._resolve_book_by_filename_variants(
        _md5("Arkady Strugatsky - Roadside Picnic.epub"), user_id=1,
    )
    assert book is library
