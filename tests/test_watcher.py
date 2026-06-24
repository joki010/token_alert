import os
import sys
import json
import tempfile
import unittest
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

    def test_parse_run_reset_time(self):
        """display_title(우선) 및 inputs.reset_time(폴백)에서 reset_time을 올바르게 파싱해야 한다."""
        from datetime import timezone, timedelta
        KST = timezone(timedelta(hours=9))

        # display_title 우선 (run-name → API보장 필드)
        run_display = {"display_title": "2026-06-24T15:00:00+09:00"}
        result = watcher._parse_run_reset_time(run_display)
        self.assertIsNotNone(result)
        self.assertEqual(result.astimezone(KST).strftime("%H:%M"), "15:00")

        # inputs 폴백 (display_title 없을 때)
        run_inputs = {"inputs": {"reset_time": "2026-06-24T16:00:00+09:00"}}
        result2 = watcher._parse_run_reset_time(run_inputs)
        self.assertIsNotNone(result2)
        self.assertEqual(result2.astimezone(KST).strftime("%H:%M"), "16:00")

        # 둘 다 없는 경우 → None
        self.assertIsNone(watcher._parse_run_reset_time({}))
        self.assertIsNone(watcher._parse_run_reset_time({"inputs": None}))
        self.assertIsNone(watcher._parse_run_reset_time({"inputs": {"other": "val"}}))

    def test_dispatch_skips_when_earlier_pending_run_exists(self):
        """기존 예약 시각이 더 이른 경우 dispatch를 건너뛰어야 한다 (맥/윈도우 동시 실행 충돌 방지)."""
        from datetime import datetime, timezone, timedelta

        cfg = self._cfg()

        # 기존 pending run: display_title에 15:00 KST
        pending_run = {"id": 999, "display_title": "2026-06-24T15:00:00+09:00"}

        # 현재 계산된 reset_time: 15:30 KST (더 늦음)
        KST = timezone(timedelta(hours=9))
        later_reset = datetime(2026, 6, 24, 15, 30, 0, tzinfo=KST)

        with patch.object(watcher, "_get_pending_runs", return_value=[pending_run]), \
             patch("urllib.request.urlopen") as mock_open:
            result = watcher.dispatch_github_workflow(cfg, later_reset, self._make_logger(), dry_run=False)

        # dispatch 건너뜀 → urlopen 호출 없어야 함
        self.assertFalse(result)
        mock_open.assert_not_called()

    def test_dispatch_proceeds_when_no_parseable_pending_run(self):
        """pending run에서 reset_time 파싱 불가(display_title·inputs 모두 없음)면 dispatch 진행해야 한다."""
        from datetime import datetime, timezone, timedelta

        cfg = self._cfg()

        # display_title·inputs 모두 없는 run (API 필드 누락 폴백 검증)
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
        """미래 초기화 시각이 있을 때 남은 시간을 포함한 응답을 전송해야 한다 (state 파일 폴백)."""
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1, minutes=23)).astimezone(
            timezone(timedelta(hours=9))
        ).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        state = {"scheduled_reset_time": future}
        sent = []

        with patch.object(watcher, "read_reset_time_from_usage_file", return_value=None), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("⏳", sent[0])
        self.assertIn("남았습니다", sent[0])

    def test_status_usage_file_takes_priority_over_state(self):
        """usage 파일 값이 있으면 state 파일보다 우선하여 응답해야 한다."""
        from datetime import datetime, timezone, timedelta
        usage_reset = datetime.now(timezone.utc) + timedelta(hours=3)
        load_state_calls = []

        def fake_load_state():
            load_state_calls.append(1)
            return {"scheduled_reset_time": "2000-01-01T00:00:00+09:00"}

        sent = []
        with patch.object(watcher, "read_reset_time_from_usage_file", return_value=usage_reset), \
             patch.object(watcher, "load_state", side_effect=fake_load_state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status"), self._make_logger())

        self.assertEqual(len(load_state_calls), 0, "usage 파일 성공 시 load_state 호출 없어야 함")
        self.assertEqual(len(sent), 1)
        self.assertIn("⏳", sent[0])

    def test_status_with_no_state(self):
        """예약된 시각이 없을 때 미예약 안내 메시지를 전송해야 한다."""
        sent = []

        with patch.object(watcher, "read_reset_time_from_usage_file", return_value=None), \
             patch.object(watcher, "load_state", return_value={}), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("✅", sent[0])

    def test_status_with_past_reset_time(self):
        """초기화 시각이 이미 지났으면 미예약 안내 메시지를 전송해야 한다."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(
            timezone(timedelta(hours=9))
        ).strftime("%Y-%m-%dT%H:%M:%S+09:00")

        state = {"scheduled_reset_time": past}
        sent = []

        with patch.object(watcher, "read_reset_time_from_usage_file", return_value=None), \
             patch.object(watcher, "load_state", return_value=state), \
             patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/status"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("✅", sent[0])

    def test_unknown_command_sends_help(self):
        """/status 외 명령에는 사용법 안내를 전송해야 한다."""
        sent = []

        with patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(self._cfg(), self._make_update("/help"), self._make_logger())

        self.assertEqual(len(sent), 1)
        self.assertIn("/status", sent[0])

    def test_ignores_unknown_chat_id(self):
        """허용되지 않은 chat_id에서 온 명령은 무시해야 한다."""
        sent = []

        with patch.object(watcher, "send_telegram_message", side_effect=lambda cfg, text, logger, **kw: sent.append(text)):
            watcher.handle_telegram_command(
                self._cfg(),
                self._make_update("/status", chat_id="999999999"),
                self._make_logger(),
            )

        self.assertEqual(len(sent), 0)

    def test_dry_run_skips_send(self):
        """dry_run 모드에서는 실제 전송 없이 로그만 남겨야 한다."""
        with patch("urllib.request.urlopen") as mock_open, \
             patch.object(watcher, "load_state", return_value={}):
            watcher.handle_telegram_command(
                self._cfg(),
                self._make_update("/status"),
                self._make_logger(),
                dry_run=True,
            )

        mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
