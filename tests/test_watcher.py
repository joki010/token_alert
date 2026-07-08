import os
import sys
import json
import base64
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import watcher


class TestPidLock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pid")
        self.tmp.close()
        self.pid_path = Path(self.tmp.name)
        # 빈 파일로 초기화
        self.pid_path.unlink()

    def tearDown(self):
        self.pid_path.unlink(missing_ok=True)

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def test_acquire_creates_pid_file(self):
        """잠금 획득 시 PID 파일이 생성되어야 한다."""
        result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertTrue(result)
        self.assertTrue(self.pid_path.exists())
        self.assertEqual(self.pid_path.read_text().strip(), str(os.getpid()))

    def test_acquire_fails_if_process_alive(self):
        """현재 프로세스 PID를 가진 PID 파일이 존재하면 False를 반환해야 한다."""
        self.pid_path.write_text(str(os.getpid()))
        result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertFalse(result)

    def test_acquire_succeeds_if_process_dead(self):
        """죽은 프로세스의 PID 파일이 있으면 덮어쓰고 True를 반환해야 한다."""
        self.pid_path.write_text("99999999")  # 존재하지 않는 PID
        with patch("os.kill", side_effect=ProcessLookupError):
            result = watcher.acquire_pid_lock(self.pid_path, self._make_logger())
        self.assertTrue(result)
        self.assertEqual(self.pid_path.read_text().strip(), str(os.getpid()))

    def test_release_removes_pid_file(self):
        """release_pid_lock 호출 시 PID 파일이 삭제되어야 한다."""
        self.pid_path.write_text(str(os.getpid()))
        watcher.release_pid_lock(self.pid_path)
        self.assertFalse(self.pid_path.exists())

    def test_release_is_idempotent(self):
        """PID 파일이 없어도 release_pid_lock 은 예외 없이 실행되어야 한다."""
        watcher.release_pid_lock(self.pid_path)  # 파일 없음, 예외 없어야 함


class TestCancelWorkflow(unittest.TestCase):

    def _cfg(self):
        return {
            "GITHUB_TOKEN": "fake-token",
            "GITHUB_OWNER": "testowner",
            "GITHUB_REPO": "token_alert",
        }

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def _make_response(self, body: dict, status: int = 200):
        import io
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_cancels_all_in_progress_runs(self):
        """진행 중인 워크플로우 실행이 2개면 취소 요청을 2번 보내야 한다."""
        # _get_pending_runs은 in_progress + queued 두 번 조회함
        list_resp = self._make_response({"workflow_runs": [{"id": 111}, {"id": 222}]})
        list_empty = self._make_response({"workflow_runs": []})
        cancel_resp = self._make_response({}, status=202)

        with patch("urllib.request.urlopen", side_effect=[list_resp, list_empty, cancel_resp, cancel_resp]) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        # 목록 조회 2번(in_progress, queued) + 취소 2번
        self.assertEqual(mock_open.call_count, 4)
        cancel_urls = [
            mock_open.call_args_list[2][0][0].full_url,
            mock_open.call_args_list[3][0][0].full_url,
        ]
        self.assertIn("runs/111/cancel", cancel_urls[0])
        self.assertIn("runs/222/cancel", cancel_urls[1])

    def test_skips_cancel_when_no_runs(self):
        """진행 중인 워크플로우가 없으면 취소 요청을 보내지 않아야 한다."""
        list_resp = self._make_response({"workflow_runs": []})

        with patch("urllib.request.urlopen", return_value=list_resp) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        # in_progress + queued 목록 조회 2번, 취소 없음
        self.assertEqual(mock_open.call_count, 2)

    def test_skips_cancel_when_no_credentials(self):
        """GITHUB_TOKEN 또는 GITHUB_OWNER 가 없으면 API 호출 없이 종료해야 한다."""
        with patch("urllib.request.urlopen") as mock_open:
            watcher.cancel_previous_workflow_runs({}, self._make_logger())

        mock_open.assert_not_called()

    def test_cancel_continues_on_http_error(self):
        """개별 취소 실패(HTTPError)가 나머지 취소를 막지 않아야 한다."""
        import urllib.error
        list_resp = self._make_response({"workflow_runs": [{"id": 111}, {"id": 222}]})
        list_empty = self._make_response({"workflow_runs": []})
        cancel_resp = self._make_response({}, status=202)
        http_err = urllib.error.HTTPError(url="", code=409, msg="", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=[list_resp, list_empty, http_err, cancel_resp]) as mock_open:
            watcher.cancel_previous_workflow_runs(self._cfg(), self._make_logger())

        # 목록 2번 + 취소 시도 2번(1번 실패해도 계속)
        self.assertEqual(mock_open.call_count, 4)

    def test_dispatch_proceeds_when_pending_run_exists(self):
        """pending run이 있어도 취소 후 dispatch를 진행해야 한다."""
        from datetime import datetime, timezone, timedelta

        cfg = self._cfg()

        pending_run = {"id": 999}
        KST = timezone(timedelta(hours=9))
        reset_time = datetime(2026, 6, 24, 15, 30, 0, tzinfo=KST)

        dispatch_resp = MagicMock()
        dispatch_resp.status = 204
        dispatch_resp.__enter__ = lambda s: s
        dispatch_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(watcher, "_get_pending_runs", return_value=[pending_run]), \
             patch("urllib.request.urlopen", return_value=dispatch_resp):
            result = watcher.dispatch_github_workflow(cfg, reset_time, self._make_logger(), dry_run=False)

        # reset_time 파싱 못해도 dispatch는 진행
        self.assertTrue(result)


