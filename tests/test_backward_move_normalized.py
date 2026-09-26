"""0005 — judge a text client's backward move on the audio timeline.

The backward-move hold compared a client's percentage with its previous one. A
Kobo reports whole numbers, which is too coarse to answer the question in either
direction, and both failures were observed on one live book (A Court of Wings
and Ruin, Grimmory + Audiobookshelf):

  00:13  a Kobo sync into the next chapter reported 44% -> 43% while the reader
         had moved FORWARD; the hold stalled it for the full window.
  06:32  a re-sync of the same page reported 45.9% -> 45% and was held likewise.
  07:04  a stale Kobo bookmark 501 seconds of audio BEHIND reported 45% -> 45%;
         Audiobookshelf, the only other party, was set aside as BookBridge's own
         write-back, and the stale position was written to it unopposed.

The numbers below are the ones those cycles logged.
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
from src.sync_manager import SyncManager, _NORMALIZED_BACKWARD_TOLERANCE_SECONDS

ABS_ID = "69b9f32c"


def _state(current, previous_pct=0.0):
    return ServiceState(
        current=current, previous_pct=previous_pct, delta=0.0, threshold=0.01,
        is_configured=True, display=("X", "{prev:.2%}->{curr:.2%}"),
        value_formatter=lambda v: f"{v:.4%}",
    )


def _config(*, leader_ts, abs_ts, raw, previous, source="href_progression"):
    booklore = {"pct": raw, "_normalization_source": source}
    if leader_ts is not None:
        booklore["_normalized_ts"] = leader_ts
    return {
        "BookLore": _state(booklore, previous_pct=previous),
        "ABS": _state({"pct": 0.46, "ts": abs_ts}, previous_pct=0.46),
    }


class _EnvCase(unittest.TestCase):
    def setUp(self):
        observation_trail.clear()
        self._saved = {k: os.environ.get(k) for k in (
            "SYNC_TRUST_CORROBORATED_REWIND", "SYNC_REWIND_HOLD_SECONDS", "SYNC_FRESHNESS_GUARDS")}
        os.environ["SYNC_TRUST_CORROBORATED_REWIND"] = "true"
        os.environ.pop("SYNC_REWIND_HOLD_SECONDS", None)
        os.environ.pop("SYNC_FRESHNESS_GUARDS", None)
        self._writes = dict(write_tracker._recent_writes)
        write_tracker._recent_writes.clear()

    def tearDown(self):
        observation_trail.clear()
        write_tracker._recent_writes.clear()
        write_tracker._recent_writes.update(self._writes)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestTheObservedCases(_EnvCase):
    def _judge(self, **kw):
        return SyncManager._backward_move(_config(**kw), "BookLore", kw["raw"], "ABS")

    def test_a_forward_read_into_the_next_chapter_is_not_backward(self):
        """00:13 — the percentage fell a point while the reader moved on."""
        backward, how = self._judge(leader_ts=33300.13, abs_ts=32053.37, raw=0.43, previous=0.44)
        self.assertFalse(backward)
        self.assertIn("audio timeline", how)

    def test_a_resync_of_the_same_page_is_not_backward(self):
        """06:32 — 6.6s AHEAD of Audiobookshelf; the old test saw 45.9% -> 45%."""
        backward, _ = self._judge(leader_ts=34527.73, abs_ts=34521.14, raw=0.45, previous=0.459)
        self.assertFalse(backward)

    def test_a_stale_bookmark_eight_minutes_behind_is_backward(self):
        """07:04 — 45% -> 45% to the old test, 501s behind on the timeline."""
        backward, how = self._judge(leader_ts=34527.73, abs_ts=35029.03, raw=0.45, previous=0.45)
        self.assertTrue(backward)
        self.assertIn("35029.0s -> 34527.7s", how)


class TestTheTolerance(_EnvCase):
    def test_conversion_noise_is_not_a_rewind(self):
        """The text->audio round trip loses 5-7 seconds on this book."""
        backward, _ = SyncManager._backward_move(
            _config(leader_ts=34521.14, abs_ts=34527.73, raw=0.45, previous=0.45),
            "BookLore", 0.45, "ABS")
        self.assertFalse(backward)

    def test_just_past_the_tolerance_is_a_rewind(self):
        abs_ts = 34527.73
        backward, _ = SyncManager._backward_move(
            _config(leader_ts=abs_ts - _NORMALIZED_BACKWARD_TOLERANCE_SECONDS - 1,
                    abs_ts=abs_ts, raw=0.45, previous=0.45),
            "BookLore", 0.45, "ABS")
        self.assertTrue(backward)


class TestEverythingElseKeepsThePercentageTest(_EnvCase):
    """Only a high-confidence text position against a started audiobook takes the
    new path. Every other shape must behave exactly as before 0005."""

    def _raw(self, config, client="BookLore", pct=0.43, audio="ABS"):
        return SyncManager._backward_move(config, client, pct, audio)

    def test_a_low_confidence_resolution(self):
        backward, how = self._raw(_config(leader_ts=40000.0, abs_ts=30000.0, raw=0.43,
                                          previous=0.44, source="href_only"))
        self.assertTrue(backward)
        self.assertEqual(how, "44.0000% -> 43.0000%")

    def test_a_book_with_no_normalization(self):
        backward, _ = self._raw(_config(leader_ts=None, abs_ts=30000.0, raw=0.43, previous=0.44))
        self.assertTrue(backward)

    def test_an_unstarted_audiobook(self):
        backward, _ = self._raw(_config(leader_ts=40000.0, abs_ts=None, raw=0.43, previous=0.44))
        self.assertTrue(backward)

    def test_the_audio_leader_itself(self):
        config = _config(leader_ts=None, abs_ts=30000.0, raw=0.43, previous=0.44)
        config["ABS"].previous_pct = 0.50
        backward, _ = self._raw(config, client="ABS", pct=0.46)
        self.assertTrue(backward)

    def test_no_audio_client_named(self):
        backward, _ = self._raw(_config(leader_ts=40000.0, abs_ts=30000.0, raw=0.43, previous=0.44),
                                audio=None)
        self.assertTrue(backward)

    def test_nothing_to_judge(self):
        config = {"BookLore": _state({"pct": 0.43}, previous_pct=None)}
        self.assertIsNone(SyncManager._backward_move(config, "BookLore", 0.43, "ABS"))


class TestTheHoldUsesIt(_EnvCase):
    def setUp(self):
        super().setUp()
        self.manager = SyncManager.__new__(SyncManager)

    def test_the_forward_read_is_no_longer_held(self):
        observation_trail.record_observation("BookLore", ABS_ID, 0.43, source="poll")
        held = self.manager._should_hold_backward_leader(
            ABS_ID, "Wings", _config(leader_ts=33300.13, abs_ts=32053.37, raw=0.43, previous=0.44),
            "BookLore", 0.43, set(), "ABS")
        self.assertFalse(held)

    def test_the_stale_bookmark_is_held_and_says_why(self):
        observation_trail.record_observation("BookLore", ABS_ID, 0.45, source="poll")
        with self.assertLogs("src.sync_manager", level="INFO") as logs:
            held = self.manager._should_hold_backward_leader(
                ABS_ID, "Wings", _config(leader_ts=34527.73, abs_ts=35029.03, raw=0.45, previous=0.45),
                "BookLore", 0.45, set(), "ABS")
        self.assertTrue(held)
        self.assertIn("35029.0s -> 34527.7s on the audio timeline", "\n".join(logs.output))

    def test_the_hold_still_expires(self):
        """0005 changes what counts as backward, not how long a hold lasts: a
        rewind the reader meant is still honoured once the device goes quiet."""
        observation_trail.record_observation("BookLore", ABS_ID, 0.45, source="poll")
        os.environ["SYNC_REWIND_HOLD_SECONDS"] = "0"
        held = self.manager._should_hold_backward_leader(
            ABS_ID, "Wings", _config(leader_ts=34527.73, abs_ts=35029.03, raw=0.45, previous=0.45),
            "BookLore", 0.45, set(), "ABS")
        self.assertFalse(held)


class TestTheEchoExcludedPath(_EnvCase):
    """07:04 end to end through `_determine_leader`. Nothing registered a delta
    (45% both times), so it resolved as a discrepancy; Audiobookshelf was dropped
    as BookBridge's own write-back, and the lone survivor led unchecked."""

    ABS_TS = 35029.03
    DURATION = 75000.0

    def _manager(self, booklore_ts):
        manager = SyncManager.__new__(SyncManager)

        class _Client:
            def can_be_leader(self):
                return True

            def get_supported_sync_types(self):
                return {"ebook"}

        manager.sync_clients = {"BookLore": _Client(), "ABS": _Client()}
        manager._has_significant_delta = MagicMock(return_value=False)
        manager._normalize_for_cross_format_comparison = MagicMock(
            return_value={"ABS": self.ABS_TS, "BookLore": booklore_ts})
        manager._get_primary_audio_client_name = MagicMock(return_value="ABS")
        manager.sync_delta_between_clients = 0.005
        manager.cross_format_deadband_seconds = 2.0
        return manager

    def _config(self, booklore_ts):
        abs_pct = self.ABS_TS / self.DURATION
        return {
            "BookLore": _state({"pct": 0.45, "_normalized_ts": booklore_ts,
                                "_normalization_source": "href_progression"}, previous_pct=0.45),
            "ABS": _state({"pct": abs_pct, "ts": self.ABS_TS}, previous_pct=abs_pct),
        }

    def _lead(self, booklore_ts, *, echo=True, trail=True):
        if echo:
            write_tracker.record_write("ABS", ABS_ID, self.ABS_TS / self.DURATION)
        if trail:
            observation_trail.record_observation("BookLore", ABS_ID, 0.45, source="poll")
        book = SimpleNamespace(duration=self.DURATION, transcript_file="t.json",
                               sync_mode="audiobook", abs_id=ABS_ID)
        with self.assertLogs("src.sync_manager", level="DEBUG") as logs:
            result = self._manager(booklore_ts)._determine_leader(
                self._config(booklore_ts), book, ABS_ID, "Wings")
        return result, "\n".join(logs.output)

    def test_the_stale_bookmark_no_longer_leads_unchecked(self):
        (leader, _), logs = self._lead(34527.73)
        self.assertIsNone(leader)
        self.assertIn("Holding 'BookLore' backward move", logs)

    def test_a_survivor_that_moved_forward_still_leads(self):
        (leader, _), logs = self._lead(35124.12)
        self.assertEqual(leader, "BookLore")
        self.assertNotIn("Holding", logs)

    def test_without_an_echo_furthest_still_wins(self):
        """Nothing was excluded, so this is not the single-survivor case and the
        path behaves exactly as before 0005."""
        (leader, _), logs = self._lead(34527.73, echo=False)
        self.assertEqual(leader, "ABS")
        self.assertNotIn("Holding", logs)


if __name__ == "__main__":
    unittest.main()
