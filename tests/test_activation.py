import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import activation
import watcher
from atomic_json import read_json, write_json


UTC = timezone.utc


class FakeChild:
    def __init__(self, returncode=0, communicate_error=None, wait_errors=()):
        self.returncode = returncode
        self.communicate_error = communicate_error
        self.wait_errors = list(wait_errors)
        self.communicate_calls = []
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if self.communicate_error is not None:
            error = self.communicate_error
            self.communicate_error = None
            raise error
        return None, None

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_errors:
            error = self.wait_errors.pop(0)
            raise error
        return self.returncode


class TestAtomicJson(unittest.TestCase):

    def test_write_is_readable_private_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_json(path, {"keep": True})

            self.assertEqual(read_json(path), {"keep": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])

    def test_corrupt_whole_watcher_state_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{broken", encoding="utf-8")
            logger = Mock()
            save_state = Mock()
            popen = Mock()
            future = datetime.now(UTC) + timedelta(hours=1)

            with unittest.mock.patch.object(watcher, "STATE_FILE", state_path):
                state = watcher.load_state()
                self.assertTrue(state[activation.STATE_CORRUPTION_SENTINEL])

                activation.activate_claude_reset(
                    {},
                    future,
                    logger,
                    state,
                    save_state,
                    popen_factory=popen,
                )
                watcher.save_state(state)
                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertTrue(persisted[activation.STATE_CORRUPTION_SENTINEL])

                state_path.write_text("{}\n", encoding="utf-8")
                self.assertEqual(watcher.load_state(), {})

            save_state.assert_not_called()
            popen.assert_not_called()
            logger.warning.assert_called()


class TestActivationState(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.policy_path = self.root / "activation-policy.json"
        self.cli_path = self.root / "claude"
        self.cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.cli_path.chmod(0o700)
        self.now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        self.logger = logging.getLogger("test.activation")
        self.saved = []

    def tearDown(self):
        self.tmp.cleanup()

    def _policy(self, enabled=True, enabled_at=None):
        if enabled_at is None:
            enabled_at = self.now - timedelta(hours=1)
        self.policy_path.write_text(json.dumps({
            "version": 1,
            "enabled": enabled,
            "enabled_at": enabled_at.isoformat().replace("+00:00", "Z"),
        }), encoding="utf-8")

    def _cfg(self):
        return {
            "CLAUDE_CLI_PATH": str(self.cli_path),
            "CLAUDE_ACTIVATION_PROMPT": ".",
            "CLAUDE_ACTIVATION_TIMEOUT_SECONDS": "120",
        }

    def _save(self, state):
        self.saved.append(json.loads(json.dumps(state)))

    def _run(self, state, reset_at=None, **kwargs):
        activation.activate_claude_reset(
            self._cfg(),
            reset_at,
            self.logger,
            state,
            self._save,
            now=kwargs.pop("now", self.now),
            policy_path=self.policy_path,
            **kwargs,
        )

    def test_missing_and_corrupt_policy_are_disabled(self):
        due = self.now - timedelta(seconds=1)
        state = {}
        popen = Mock()
        self._run(state, due, popen_factory=popen)
        self.assertFalse(popen.called)
        self.assertEqual(state, {})

        self.policy_path.write_text("{broken", encoding="utf-8")
        self._run(state, due, popen_factory=popen)
        self.assertFalse(popen.called)

    def test_enabled_at_must_precede_reset(self):
        reset = self.now + timedelta(hours=1)
        self._policy(enabled_at=reset + timedelta(minutes=1))
        state = {}
        popen = Mock()
        self._run(state, reset, popen_factory=popen)
        self._run(state, None, now=reset + timedelta(seconds=1), popen_factory=popen)

        self.assertFalse(popen.called)
        record = state["claude_activation"]["records"][activation.canonical_reset_key(reset)]
        self.assertEqual(record["status"], "final_failed")
        self.assertEqual(record["error_kind"], "policy_stale")

    def test_future_reset_is_armed_and_offline_run_catches_up_once(self):
        self._policy()
        future = self.now + timedelta(hours=1)
        state = {}
        calls = []

        def popen(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeChild(0)

        self._run(state, future, popen_factory=popen)
        self.assertEqual(calls, [])
        self.assertIsNotNone(state["claude_activation"]["pending"])

        self._run(state, None, now=future + timedelta(seconds=1), popen_factory=popen)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ([str(self.cli_path), "-p", "."],))
        self.assertFalse(calls[0][1]["shell"])
        self.assertIs(calls[0][1]["stdin"], subprocess.DEVNULL)
        self.assertIs(calls[0][1]["stdout"], subprocess.DEVNULL)
        self.assertIs(calls[0][1]["stderr"], subprocess.DEVNULL)
        self.assertIsNone(state["claude_activation"]["pending"])
        self.assertEqual(
            state["claude_activation"]["records"][activation.canonical_reset_key(future)]["status"],
            "succeeded",
        )

        self._run(state, future, now=future + timedelta(seconds=2), popen_factory=popen)
        self.assertEqual(len(calls), 1)

    def test_reset_key_normalization_and_high_watermark(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        equivalent = reset.astimezone(timezone(timedelta(hours=9))) + timedelta(microseconds=999999)
        self.assertEqual(activation.canonical_reset_key(reset), activation.canonical_reset_key(equivalent))

        state = {
            "claude_activation": {
                "version": 1,
                "pending": None,
                "records": {},
                "high_watermark_reset_epoch": int(reset.timestamp()),
            }
        }
        popen = Mock()
        self._run(state, equivalent, popen_factory=popen)
        self.assertIsNone(state["claude_activation"]["pending"])
        self.assertFalse(popen.called)

    def test_terminal_records_are_retained_by_newest_reset_epoch(self):
        records = {}
        for offset in range(40):
            reset = self.now + timedelta(seconds=offset)
            key = activation.canonical_reset_key(reset)
            records[key] = {
                "reset_key": key,
                "status": "succeeded",
                "attempts": 1,
                "started_at": activation.canonical_reset_at(reset),
                "finished_at": activation.canonical_reset_at(reset),
                "error_kind": None,
            }
        namespace, valid = activation.load_activation_namespace({
            "claude_activation": {
                "version": 1,
                "pending": None,
                "records": records,
                "high_watermark_reset_epoch": None,
            }
        })

        self.assertTrue(valid)
        self.assertEqual(len(namespace["records"]), 32)
        self.assertIn(activation.canonical_reset_key(self.now + timedelta(seconds=39)), namespace["records"])
        self.assertNotIn(activation.canonical_reset_key(self.now), namespace["records"])

    def test_due_pending_is_resolved_before_newer_reset_is_armed(self):
        self._policy()
        old_reset = self.now + timedelta(seconds=1)
        new_reset = self.now + timedelta(hours=1)
        state = {}
        self._run(state, old_reset, popen_factory=Mock())
        pending = state["claude_activation"]["pending"]
        self._run(
            state,
            new_reset,
            now=old_reset + timedelta(seconds=1),
            popen_factory=lambda *a, **k: FakeChild(0),
        )

        records = state["claude_activation"]["records"]
        self.assertEqual(records[pending["reset_key"]]["status"], "succeeded")
        self.assertEqual(state["claude_activation"]["pending"]["reset_key"], activation.canonical_reset_key(new_reset))

    def test_nonzero_retries_three_total_attempts(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        children = [FakeChild(1), FakeChild(2), FakeChild(3)]
        sleeps = []
        state = {}
        self._run(state, reset, popen_factory=Mock())
        self._run(
            state,
            None,
            now=reset + timedelta(seconds=1),
            popen_factory=lambda *a, **k: children.pop(0),
            sleep_fn=sleeps.append,
        )

        record = state["claude_activation"]["records"][activation.canonical_reset_key(reset)]
        self.assertEqual(record["status"], "final_failed")
        self.assertEqual(record["attempts"], 3)
        self.assertEqual(sleeps, [5, 10])

    def test_spawn_error_retries_then_succeeds(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        children = [OSError("not found"), FakeChild(0)]
        sleeps = []
        state = {}
        self._run(state, reset, popen_factory=Mock())

        def popen(*args, **kwargs):
            child = children.pop(0)
            if isinstance(child, OSError):
                raise child
            return child

        self._run(
            state,
            None,
            now=reset + timedelta(seconds=1),
            popen_factory=popen,
            sleep_fn=sleeps.append,
        )

        record = state["claude_activation"]["records"][activation.canonical_reset_key(reset)]
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(sleeps, [5])

    def test_timeout_cleans_only_returned_child(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        child = FakeChild(
            1,
            communicate_error=subprocess.TimeoutExpired([str(self.cli_path)], 1),
            wait_errors=(subprocess.TimeoutExpired([str(self.cli_path)], 5),),
        )
        children = [child, FakeChild(0), FakeChild(0)]
        state = {}
        self._run(state, reset, popen_factory=Mock())
        self._run(
            state,
            None,
            now=reset + timedelta(seconds=1),
            popen_factory=lambda *a, **k: children.pop(0),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(child.terminate_calls, 1)
        self.assertEqual(child.kill_calls, 1)
        self.assertEqual(child.wait_calls, [5, None])

    def test_restart_started_is_unknown_without_spawn(self):
        self._policy()
        reset = self.now - timedelta(seconds=1)
        key = activation.canonical_reset_key(reset)
        state = {
            "claude_activation": {
                "version": 1,
                "pending": {
                    "reset_key": key,
                    "reset_at": activation.canonical_reset_at(reset),
                    "armed_at": activation.canonical_reset_at(reset),
                    "status": "started",
                    "attempts": 1,
                    "started_at": activation.canonical_reset_at(reset),
                },
                "records": {},
                "high_watermark_reset_epoch": None,
            }
        }
        popen = Mock()
        self._run(state, None, popen_factory=popen)

        self.assertFalse(popen.called)
        self.assertEqual(state["claude_activation"]["records"][key]["status"], "unknown")

    def test_parent_termination_commits_unknown_and_uses_child_methods_only(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        child = FakeChild(0)

        def communicate(timeout=None):
            activation.handle_parent_termination()
            return None, None

        child.communicate = communicate
        state = {}
        self._run(state, reset, popen_factory=Mock())
        self._run(state, None, now=reset + timedelta(seconds=1), popen_factory=lambda *a, **k: child)

        record = state["claude_activation"]["records"][activation.canonical_reset_key(reset)]
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(child.terminate_calls, 1)

    def test_dry_run_does_not_mutate_or_spawn(self):
        self._policy()
        future = self.now + timedelta(hours=1)
        state = {"keep": True}
        original = json.loads(json.dumps(state))
        popen = Mock()
        self._run(state, future, dry_run=True, popen_factory=popen)

        self.assertEqual(state, original)
        self.assertFalse(popen.called)

    def test_invalid_cli_is_terminal_configuration_error_without_spawn(self):
        self._policy()
        reset = self.now + timedelta(hours=1)
        state = {}
        self._run(state, reset, popen_factory=Mock())
        activation.activate_claude_reset(
            {"CLAUDE_CLI_PATH": "relative/claude"},
            None,
            self.logger,
            state,
            self._save,
            now=reset + timedelta(seconds=1),
            policy_path=self.policy_path,
            popen_factory=Mock(),
        )

        record = state["claude_activation"]["records"][activation.canonical_reset_key(reset)]
        self.assertEqual(record["status"], "final_failed")
        self.assertEqual(record["error_kind"], "configuration_error")
        self.assertEqual(record["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