class TestTelegramBotCommands(unittest.TestCase):

    def _cfg(self):
        return {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_CHAT_ID": "111222333",
        }

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def _make_update(self, text: str, chat_id: str = "111222333", update_id: int = 1) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "text": text,
                "chat": {"id": int(chat_id)},
            },
        }

    def test_status_with_future_reset_time(self):
        """state에 미래 예약이 있어도 실제 남은 시간처럼 응답하지 않아야 한다."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1, minutes=23)).astimezone(
            timezone(timedelta(hours=9))
        ).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        state = {"scheduled_reset_time": future}
        sent = []

        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status claude"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])
        self.assertNotIn("남았습니다", sent[0])

    def test_status_usage_file_takes_priority_over_state(self):
        """usage 파일 값이 있으면 state 파일보다 우선하여 응답해야 한다."""
        usage_reset = datetime.now(timezone.utc) + timedelta(hours=3)
        load_state_calls = []

        def fake_load_state():
            load_state_calls.append(1)
            return {"scheduled_reset_time": "2000-01-01T00:00:00+09:00"}

        sent = []
        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus(five_hour_reset=usage_reset, source="cache")), \
             patch.object(watcher, "load_state", side_effect=fake_load_state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status claude"), self._make_logger())

        self.assertEqual(len(load_state_calls), 0, "usage 파일 성공 시 load_state 호출 없어야 함")
        self.assertEqual(len(sent), 1)
        self.assertIn("⏳", sent[0])

    def test_status_with_no_state(self):
        """예약된 시각이 없을 때 미예약 안내 메시지를 전송해야 한다."""
        sent = []

        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value={}), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status claude"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])

    def test_status_with_past_reset_time(self):
        """과거 state 예약도 실제 초기화 시각처럼 응답하지 않아야 한다."""
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(
            timezone(timedelta(hours=9))
        ).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        state = {"scheduled_reset_time": past}
        sent = []

        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status claude"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])

    def test_unknown_command_sends_help(self):
        """/status 외 명령에는 사용법 안내를 전송해야 한다."""
        sent = []

        with patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/help"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("/status claude", sent[0])

    def test_ignores_unknown_chat_id(self):
        """허용되지 않은 chat_id에서 온 명령은 무시해야 한다."""
        sent = []

        with patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                self._cfg(),
                self._make_update("/status claude", chat_id="999999999"),
                self._make_logger(),
            )

        self.assertEqual(len(sent), 0)

    def test_dry_run_skips_send(self):
        """dry_run 모드에서는 실제 전송 없이 로그만 남겨야 한다."""
        with patch("urllib.request.urlopen") as mock_open, \
             patch.object(watcher, "load_state", return_value={}):
            watcher.handle_telegram_command(
                self._cfg(),
                self._make_update("/status claude"),
                self._make_logger(),
                dry_run=True,
            )

        mock_open.assert_not_called()


class TestUsageCacheStatus(unittest.TestCase):

    def _cfg(self):
        return {"CLAUDE_PROJECTS_DIR": str(Path(tempfile.gettempdir()) / "missing-token-alert-projects")}

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def _write_usage(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_reads_nested_rate_limits_five_hour_reset(self):
        """rate_limits.five_hour.resets_at 형태의 cache를 읽어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            future = datetime.now(timezone.utc) + timedelta(hours=2)
            self._write_usage(usage_path, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "rate_limits": {"five_hour": {"resets_at": future.timestamp()}},
            })

            with patch.object(watcher, "USAGE_FILE", usage_path):
                result = watcher.read_reset_time_from_usage_file()

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.timestamp(), future.timestamp(), delta=1)

    def test_ignores_past_reset_or_stale_updated_at(self):
        """과거 reset 시각이나 오래된 updated_at을 가진 cache는 무시해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            past = datetime.now(timezone.utc) - timedelta(minutes=1)
            stale_future = datetime.now(timezone.utc) + timedelta(hours=2)

            self._write_usage(usage_path, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "five_hour_resets_at": past.timestamp(),
            })
            with patch.object(watcher, "USAGE_FILE", usage_path):
                self.assertIsNone(watcher.read_reset_time_from_usage_file())

            self._write_usage(usage_path, {
                "updated_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
                "five_hour_resets_at": stale_future.timestamp(),
            })
            with patch.object(watcher, "USAGE_FILE", usage_path):
                self.assertIsNone(watcher.read_reset_time_from_usage_file())

    def test_status_ignores_state_scheduled_reset_without_cache(self):
        """usage cache가 없고 state에 미래 scheduled_reset_time이 있어도 남은 시간 표시 금지."""
        sent = []
        state = {"scheduled_reset_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}

        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
                {"message": {"text": "/status claude", "chat": {"id": 1}}},
                self._make_logger(),
            )

        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])
        self.assertNotIn("예정 시각:", sent[0])

    def test_status_reads_usage_cache_from_temp_file_before_state(self):
        """usage cache가 있으면 임시 파일 값이 state보다 우선해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            future = datetime.now(timezone.utc) + timedelta(hours=3)
            self._write_usage(usage_path, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "five_hour_resets_at": future.timestamp(),
            })
            sent = []

            with patch.object(watcher, "USAGE_FILE", usage_path), \
                 patch.object(watcher, "load_state", return_value={"scheduled_reset_time": "2000-01-01T00:00:00+09:00"}), \
                 patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
                watcher.handle_telegram_command(
                    {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
                    {"message": {"text": "/status claude", "chat": {"id": 1}}},
                    self._make_logger(),
                )

        self.assertIn("5시간 단기 한도", sent[0])
        self.assertIn("남은 시간", sent[0])

    def test_status_reports_five_hour_and_seven_day_limits(self):
        """5시간과 7일 한도 값이 함께 있으면 둘 다 응답해야 한다."""
        now = datetime.now(timezone.utc)
        status = watcher.LimitStatus(
            five_hour_reset=now + timedelta(hours=1),
            seven_day_reset=now + timedelta(days=2),
            source="cache",
        )
        sent = []

        with patch.object(watcher, "get_current_limit_status", return_value=status), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
                {"message": {"text": "/status claude", "chat": {"id": 1}}},
                self._make_logger(),
            )

        self.assertIn("5시간 단기 한도", sent[0])
        self.assertIn("7일 장기 한도", sent[0])

    def test_jsonl_fallback_status_is_marked_estimated(self):
        """JSONL 폴백을 쓰면 응답에 추정값임이 드러나야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp)
            log_path = projects_dir / "session.jsonl"
            ts = datetime.now(timezone.utc) - timedelta(hours=1)
            log_path.write_text(json.dumps({"timestamp": ts.isoformat()}) + "\n", encoding="utf-8")
            sent = []

            with patch.object(watcher, "USAGE_FILE", Path(tmp) / "missing.json"), \
                 patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
                watcher.handle_telegram_command(
                    {
                        "TELEGRAM_BOT_TOKEN": "fake",
                        "TELEGRAM_CHAT_ID": "1",
                        "CLAUDE_PROJECTS_DIR": str(projects_dir),
                    },
                    {"message": {"text": "/status claude", "chat": {"id": 1}}},
                    self._make_logger(),
                )

        self.assertIn("추정값", sent[0])
    def test_jsonl_fallback_uses_gjc_sessions_dir_when_claude_projects_missing(self):
        """CLAUDE_PROJECTS_DIR가 없어도 GJC_SESSIONS_DIR의 jsonl로 추정값을 계산해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            gjc_sessions_dir = Path(tmp) / "gjc-sessions" / "-some-project"
            gjc_sessions_dir.mkdir(parents=True)
            log_path = gjc_sessions_dir / "2026-01-01T00-00-00-000Z_abc.jsonl"
            ts = datetime.now(timezone.utc) - timedelta(hours=1)
            log_path.write_text(json.dumps({"type": "session", "timestamp": ts.isoformat()}) + "\n", encoding="utf-8")
            sent = []

            with patch.object(watcher, "USAGE_FILE", Path(tmp) / "missing.json"), \
                 patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
                watcher.handle_telegram_command(
                    {
                        "TELEGRAM_BOT_TOKEN": "fake",
                        "TELEGRAM_CHAT_ID": "1",
                        "CLAUDE_PROJECTS_DIR": str(Path(tmp) / "missing-claude-projects"),
                        "GJC_SESSIONS_DIR": str(Path(tmp) / "gjc-sessions"),
                    },
                    {"message": {"text": "/status claude", "chat": {"id": 1}}},
                    self._make_logger(),
                )

        self.assertIn("추정값", sent[0])

    def test_jsonl_source_dirs_merge_picks_oldest_across_both(self):
        """Claude 네이티브 CLI와 GJC 세션이 함께 있으면 둘 중 더 오래된 타임스탬프를 골라야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / "claude-projects"
            gjc_dir = Path(tmp) / "gjc-sessions"
            claude_dir.mkdir()
            gjc_dir.mkdir()

            newer = datetime.now(timezone.utc) - timedelta(minutes=10)
            older = datetime.now(timezone.utc) - timedelta(hours=3)
            (claude_dir / "a.jsonl").write_text(json.dumps({"timestamp": newer.isoformat()}) + "\n", encoding="utf-8")
            (gjc_dir / "b.jsonl").write_text(json.dumps({"timestamp": older.isoformat()}) + "\n", encoding="utf-8")

            cfg = {"CLAUDE_PROJECTS_DIR": str(claude_dir), "GJC_SESSIONS_DIR": str(gjc_dir)}
            oldest = watcher.find_oldest_message_in_window(watcher.get_jsonl_source_dirs(cfg))

        self.assertAlmostEqual(oldest.timestamp(), older.timestamp(), delta=1)

    def test_find_oldest_message_in_window_accepts_single_path(self):
        """단일 Path 인자로도 기존처럼 동작해야 한다(하위 호환)."""
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp)
            ts = datetime.now(timezone.utc) - timedelta(hours=1)
            (projects_dir / "a.jsonl").write_text(json.dumps({"timestamp": ts.isoformat()}) + "\n", encoding="utf-8")

            oldest = watcher.find_oldest_message_in_window(projects_dir)

        self.assertAlmostEqual(oldest.timestamp(), ts.timestamp(), delta=1)


    def test_status_ignores_state_after_dispatch_failure(self):
        """dispatch 실패 뒤 stale state가 실제 초기화 시각처럼 표시되면 안 된다."""
        sent = []
        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value={"scheduled_reset_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
                {"message": {"text": "/status claude", "chat": {"id": 1}}},
                self._make_logger(),
            )

        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])

    def test_status_ignores_stale_state_after_near_reset_skip(self):
        """초기화 5분 이하로 dispatch를 건너뛰어도 stale state 노출 금지."""
        sent = []
        with patch.object(watcher, "get_current_limit_status", return_value=watcher.LimitStatus()), \
             patch.object(watcher, "load_state", return_value={"scheduled_reset_time": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()}), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
                {"message": {"text": "/status claude", "chat": {"id": 1}}},
                self._make_logger(),
            )

        self.assertIn("아직 Claude 한도 값을 받은 적이 없습니다", sent[0])

    def test_notify_advance_dispatch_sends_reset_and_notify_times(self):
        reset_time = datetime(2026, 6, 24, 6, 30, tzinfo=timezone.utc)
        cfg = {
            "GITHUB_TOKEN": "fake-token",
            "GITHUB_OWNER": "owner",
            "GITHUB_REPO": "repo",
            "NOTIFY_ADVANCE_SECONDS": "600",
        }
        dispatch_resp = MagicMock()
        dispatch_resp.status = 204
        dispatch_resp.__enter__ = lambda s: s
        dispatch_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(watcher, "_get_pending_runs", return_value=[]), \
             patch("urllib.request.urlopen", return_value=dispatch_resp) as mock_open:
            ok = watcher.dispatch_github_workflow(cfg, reset_time, self._make_logger(), dry_run=False)

        self.assertTrue(ok)
        payload = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(payload["inputs"]["reset_time"], "2026-06-24T15:30:00+09:00")
        self.assertEqual(payload["inputs"]["notify_time"], "2026-06-24T15:20:00+09:00")
        self.assertEqual(payload["inputs"]["target_label"], "Claude Code 5시간")

    def test_dispatch_sends_custom_target_label(self):
        reset_time = datetime(2026, 6, 24, 6, 30, tzinfo=timezone.utc)
        cfg = {
            "GITHUB_TOKEN": "fake-token",
            "GITHUB_OWNER": "owner",
            "GITHUB_REPO": "repo",
            "GITHUB_REF": "direct-usage",
        }
        dispatch_resp = MagicMock()
        dispatch_resp.status = 204
        dispatch_resp.__enter__ = lambda s: s
        dispatch_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(watcher, "_get_pending_runs", return_value=[]), \
             patch("urllib.request.urlopen", return_value=dispatch_resp) as mock_open:
            ok = watcher.dispatch_github_workflow(
                cfg,
                reset_time,
                self._make_logger(),
                dry_run=False,
                target_label="Codex work 5시간",
            )

        self.assertTrue(ok)
        payload = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(payload["ref"], "direct-usage")
        self.assertEqual(payload["inputs"]["target_label"], "Codex work 5시간")

    def test_status_line_writer_saves_flat_and_nested_fields(self):
        """statusLine writer가 호환 필드와 rate_limits 원본 필드를 함께 저장해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            future = datetime.now(timezone.utc) + timedelta(hours=2)
            seven = datetime.now(timezone.utc) + timedelta(days=3)

            watcher.write_usage_cache_from_status_line(
                {
                    "rate_limits": {
                        "five_hour": {"resets_at": future.timestamp(), "used_percentage": 91},
                        "seven_day": {"resets_at": seven.timestamp(), "used_percentage": 42},
                    }
                },
                usage_path,
            )

            data = json.loads(usage_path.read_text(encoding="utf-8"))

        self.assertAlmostEqual(data["five_hour_resets_at"], future.timestamp(), delta=1)
        self.assertAlmostEqual(data["seven_day_resets_at"], seven.timestamp(), delta=1)
        self.assertEqual(data["five_hour_used_percentage"], 91)
        self.assertEqual(data["seven_day_used_percentage"], 42)
        self.assertIn("updated_at", data)


