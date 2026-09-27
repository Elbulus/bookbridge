"""Regression for issue #455: ABS audio must be local before CTC runs."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

from src.api.api_clients import ABSClient
from src.services.audio_source_adapters import ABSAudioSourceAdapter
from src.sync_manager import SyncManager


def test_abs_ctc_preflight_downloads_and_reuses_all_tracks(tmp_path, monkeypatch):
    monkeypatch.setenv("CTC_ENABLED", "true")
    client = MagicMock()
    client.get_audio_files.return_value = [
        {"stream_url": "http://abs/track-1?token=secret", "ext": "mp3"},
        {"stream_url": "http://abs/track-2?token=secret", "ext": "m4b"},
    ]

    def download(_url, destination):
        Path(destination).write_bytes(b"audio" * 300)
        return True

    client.download_file.side_effect = download
    adapter = ABSAudioSourceAdapter(client, tmp_path)

    paths = SyncManager._ctc_local_audio_paths(None, adapter, "item-1", "abs-1")
    assert paths == [
        str(tmp_path / "audio_cache/abs-1/source_tracks/track_000.mp3"),
        str(tmp_path / "audio_cache/abs-1/source_tracks/track_001.m4b"),
    ]
    assert all(Path(path).read_bytes() == b"audio" * 300 for path in paths)
    assert client.download_file.call_count == 2

    assert SyncManager._ctc_local_audio_paths(None, adapter, "item-1", "abs-1") == paths
    assert client.download_file.call_count == 2


def test_abs_ctc_preflight_falls_back_to_stream_on_failed_download(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("CTC_ENABLED", "true")
    client = MagicMock()
    client.get_audio_files.return_value = [
        {"stream_url": "http://abs/track-1", "ext": "mp3"},
    ]
    client.download_file.return_value = False
    adapter = ABSAudioSourceAdapter(client, tmp_path)

    assert SyncManager._ctc_local_audio_paths(None, adapter, "item-1", "abs-1") is None
    assert "ABS track download failed for item_id=item-1 track_index=0" in caplog.text
    assert client.get_audio_files.return_value[0]["stream_url"] == "http://abs/track-1"
    assert not (tmp_path / "audio_cache/abs-1/source_tracks/track_000.mp3").exists()


def test_abs_audio_stays_remote_when_ctc_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CTC_ENABLED", "false")
    client = MagicMock()
    client.get_audio_files.return_value = [{"stream_url": "http://abs/track", "ext": "mp3"}]
    files = ABSAudioSourceAdapter(client, tmp_path).get_audio_files("item-1", bridge_key="abs-1")
    assert files == [{"stream_url": "http://abs/track", "ext": "mp3"}]
    client.download_file.assert_not_called()


def test_abs_track_download_log_hides_stream_token(tmp_path, caplog):
    client = ABSClient(credentials={"ABS_KEY": "secret"})
    response = MagicMock()
    response.headers = {"Content-Length": "1500"}
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"audio" * 300]
    client.session = MagicMock()
    client.session.get.return_value = response

    with caplog.at_level(logging.INFO, logger="src.api.api_clients"):
        assert client.download_file("http://abs/track?token=secret", tmp_path / "track.mp3")
    assert "http://abs/track" in caplog.text
    assert "secret" not in caplog.text

    caplog.clear()
    client.session.get.side_effect = RuntimeError("GET http://abs/track?token=secret failed")
    with caplog.at_level(logging.ERROR, logger="src.api.api_clients"):
        assert not client.download_file("http://abs/track?token=secret", tmp_path / "failed.mp3")
    assert "secret" not in caplog.text
