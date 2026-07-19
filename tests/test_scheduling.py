import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import scheduling
import watcher


class TestSchedulingPolicy(unittest.TestCase):

    def test_exact_horizon_boundaries(self):
        now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(scheduling.is_scheduleable(now + timedelta(seconds=300), now))
        self.assertTrue(scheduling.is_scheduleable(now + timedelta(seconds=301), now))
        self.assertTrue(scheduling.is_scheduleable(now + timedelta(seconds=21600), now))
        self.assertFalse(scheduling.is_scheduleable(now + timedelta(seconds=21601), now))

    def test_timezone_normalization(self):
        now = datetime(2026, 7, 19, 21, 0, tzinfo=timezone(timedelta(hours=9)))
        reset = datetime(2026, 7, 20, 2, 1, tzinfo=timezone(timedelta(hours=9)))
        self.assertTrue(scheduling.should_schedule(reset, now))

    def test_out_of_horizon_provider_window_is_not_dispatched_or_recorded(self):
        now = datetime.now(timezone.utc)
        state = {}
        status = watcher.LimitStatus(
            provider_windows=(
                watcher.ProviderWindow(
                    provider="claude",
                    label="Claude",
                    window="five_hour",
                    reset=now + timedelta(seconds=21601),
                ),
            ),
            source="direct",
        )
        dispatched = []
        saves = []
        logger = Mock()

        with patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "save_state", side_effect=lambda value: saves.append(value)), \
             patch.object(watcher, "dispatch_github_workflow", side_effect=lambda *args, **kwargs: dispatched.append(args) or True):
            watcher._schedule_provider_windows({}, status, logger, dry_run=False)

        self.assertEqual(dispatched, [])
        self.assertEqual(saves, [])

    def test_provider_v2_record_waits_for_horizon_then_redispatches(self):
        reset = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        window = watcher.ProviderWindow("claude", "Claude", "seven_day", reset)
        status = watcher.LimitStatus(provider_windows=(window,), source="direct")
        reset_iso = watcher._window_reset_iso(window)
        state = {
            "scheduled_resets": {
                "claude:default:seven_day": {
                    "reset_time": reset_iso,
                    "alert_format_version": "provider-layout-v2",
                }
            }
        }
        dispatched = []
        saves = []
        logger = Mock()

        clock = Mock()
        clock.now.return_value = reset - timedelta(seconds=21601)
        with patch.object(watcher, "datetime", clock), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "save_state", side_effect=lambda value: saves.append(value.copy())), \
             patch.object(watcher, "dispatch_github_workflow", side_effect=lambda *args, **kwargs: dispatched.append(args) or True):
            watcher._schedule_provider_windows({}, status, logger, dry_run=False)

        self.assertEqual(dispatched, [])
        self.assertEqual(saves, [])

        clock.now.return_value = reset - timedelta(seconds=21600)
        with patch.object(watcher, "datetime", clock), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "save_state", side_effect=lambda value: saves.append(value.copy())), \
             patch.object(watcher, "dispatch_github_workflow", side_effect=lambda *args, **kwargs: dispatched.append(args) or True):
            watcher._schedule_provider_windows({}, status, logger, dry_run=False)

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(
            state["scheduled_resets"]["claude:default:seven_day"]["alert_format_version"],
            watcher.RESET_ALERT_FORMAT_VERSION,
        )

    def test_legacy_schedule_missing_or_old_version_redispatches(self):
        reset = datetime.now(timezone.utc) + timedelta(hours=1)
        reset_iso = reset.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+09:00")
        status = watcher.LimitStatus(five_hour_reset=reset, source="cache")

        for old_version in (None, "provider-layout-v2"):
            with self.subTest(old_version=old_version):
                state = {"scheduled_reset_time": reset_iso}
                if old_version is not None:
                    state[watcher.LEGACY_SCHEDULE_FORMAT_KEY] = old_version
                dispatched = []

                with patch.object(watcher, "get_current_limit_status", return_value=status), \
                     patch.object(watcher, "load_state", return_value=state), \
                     patch.object(watcher, "save_state", side_effect=lambda value: state.update(value)), \
                     patch.object(watcher, "dispatch_github_workflow", side_effect=lambda *args, **kwargs: dispatched.append(args) or True), \
                     patch.object(watcher, "activate_claude_reset"):
                    watcher.run_once({}, Mock(), dry_run=False)

                self.assertEqual(len(dispatched), 1)
                self.assertEqual(
                    state[watcher.LEGACY_SCHEDULE_FORMAT_KEY],
                    watcher.RESET_ALERT_FORMAT_VERSION,
                )


if __name__ == "__main__":
    unittest.main()