class TestDirectUsageFetch(unittest.TestCase):

    def _make_logger(self):
        import logging
        return logging.getLogger("test")

    def _make_response(self, body: dict, status: int = 200):
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _jwt(self, payload: dict) -> str:
        raw = json.dumps(payload).encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return f"header.{encoded}.signature"

    def test_codex_auth_redacts_token_and_usage_maps_remaining_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps({
                "tokens": {
                    "id_token": self._jwt({
                        "email": "me@example.com",
                        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                    }),
                    "access_token": "secret-access-token",
                    "account_id": "acct_123",
                }
            }), encoding="utf-8")

            auth = watcher.read_codex_auth(auth_path)
            self.assertIsNotNone(auth)
            self.assertNotIn("secret-access-token", str(auth))
            self.assertIn("[redacted]", str(auth))

            now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
            resp = self._make_response({
                "email": "me@example.com",
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {"used_percent": 72.4, "reset_after_seconds": 600},
                    "secondary_window": {"used_percent": 10, "reset_after_seconds": 3600},
                },
            })

            with patch("urllib.request.urlopen", return_value=resp):
                status = watcher.fetch_codex_usage_status(auth, now, self._make_logger())

        self.assertEqual(status.five_hour_remaining_percentage, 28)
        self.assertEqual(status.seven_day_remaining_percentage, 90)
        self.assertEqual(status.five_hour_reset, now + timedelta(seconds=600))
        self.assertEqual(status.source, "codex")

    def test_claude_usage_response_maps_utilization_and_reset(self):
        five_dt = datetime.now(timezone.utc) + timedelta(hours=1)
        seven_dt = datetime.now(timezone.utc) + timedelta(days=7)
        resp = self._make_response({
            "five_hour": {"utilization": 81.5, "resets_at": five_dt.isoformat()},
            "seven_day": {"utilization": 41, "resets_at": seven_dt.isoformat()},
        })
        credentials = watcher.ClaudeCredentials("claude-token", None, None, ("user:profile",))

        with patch("urllib.request.urlopen", return_value=resp):
            status = watcher.fetch_claude_usage_status(credentials, self._make_logger())

        self.assertEqual(status.five_hour_used_percentage, 81.5)
        self.assertEqual(status.seven_day_used_percentage, 41)
        self.assertAlmostEqual(status.five_hour_reset.timestamp(), five_dt.timestamp(), delta=1)
        self.assertEqual(status.source, "claude")

    def test_direct_api_failure_falls_back_to_usage_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            usage_path = Path(tmp) / "usage.json"
            future = datetime.now(timezone.utc) + timedelta(hours=2)
            auth_path.write_text(json.dumps({
                "tokens": {
                    "access_token": "secret-access-token",
                    "account_id": "acct_123",
                }
            }), encoding="utf-8")
            usage_path.write_text(json.dumps({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "five_hour_resets_at": future.timestamp(),
            }), encoding="utf-8")

            with patch.object(watcher, "USAGE_FILE", usage_path), \
                 patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
                status = watcher.get_current_limit_status({
                    "CODEX_AUTH_JSON": str(auth_path),
                    "CLAUDE_USAGE_CREDENTIALS": str(Path(tmp) / "missing-claude.json"),
                    "CLAUDE_PROJECTS_DIR": str(Path(tmp) / "missing-projects"),
                    "GJC_SESSIONS_DIR": str(Path(tmp) / "missing-sessions"),
                })

        self.assertEqual(status.source, "cache")
        self.assertAlmostEqual(status.five_hour_reset.timestamp(), future.timestamp(), delta=1)

    def test_status_distinguishes_provider_windows(self):
        now = datetime.now(timezone.utc)
        status = watcher.LimitStatus(
            five_hour_reset=now + timedelta(hours=1),
            provider_windows=(
                watcher.ProviderWindow(
                    provider="codex",
                    label="Codex",
                    window="five_hour",
                    reset=now + timedelta(hours=1),
                    remaining_percentage=37,
                    used_percentage=None,
                    estimated=False,
                ),
                watcher.ProviderWindow(
                    provider="claude",
                    label="Claude",
                    window="seven_day",
                    reset=now + timedelta(days=2),
                    remaining_percentage=None,
                    used_percentage=42,
                    estimated=False,
                ),
            ),
            source="direct",
        )

        reply = watcher.format_limit_status_reply(status)

        self.assertIn("🤖 <b>Codex</b>", reply)
        self.assertIn("계정: <code>default</code>", reply)
        self.assertIn("⚡ <b>5시간 한도</b>", reply)
        self.assertIn("남은 비율: 37%", reply)
        self.assertIn("🧠 <b>Claude</b>", reply)
        self.assertIn("📅 <b>7일 한도</b>", reply)
        self.assertIn("사용 비율: 42%", reply)

    def test_codex_profiles_fetches_every_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = Path(tmp) / "profiles"
            for name in ("work", "personal"):
                profile_dir = profiles_dir / name
                profile_dir.mkdir(parents=True)
                (profile_dir / "auth.json").write_text(json.dumps({
                    "tokens": {
                        "id_token": self._jwt({"email": f"{name}@example.com"}),
                        "access_token": f"{name}-token",
                        "account_id": f"acct-{name}",
                    }
                }), encoding="utf-8")

            responses = [
                self._make_response({
                    "rate_limit": {
                        "primary_window": {"used_percent": 25, "reset_after_seconds": 600},
                    }
                }),
                self._make_response({
                    "rate_limit": {
                        "primary_window": {"used_percent": 80, "reset_after_seconds": 1200},
                    }
                }),
            ]

            with patch("urllib.request.urlopen", side_effect=responses):
                status = watcher.fetch_direct_usage_status({
                    "CODEX_PROFILES_DIR": str(profiles_dir),
                    "CLAUDE_USAGE_CREDENTIALS": str(Path(tmp) / "missing-claude.json"),
                }, self._make_logger())

        codex_windows = [w for w in status.provider_windows if w.provider == "codex"]
        self.assertEqual([w.profile for w in codex_windows], ["personal", "work"])
        self.assertEqual([w.remaining_percentage for w in codex_windows], [75, 20])

    def test_run_once_schedules_each_provider_window_independently(self):
        now = datetime.now(timezone.utc)
        windows = (
            watcher.ProviderWindow(
                provider="codex",
                label="Codex work",
                window="five_hour",
                reset=now + timedelta(hours=1),
                profile="work",
            ),
            watcher.ProviderWindow(
                provider="codex",
                label="Codex personal",
                window="five_hour",
                reset=now + timedelta(hours=2),
                profile="personal",
            ),
            watcher.ProviderWindow(
                provider="claude",
                label="Claude",
                window="five_hour",
                reset=now + timedelta(hours=3),
            ),
        )
        status = watcher.LimitStatus(five_hour_reset=windows[0].reset, source="direct", provider_windows=windows)
        dispatched = []
        state = {}

        with patch.object(watcher, "get_current_limit_status", return_value=status), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "save_state", side_effect=lambda new_state: state.update(new_state)), \
             patch.object(watcher, "dispatch_github_workflow", side_effect=lambda cfg, reset, logger, dry_run=False, target_label="": dispatched.append((reset, target_label)) or True):
            watcher.run_once({}, self._make_logger(), dry_run=False)

        self.assertEqual(len(dispatched), 3)
        self.assertEqual(
            [label for _, label in dispatched],
            ["Codex work 5시간", "Codex personal 5시간", "Claude 5시간"],
        )
        self.assertIn("scheduled_resets", state)
        self.assertEqual(len(state["scheduled_resets"]), 3)

    def test_status_command_sends_provider_choice_and_text_fallback(self):
        sent = []
        now = datetime.now(timezone.utc)
        status = watcher.LimitStatus(
            five_hour_reset=now + timedelta(hours=1),
            provider_windows=(
                watcher.ProviderWindow("codex", "Codex work", "five_hour", now + timedelta(hours=1), profile="work"),
                watcher.ProviderWindow("claude", "Claude", "five_hour", now + timedelta(hours=2)),
            ),
            source="direct",
        )

        with patch.object(watcher, "get_current_limit_status", return_value=status), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append((text, kw))):
            cfg = {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"}
            watcher.handle_telegram_command(cfg, {"message": {"text": "/status", "chat": {"id": 1}}}, self._make_logger())
            watcher.handle_telegram_command(cfg, {"message": {"text": "/status codex", "chat": {"id": 1}}}, self._make_logger())
            watcher.handle_telegram_command(cfg, {"callback_query": {"data": "status:claude", "message": {"chat": {"id": 1}}}}, self._make_logger())

        self.assertIn("선택", sent[0][0])
        self.assertIn("reply_markup", sent[0][1])
        self.assertIn("🤖 <b>Codex 남은 시간</b>", sent[1][0])
        self.assertIn("계정: <code>work</code>", sent[1][0])
        self.assertNotIn("Claude", sent[1][0])
        self.assertIn("Claude", sent[2][0])
        self.assertNotIn("Codex work", sent[2][0])


class TestWorkflowDefinition(unittest.TestCase):

    def test_workflow_keeps_provider_labels_independent(self):
        workflow = (Path(__file__).parent.parent / ".github" / "workflows" / "token-reset-notify.yml").read_text(encoding="utf-8")

        self.assertIn("target_label:", workflow)
        self.assertIn("group: token-reset-notify-${{ inputs.target_label", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("cancel-in-progress: true", workflow)
        self.assertIn("TARGET_LABEL", workflow)
        self.assertIn("${{ inputs.target_label", workflow)
        self.assertIn("sendMessage", workflow)
        self.assertIn("PROVIDER=\"Claude\"", workflow)
        self.assertIn("ACCOUNT=\"${TARGET_LABEL#Codex }\"", workflow)
        self.assertIn("🤖 <b>Codex 초기화 완료</b>", workflow)
        self.assertIn("🧠 <b>Claude 초기화 완료</b>", workflow)
        self.assertIn("계정: ", workflow)
        self.assertIn("⚡ 5시간 한도 초기화됨", workflow)
        self.assertIn("📅 7일 한도 초기화됨", workflow)
        self.assertIn("• <code>", workflow)
        self.assertNotIn("sendPhoto", workflow)
        self.assertNotIn("assets/telegram/", workflow)
        self.assertIn("Codex 초기화 완료", workflow)
        self.assertIn("Claude 초기화 완료", workflow)
        self.assertNotIn("완료 예정", workflow)
        self.assertNotIn("알림 전송:", workflow)


if __name__ == "__main__":
    unittest.main()
