"""Regression coverage for Storyteller OPDS hashes and linked-book reconciliation (#519)."""

import hashlib
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.api.storyteller_api import StorytellerAPIClient
from src.services.storyteller_library_watch import register_hashes, scan_linked
from src.utils.ebook_utils import EbookParser


class FakeResponse:
    def __init__(self, status, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}

    def close(self):
        pass

    def iter_content(self, chunk_size=1024):
        for idx in range(0, len(self.content), chunk_size):
            yield self.content[idx:idx + chunk_size]


def hash_client(content, ignore_range=False):
    client = StorytellerAPIClient()
    client.base_url = "http://storyteller.test"
    client._get_fresh_token = Mock(return_value="token")

    def get(url, *, headers, params, timeout, stream):
        assert params["format"] in {"ebook", "readaloud"}
        assert stream
        if ignore_range:
            return FakeResponse(200, content)
        start = int(headers["Range"].split("=")[1].split("-")[0])
        if start >= len(content):
            return FakeResponse(416)
        end = min(len(content), start + 1024)
        return FakeResponse(206, content[start:end],
                            {"Content-Range": f"bytes {start}-{end - 1}/{len(content)}"})

    client.session.get = Mock(side_effect=get)
    return client


@pytest.mark.parametrize("size", [500, 1580, 12345, 95000])
def test_range_hash_matches_full_epub_hash(size):
    contents = bytes(i % 251 for i in range(size))
    client = hash_client(contents)
    expected = EbookParser._compute_koreader_hash_from_bytes(None, contents)
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
        assert client.get_kosync_content_hash("a-uuid", "ebook") == expected
    assert client.session.get.call_count <= 12


def test_200_fallback_hashes_streamed_full_body():
    contents = bytes(i % 256 for i in range(350000))
    client = hash_client(contents, ignore_range=True)
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
        assert client.get_kosync_content_hash("a-uuid", "readaloud") == (
            EbookParser._compute_koreader_hash_from_bytes(None, contents))
    client.session.get.assert_called_once()


def test_filename_method_does_not_call_storyteller():
    client = hash_client(b"book")
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "filename"}):
        assert client.get_kosync_content_hash("uuid", "ebook") is None
    client.session.get.assert_not_called()


def setup_registration(artifact=True, owner=None):
    book = SimpleNamespace(
        abs_id="ebook-1", storyteller_uuid="abcd-1234",
        ebook_filename="storyteller_abcd-1234.epub",
        original_ebook_filename=None if artifact else "plain.epub",
    )
    state = {}
    db = Mock()
    db.get_json_setting.side_effect = lambda key, default=None: state.get(key, default)
    db.set_json_setting.side_effect = lambda key, value: state.__setitem__(key, dict(value))
    db.get_kosync_document.side_effect = (
        lambda value: SimpleNamespace(linked_abs_id=owner) if value == "b" * 32 and owner else None
    )
    client = Mock()
    client.is_configured.return_value = True
    client.get_kosync_content_hash.side_effect = (
        lambda uuid, fmt: {"ebook": "a" * 32, "readaloud": "b" * 32}[fmt]
    )
    details = {"readaloud": {"status": "ALIGNED", "missing": False, "fingerprint": "v1"}}
    return db, client, book, details, Mock(), state


def test_registers_plain_and_readaloud_for_artifact_only():
    for artifact, expected in ((True, 2), (False, 1)):
        db, client, book, details, adopt, _ = setup_registration(artifact=artifact)
        with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
            assert register_hashes(db, client, book, adopt, details, user_id=1)
        assert adopt.call_count == expected
        adopt.assert_any_call(book.abs_id, "a" * 32)
        if not artifact:
            client.get_kosync_content_hash.assert_called_once_with("abcd-1234", "ebook")


def test_conflicting_hash_is_never_repointed():
    db, client, book, details, adopt, _ = setup_registration(owner="other-book")
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
        assert register_hashes(db, client, book, adopt, details, user_id=3)
    adopt.assert_called_once_with("ebook-1", "a" * 32)


def test_unchanged_fingerprint_skips_rehash_and_change_retains_old_hash():
    db, client, book, details, adopt, state = setup_registration()
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
        register_hashes(db, client, book, adopt, details, user_id=1)
        client.get_kosync_content_hash.reset_mock()
        register_hashes(db, client, book, adopt, details, user_id=1)
        client.get_kosync_content_hash.assert_not_called()
        details["readaloud"]["fingerprint"] = "v2"
        client.get_kosync_content_hash.side_effect = (
            lambda uuid, fmt: {"ebook": "c" * 32, "readaloud": "d" * 32}[fmt]
        )
        register_hashes(db, client, book, adopt, details, user_id=1)
    assert ("ebook-1", "b" * 32) in [c.args for c in adopt.call_args_list]
    assert ("ebook-1", "d" * 32) in [c.args for c in adopt.call_args_list]
    assert state["STORYTELLER_519_HASHES:1"]["abcd-1234"]["fingerprint"] == "v2"


def test_reconciler_only_handles_passed_user_books():
    db, client, book, details, adopt, _ = setup_registration()
    other = SimpleNamespace(abs_id="other", storyteller_uuid="other-uuid")
    client.list_library_books.return_value = [
        {"uuid": "abcd-1234", **details},
        {"uuid": "other-uuid", **details},
    ]
    with patch.dict(os.environ, {"KOSYNC_HASH_METHOD": "content"}):
        scan_linked(db, client, [book], adopt, 17)
    client.get_kosync_content_hash.assert_any_call("abcd-1234", "ebook")
    assert all(call.args[0] == "abcd-1234" for call in client.get_kosync_content_hash.call_args_list)
    assert "STORYTELLER_519_HASHES:17" in _
