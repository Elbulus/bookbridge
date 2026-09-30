"""0007 — a Kobo that finishes a book is finishing it, not rewinding.

A Kobo marks a finished book by reporting 100% with its bookmark back on the
title page. Resolved as a position that is char 0, a jump from the audio's spot
to the start of the book, and the rollback guards refused it. The bridge then
wrote the listening position back to Grimmory and pulled the reader's Kobo out
of the finished state, for about five minutes, until the hold expired and the
same report led anyway.

Observed live (A Court of Wings and Ruin, 2026-09-30):
  21:39  Kobo reports 100% on the title page; normalized 0.0 s, href_only.
         "Rewind shadow [demoted]: 'BookLore' is 71416.0s behind" - ABS leads.
  21:44  The same report leads; completion reaches KOReader and ABS.
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
DURATION = 74989.0
ABS_TS = 71416.0            # 95.2353%, where the listening had reached


def _state(current, previous_pct=0.0):
    return ServiceState(
        current=current, previous_pct=previous_pct, delta=0.0, threshold=0.01,
        is_configured=True, display=("X", "{prev:.2%}->{curr:.2%}"),
        value_formatter=lambda v: f"{v:.4%}",
    )


class _Base(unittest.TestCase):
    def setUp(self):
        observation_trail.clear()
        self._env = {k: os.environ.get(k) for k in (
            "SYNC_COMPLETION_THRESHOLD", "SYNC_TRUST_CORROBORATED_REWIND",
            "SYNC_FRESHNESS_GUARDS", "SYNC_REWIND_HOLD_SECONDS")}
        for k in self._env:
            os.environ.pop(k, None)
        self._writes = dict(write_tracker._recent_writes)
        write_tracker._recent_writes.clear()
        self.manager = SyncManager.__new__(SyncManager)

    def tearDown(self):
        observation_trail.clear()
        write_tracker._recent_writes.clear()
        write_tracker._recent_writes.update(self._writes)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestWhatCountsAsAFinish(_Base):
    def test_the_kobos_hundred_percent(self):
        self.assertTrue(self.manager._is_completion_report(1.0))

    def test_the_default_threshold_is_ninety_nine(self):
        self.assertTrue(self.manager._is_completion_report(0.99))
        self.assertFalse(self.manager._is_completion_report(0.989))

    def test_the_threshold_setting_is_honoured(self):
        os.environ["SYNC_COMPLETION_THRESHOLD"] = "100"
        self.assertFalse(self.manager._is_completion_report(0.995))
        self.assertTrue(self.manager._is_completion_report(1.0))

    def test_nothing_to_judge(self):
        for value in (None, True, "abc"):
            with self.subTest(value=value):
                self.assertFalse(self.manager._is_completion_report(value))


class TestTheHoldLetsAFinishThrough(_Base):
    def _config(self, raw):
        return {
            "BookLore": _state({"pct": raw, "_normalized_ts": 0.0,
                                "_normalization_source": "cfi"}, previous_pct=0.949),
            "ABS": _state({"pct": ABS_TS / DURATION, "ts": ABS_TS}, previous_pct=ABS_TS / DURATION),
        }

    def test_a_finish_is_not_held(self):
        observation_trail.record_observation("BookLore", ABS_ID, 1.0, source="poll")
        self.assertFalse(self.manager._should_hold_backward_leader(
            ABS_ID, "Wings", self._config(1.0), "BookLore", 1.0, set(), "ABS"))

    def test_a_genuine_jump_back_is_still_held(self):
        observation_trail.record_observation("BookLore", ABS_ID, 0.5, source="poll")
        self.assertTrue(self.manager._should_hold_backward_leader(
            ABS_ID, "Wings", self._config(0.5), "BookLore", 0.5, set(), "ABS"))


class TestTonightThroughTheLeaderDecision(_Base):
    def _lead(self, raw):
        class _Client:
            def can_be_leader(self):
                return True

            def get_supported_sync_types(self):
                return {"ebook"}

        m = self.manager
        m.sync_clients = {"BookLore": _Client(), "ABS": _Client()}
        m._has_significant_delta = MagicMock(side_effect=lambda name, cfg, book: name == "BookLore")
        m._normalize_for_cross_format_comparison = MagicMock(
            return_value={"ABS": ABS_TS, "BookLore": 0.0})
        m._get_primary_audio_client_name = MagicMock(return_value="ABS")
        m.sync_delta_between_clients = 0.005
        m.cross_format_deadband_seconds = 2.0
        config = {
            "BookLore": _state({"pct": raw, "_normalized_ts": 0.0,
                                "_normalization_source": "href_only"}, previous_pct=0.949),
            "ABS": _state({"pct": ABS_TS / DURATION, "ts": ABS_TS}, previous_pct=ABS_TS / DURATION),
        }
        book = SimpleNamespace(duration=DURATION, audio_duration=None, transcript_file="t.json",
                               sync_mode="audiobook", abs_id=ABS_ID)
        with self.assertLogs("src.sync_manager", level="DEBUG") as logs:
            leader, pct = m._determine_leader(config, book, ABS_ID, "Wings")
        return leader, pct, "\n".join(logs.output)

    def test_the_finished_kobo_leads_at_once(self):
        leader, pct, logs = self._lead(1.0)
        self.assertEqual(leader, "BookLore")
        self.assertEqual(pct, 1.0)
        self.assertIn("a finish, not a rewind", logs)
        self.assertNotIn("demoted", logs)

    def test_a_real_jump_to_the_start_is_still_refused(self):
        """The same title-page position without the 100% claim: stale, demoted."""
        leader, _, logs = self._lead(0.0)
        self.assertEqual(leader, "ABS")
        self.assertIn("demoted", logs)


if __name__ == "__main__":
    unittest.main()
