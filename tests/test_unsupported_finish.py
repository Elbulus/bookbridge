"""0008 — a "finished" that nothing else supports is ignored, not followed.

0007 decided when a 100% report that looks like a rewind still counts as a
finish (the reader already known to be past SYNC_COMPLETION_MIN_PRIOR, 60%).
This is the other half: a 99-100% report from a reader known to be early in
the book is an old "read" mark or a stale end-of-book bookmark, and may not
lead. The client stays a follower, so the real position is written back over
it; Hardcover and StoryGraph are not told the book is finished either.

Observed live (A Court of Silver Flames, 2026-09-22): the book had been marked
read in Grimmory years before. Re-listened from the start, the audio reached
18.28% (4:11:29). Grimmory then reported its old bookmark again, 99.2% at
epubcfi(/6/184!/4/2/62:0); furthest-wins made it the leader and the audiobook
was pushed to 99.85% and marked finished.
"""
import os
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services import observation_trail, write_tracker
from src.sync_clients.sync_client_interface import ServiceState, SyncResult
from src.sync_manager import SyncManager

ABS_ID = "ebdba9b3"
DURATION = 82534.0          # 22:55:34
ABS_TS = 15089.0            # 4:11:29, 18.28%
STALE_PCT = 0.992           # Grimmory's old end-of-book bookmark
STALE_TS = 81874.0          # 22:44:34


def _state(current, previous_pct=0.0):
    return ServiceState(
        current=current, previous_pct=previous_pct, delta=0.0, threshold=0.01,
        is_configured=True, display=("X", "{prev:.2%}->{curr:.2%}"),
        value_formatter=lambda v: f"{v:.4%}",
    )


class _Leader:
    def can_be_leader(self):
        return True

    def get_supported_sync_types(self):
        return {"ebook", "audiobook"}


class _Base(unittest.TestCase):
    def setUp(self):
        observation_trail.clear()
        self._env = {k: os.environ.get(k) for k in (
            "SYNC_COMPLETION_THRESHOLD", "SYNC_COMPLETION_MIN_PRIOR", "SYNC_TRUST_CORROBORATED_REWIND",
            "SYNC_FRESHNESS_GUARDS", "SYNC_REWIND_HOLD_SECONDS", "HARDCOVER_UPDATE_COOLDOWN_MINS")}
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


class TestWhichFinishesAreUnsupported(_Base):
    def _refused(self, grimmory_pct, grimmory_prev, abs_pct):
        config = {
            "BookLore": _state({"pct": grimmory_pct}, previous_pct=grimmory_prev),
            "ABS": _state({"pct": abs_pct}, previous_pct=abs_pct),
        }
        return self.manager._unsupported_finishes(
            config, {"BookLore": grimmory_pct, "ABS": abs_pct})

    def test_silver_flames(self):
        """Grimmory jumped from 18.42% to its old 99.2%; the audio was at 18.28%."""
        self.assertEqual(self._refused(STALE_PCT, 0.1842, 0.1828), {"BookLore"})

    def test_a_stale_finish_cannot_vouch_for_itself(self):
        """Grimmory already held the old 99.2% last time; the audio is at 18%."""
        self.assertEqual(self._refused(STALE_PCT, 0.9916, 0.1828), {"BookLore"})

    def test_the_audio_near_the_end_supports_it(self):
        self.assertEqual(self._refused(1.0, 0.30, 0.95), set())

    def test_reading_well_into_the_book_on_the_kobo_supports_it(self):
        self.assertEqual(self._refused(1.0, 0.65, 0.30), set())

    def test_the_boundary_is_sixty_percent(self):
        self.assertEqual(self._refused(1.0, 0.60, 0.10), set())
        self.assertEqual(self._refused(1.0, 0.599, 0.10), {"BookLore"})

    def test_the_boundary_is_a_setting(self):
        os.environ["SYNC_COMPLETION_MIN_PRIOR"] = "0"
        self.assertEqual(self._refused(1.0, 0.0, 0.0), set())

    def test_short_of_a_finish_is_never_refused(self):
        self.assertEqual(self._refused(0.98, 0.18, 0.18), set())

    def test_the_audiobook_marked_finished_early_is_refused_too(self):
        config = {
            "BookLore": _state({"pct": 0.20}, previous_pct=0.20),
            "ABS": _state({"pct": 1.0}, previous_pct=0.20),
        }
        self.assertEqual(self.manager._unsupported_finishes(
            config, {"BookLore": 0.20, "ABS": 1.0}), {"ABS"})

    def test_everything_finished_is_a_finished_book(self):
        config = {
            "BookLore": _state({"pct": 1.0}, previous_pct=1.0),
            "ABS": _state({"pct": 1.0}, previous_pct=1.0),
        }
        self.assertEqual(self.manager._unsupported_finishes(
            config, {"BookLore": 1.0, "ABS": 1.0}), set())

    def test_a_lone_client_has_nothing_better_to_keep(self):
        config = {"BookLore": _state({"pct": 1.0}, previous_pct=0.0)}
        self.assertEqual(self.manager._unsupported_finishes(config, {"BookLore": 1.0}), set())


