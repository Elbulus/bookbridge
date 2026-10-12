"""Local File candidates carry no metadata: their title is the filename stem, which in a
Calibre library is "Title - Author". Scoring the audiobook title against that stem, with
no candidate author, capped an exact title-and-author match near 50%.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import src.web_server as web_server
from src.services.suggestions_service import SuggestionsService

EbookResult = web_server.EbookResult


def _service() -> SuggestionsService:
    return SuggestionsService(
        database_service=MagicMock(),
        container=MagicMock(),
        manager=MagicMock(),
        get_audiobooks_conditionally=lambda: [],
        get_searchable_ebooks=lambda _q: [],
        audiobook_matches_search=lambda _ab, _q: False,
        get_abs_author=lambda _ab: '',
        logger=MagicMock(),
    )


def _local(filename, folder="Author/Title (1)"):
    return EbookResult(name=filename, path=f"/books/{folder}/{filename}", source="Local File")


def _abs_item(title, author):
    return {
        "id": "abs-1",
        "audio_source": "ABS",
        "audio_source_id": "abs-1",
        "audio_title": title,
        "audio_author": author,
        "audio_path": "/audiobooks/x/x.m4b",
    }


def _top(title, author, *candidates):
    svc = _service()
    result = svc._scan_single_audiobook(_abs_item(title, author), svc._prepare_candidate_pool(list(candidates)))
    return result["matches"][0] if result else None


def test_calibre_filename_gives_an_exact_match_full_marks():
    top = _top("The Pillars of the Earth", "Ken Follett", _local("The Pillars of the Earth - Ken Follett.epub"))
    assert top["score"] == 100.0


def test_translator_credit_does_not_lower_the_author_score():
    top = _top(
        "The Savage Detectives",
        "Roberto Bolaño, Natasha Wimmer - translator",
        _local("The Savage Detectives - Roberto Bolaño.epub"),
    )
    assert top["score"] == 100.0


def test_title_containing_the_separator_keeps_its_parts():
    pool = _service()._prepare_candidate_pool([_local("Asian Saga 1 - Shogun - James Clavell.epub")])
    assert (pool[0]["title"], pool[0]["author"]) == ("Asian Saga 1 - Shogun", "James Clavell")


def test_filename_without_separator_is_unchanged():
    pool = _service()._prepare_candidate_pool([_local("Dune.epub")])
    assert (pool[0]["title"], pool[0]["author"]) == ("Dune", "")


def test_sources_with_metadata_are_not_split():
    grimmory = EbookResult(name="x.epub", title="Star Wars - Thrawn", authors="Timothy Zahn", source="Grimmory")
    pool = _service()._prepare_candidate_pool([grimmory])
    assert (pool[0]["title"], pool[0]["author"]) == ("Star Wars - Thrawn", "Timothy Zahn")


def test_a_different_author_is_never_an_exact_match():
    top = _top("Dune", "Brian Herbert", _local("Dune - Frank Herbert.epub"))
    assert top["score"] < 100.0


def test_candidate_with_several_authors_matches_one_of_them():
    top = _top("Good Omens", "Neil Gaiman", _local("Good Omens - Terry Pratchett & Neil Gaiman.epub"))
    assert top["score"] == 100.0
