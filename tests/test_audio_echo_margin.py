"""0006 — an audio position is BookBridge's own echo only to the second.

The own-write echo check compared every client with the cross-client sync
threshold, 0.5% of the book. On a 21-hour audiobook that is 6 min 15 s.
Audiobookshelf stores the timestamp it is sent, so a genuine echo of our write
comes back exactly, and anything looser mistakes real listening for it.

Observed live (A Court of Wings and Ruin, 2026-09-26): a Kobo sync wrote ABS at
the start of chapter 39 (40612.54 s). The reader then listened for five minutes.
Every ABS report was discarded as our own write-back, the Kobo's position led,
and ABS was written back to 40612.54 s once a minute, re-arming the marker. It
let go at 0.51%. The marker lives in memory only, which is why a restart used to
"fix" it.
"""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services import observation_trail, write_tracker
from src.sync_clients.sync_client_interface import ServiceState
from src.sync_manager import SyncManager

ABS_ID = "69b9f32c"
DURATION = 74989.0          # derived from the live log: 39190.0 s <-> 52.2610%
WRITTEN_TS = 40612.54       # start of chapter 39, where the Kobo sync put ABS


def _state(current, previous_pct=0.0):
    return ServiceState(
        current=current, previous_pct=previous_pct, delta=0.0, threshold=0.01,
        is_configured=True, display=("X", "{prev:.2%}->{curr:.2%}"),
        value_formatter=lambda v: f"{v:.4%}",
    )


def _abs_state(ts):
    return _state({"pct": ts / DURATION, "ts": ts}, previous_pct=WRITTEN_TS / DURATION)


class _Base(unittest.TestCase):
    def setUp(self):
        observation_trail.clear()
        self._env = {k: os.environ.get(k) for k in (
            "SYNC_AUDIO_ECHO_TOLERANCE_SECONDS", "SYNC_FRESHNESS_GUARDS",
            "SYNC_TRUST_CORROBORATED_REWIND", "SYNC_PERIOD_MINS")}
        for k in self._env:
            os.environ.pop(k, None)
        self._writes = dict(write_tracker._recent_writes)
        write_tracker._recent_writes.clear()
        self.manager = SyncManager.__new__(SyncManager)
        self.book = SimpleNamespace(duration=DURATION, audio_duration=None,
                                    transcript_file="t.json", sync_mode="audiobook",
                                    abs_id=ABS_ID)

    def tearDown(self):
        observation_trail.clear()
        write_tracker._recent_writes.clear()
        write_tracker._recent_writes.update(self._writes)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _echo(self, observed_ts):
        write_tracker.record_write("ABS", ABS_ID, WRITTEN_TS / DURATION)
        margin = self.manager._audio_echo_margin(_abs_state(observed_ts), self.book, 0.005)
        return self.manager._peer_position_is_own_writeback(
            ABS_ID, "ABS", observed_ts / DURATION, margin)


class TestTheMargin(_Base):
    def test_ten_seconds_on_the_audio_clients_own_scale(self):
        margin = self.manager._audio_echo_margin(_abs_state(41000.0), self.book, 0.005)
        self.assertAlmostEqual(margin * DURATION, 10.0, places=6)

    def test_falls_back_to_the_books_duration(self):
        state = _state({"pct": None, "ts": None})
        margin = self.manager._audio_echo_margin(state, self.book, 0.005)
        self.assertAlmostEqual(margin * DURATION, 10.0, places=6)

    def test_with_no_duration_at_all_nothing_changes(self):
        book = SimpleNamespace(duration=None, audio_duration=None)
        state = _state({"pct": 0.0, "ts": 0.0})
        self.assertEqual(self.manager._audio_echo_margin(state, book, 0.005), 0.005)

    def test_never_wider_than_the_shared_margin(self):
        """A very short book cannot turn ten seconds into more than 0.5%."""
        short = SimpleNamespace(duration=600.0)
        state = _state({"pct": 0.5, "ts": 300.0})
        self.assertEqual(self.manager._audio_echo_margin(state, short, 0.005), 0.005)

    def test_the_tolerance_is_a_setting(self):
        os.environ["SYNC_AUDIO_ECHO_TOLERANCE_SECONDS"] = "30"
        margin = self.manager._audio_echo_margin(_abs_state(41000.0), self.book, 0.005)
        self.assertAlmostEqual(margin * DURATION, 30.0, places=6)

    def test_a_bad_setting_keeps_the_default(self):
        for raw in ("", "abc", "0", "-5"):
            with self.subTest(value=raw):
                os.environ["SYNC_AUDIO_ECHO_TOLERANCE_SECONDS"] = raw
                self.assertEqual(SyncManager._audio_echo_tolerance_seconds(), 10.0)


class TestTonightsNumbers(_Base):
    def test_the_exact_write_coming_back_is_still_an_echo(self):
        self.assertTrue(self._echo(WRITTEN_TS))

    def test_a_few_seconds_of_rounding_is_still_an_echo(self):
        self.assertTrue(self._echo(WRITTEN_TS + 5.0))

    def test_one_minute_of_listening_is_not_an_echo(self):
        self.assertFalse(self._echo(WRITTEN_TS + 60.0))

    def test_five_minutes_of_listening_is_not_an_echo(self):
        """The session that was swallowed whole."""
        self.assertFalse(self._echo(WRITTEN_TS + 308.0))

    def test_a_prologue_style_resume_rewind_is_movement_too(self):
        """20 s back is the reader's player, not our write; the backward-move
        hold decides what to do with it."""
        self.assertFalse(self._echo(WRITTEN_TS - 20.0))


class TestTheLeaderDecision(_Base):
    """Tonight end to end through `_determine_leader`."""

    def _lead(self, abs_ts):
        class _Client:
            def can_be_leader(self):
                return True

            def get_supported_sync_types(self):
                return {"ebook"}

        m = self.manager
        m.sync_clients = {"BookLore": _Client(), "ABS": _Client()}
        m._has_significant_delta = MagicMock(side_effect=lambda name, cfg, book: name == "ABS")
        m._normalize_for_cross_format_comparison = MagicMock(
            return_value={"ABS": abs_ts, "BookLore": WRITTEN_TS})
        m._get_primary_audio_client_name = MagicMock(return_value="ABS")
        m.sync_delta_between_clients = 0.005
        m.cross_format_deadband_seconds = 2.0

        write_tracker.record_write("ABS", ABS_ID, WRITTEN_TS / DURATION)
        config = {
            "BookLore": _state({"pct": 0.53, "_normalized_ts": WRITTEN_TS,
                                "_normalization_source": "href_progression"}, previous_pct=0.53),
            "ABS": _abs_state(abs_ts),
        }
        with self.assertLogs("src.sync_manager", level="DEBUG") as logs:
            leader, _ = m._determine_leader(config, self.book, ABS_ID, "Wings")
        return leader, "\n".join(logs.output)

    def test_listening_leads_instead_of_being_dragged_back(self):
        leader, logs = self._lead(WRITTEN_TS + 60.0)
        self.assertEqual(leader, "ABS")
        self.assertNotIn("Ignoring 'ABS' delta", logs)

    def test_the_genuine_echo_still_cannot_lead(self):
        """#416 must still hold: our own write coming back is not a move."""
        leader, logs = self._lead(WRITTEN_TS)
        self.assertNotEqual(leader, "ABS")
        self.assertIn("Ignoring 'ABS' delta", logs)


if __name__ == "__main__":
    unittest.main()