class TestSilverFlamesThroughTheLeaderDecision(_Base):
    def _lead(self, grimmory_pct, grimmory_ts, grimmory_prev=0.1842, abs_ts=ABS_TS):
        m = self.manager
        m.sync_clients = {"BookLore": _Leader(), "ABS": _Leader()}
        m._has_significant_delta = MagicMock(side_effect=lambda name, cfg, book: name == "BookLore")
        m._normalize_for_cross_format_comparison = MagicMock(
            return_value={"ABS": abs_ts, "BookLore": grimmory_ts})
        m._get_primary_audio_client_name = MagicMock(return_value="ABS")
        m.sync_delta_between_clients = 0.005
        m.cross_format_deadband_seconds = 2.0
        config = {
            "BookLore": _state({"pct": grimmory_pct, "_normalized_ts": grimmory_ts,
                                "_normalization_source": "cfi"}, previous_pct=grimmory_prev),
            "ABS": _state({"pct": abs_ts / DURATION, "ts": abs_ts}, previous_pct=abs_ts / DURATION),
        }
        book = SimpleNamespace(duration=DURATION, audio_duration=None, transcript_file="t.json",
                               sync_mode="audiobook", abs_id=ABS_ID)
        with self.assertLogs("src.sync_manager", level="DEBUG") as logs:
            leader, pct = m._determine_leader(config, book, ABS_ID, "Silver")
        return leader, pct, "\n".join(logs.output)

    def test_the_old_bookmark_no_longer_leads(self):
        leader, pct, logs = self._lead(STALE_PCT, STALE_TS)
        self.assertEqual(leader, "ABS")
        self.assertAlmostEqual(pct, ABS_TS / DURATION)
        self.assertIn("Ignoring 'BookLore' at 99.2%", logs)

    def test_a_supported_finish_still_leads(self):
        """Audio at 95%: finishing on the Kobo is believed and leads."""
        leader, pct, logs = self._lead(1.0, DURATION, grimmory_prev=0.94, abs_ts=0.95 * DURATION)
        self.assertEqual(leader, "BookLore")
        self.assertEqual(pct, 1.0)
        self.assertNotIn("Ignoring 'BookLore'", logs)

    def test_an_ordinary_jump_forward_is_untouched(self):
        """A real read ahead to 40% is not a finish; it leads as before."""
        leader, pct, _ = self._lead(0.40, 0.40 * DURATION)
        self.assertEqual(leader, "BookLore")
        self.assertEqual(pct, 0.40)


class TestTrackersAreNotToldItIsFinished(_Base):
    def setUp(self):
        super().setUp()
        self.posts = []
        posts = self.posts

        class _Hardcover:
            def is_configured(self):
                return True

            def can_be_leader(self):
                return False

            def update_progress(self, book, request):
                posts.append(request.locator_result.percentage)
                return SyncResult(location=request.locator_result.percentage, success=True)

        db = MagicMock()
        db.get_state.return_value = None
        m = self.manager
        m.sync_clients = {"Hardcover": _Hardcover(), "BookLore": _Leader(), "ABS": _Leader()}
        m.database_service = db
        m._hardcover_cooldown = {}
        m._hardcover_cooldown_lock = threading.Lock()
        m._schedule_tracker_cooldown_check = MagicMock(return_value=False)
        os.environ["HARDCOVER_UPDATE_COOLDOWN_MINS"] = "60"
        self.book = SimpleNamespace(abs_id=ABS_ID)

    def _post(self, grimmory_pct, grimmory_prev, abs_pct):
        config = {
            "BookLore": _state({"pct": grimmory_pct}, previous_pct=grimmory_prev),
            "ABS": _state({"pct": abs_pct}, previous_pct=abs_pct),
        }
        self.manager._handle_hardcover_cooldown(self.book, config, 1000.0)
        return self.posts

    def test_the_old_bookmark_posts_no_finish(self):
        self.assertEqual(self._post(STALE_PCT, 0.1842, 0.1828), [])

    def test_a_supported_finish_posts_at_once(self):
        self.assertEqual(self._post(1.0, 0.94, 0.95), [1.0])


if __name__ == "__main__":
    unittest.main()
