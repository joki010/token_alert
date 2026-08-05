import os
import json
import tempfile
import stat
from pathlib import Path
from unittest import mock, TestCase
import subprocess
import sys
import importlib.util

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

ROOT_DIR = Path(__file__).parent.parent
tray = load_module("tray", ROOT_DIR / "platform" / "macos" / "tray.py")
install = load_module("install", ROOT_DIR / "platform" / "macos" / "install.py")
uninstall = load_module("uninstall", ROOT_DIR / "platform" / "macos" / "uninstall.py")

class TestTrayPolicy(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.policy_path = self.tmp_path / "activation-policy.json"
        self.notify_policy_path = self.tmp_path / "notify-policy.json"
        self.patcher = mock.patch("tray.POLICY_FILE", self.policy_path)
        self.notify_patcher = mock.patch("tray.NOTIFY_POLICY_FILE", self.notify_policy_path)
        self.patcher.start()
        self.notify_patcher.start()

    def tearDown(self):
        self.notify_patcher.stop()
        self.patcher.stop()
        self.tmp_dir.cleanup()

    def test_policy_default_when_missing(self):
        self.assertFalse(self.policy_path.exists())
        policy = tray.read_policy()
        self.assertEqual(policy, {"version": 1, "enabled": False})

    def test_policy_default_when_corrupt(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text("invalid json", encoding="utf-8")
        policy = tray.read_policy()
        self.assertEqual(policy, {"version": 1, "enabled": False})

    def test_policy_strict_validation(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Missing enabled_at
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})
        
        # Non-bool enabled
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": "true", "enabled_at": "2026-07-19T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})
        
        # Naive timestamp
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "2026-07-19T00:00:00"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})
        
        # Invalid timestamp
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "invalid"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})
        
        # Unsupported version
        self.policy_path.write_text(json.dumps({"version": 2, "enabled": True, "enabled_at": "2026-07-19T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})

        # bool is an int subclass but not an exact schema version
        self.policy_path.write_text(json.dumps({"version": True, "enabled": True, "enabled_at": "2026-07-19T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})

        # Exact schema rejects extra fields
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "2026-07-19T00:00:00Z", "extra": 1}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})

        # Policy timestamps must be UTC, not merely timezone-aware
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "2026-07-19T09:00:00+09:00"}), encoding="utf-8")
        self.assertEqual(tray.read_policy(), {"version": 1, "enabled": False})
        
        # Valid aware timestamp (+00:00)
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "2026-07-19T00:00:00+00:00"}), encoding="utf-8")
        self.assertEqual(tray.read_policy()["enabled"], True)
        
        # Valid aware timestamp (Z)
        self.policy_path.write_text(json.dumps({"version": 1, "enabled": True, "enabled_at": "2026-07-19T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(tray.read_policy()["enabled"], True)
        
    def test_policy_read_write_persistence(self):
        self.assertTrue(tray.write_policy(True))
        self.assertTrue(self.policy_path.exists())
        policy = tray.read_policy()
        self.assertEqual(policy["version"], 1)
        self.assertTrue(policy["enabled"])
        self.assertIn("enabled_at", policy)
        
        # Test mode is 0600
        mode = stat.S_IMODE(os.stat(self.policy_path).st_mode)
        self.assertEqual(mode, 0o600)
        
        import time
        time.sleep(1.1)
        
        self.assertTrue(tray.write_policy(False))
        policy2 = tray.read_policy()
        self.assertFalse(policy2["enabled"])
        self.assertNotEqual(policy2["enabled_at"], policy["enabled_at"])

    def test_notify_policy_default_corrupt_and_persistence(self):
        self.assertEqual(
            tray.read_notify_policy(),
            {"version": 1, "enabled": False},
        )

        self.notify_policy_path.write_text("invalid json", encoding="utf-8")
        self.assertEqual(
            tray.read_notify_policy(),
            {"version": 1, "enabled": False},
        )

        self.assertTrue(tray.write_notify_policy(True))
        policy = tray.read_notify_policy()
        self.assertEqual(policy["version"], 1)
        self.assertTrue(policy["enabled"])
        self.assertIn("enabled_at", policy)

    @mock.patch("tray.is_watcher_running", return_value=True)
    @mock.patch("tray.is_login_item_enabled", return_value=False)
    @mock.patch("tray.NSApplication")
    def test_tray_menu_contract(self, mock_nsapp, mock_login, mock_watcher):
        tray.write_policy(False)
        with mock.patch("rumps.App.run"):
            app = tray.TokenAlertApp()
            self.assertEqual(app.activation_item.title, "Claude 자동 창 시작")
            self.assertFalse(app.activation_item.state)
            self.assertEqual(app.notify_item.title, "클로드코드 알림")
            self.assertFalse(app.notify_item.state)
            
            # Simulate click
            app.toggle_activation(app.activation_item)
            self.assertTrue(app.activation_item.state)
            self.assertTrue(tray.read_policy()["enabled"])

            app.toggle_notify(app.notify_item)
            self.assertTrue(app.notify_item.state)
            self.assertTrue(tray.read_notify_policy()["enabled"])

class TestInstall(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        
        self.install_lib_dir = self.tmp_path / "lib" / "token_alert" / "src"
        self.installed_config_dir = self.tmp_path / ".config" / "token-alert"
        self.installed_config_env = self.installed_config_dir / "config.env"
        
        self.script_dir = self.tmp_path / "script_dir"
        self.src_dir = self.script_dir / "src"
        self.src_dir.mkdir(parents=True)
        for f in ["watcher.py", "atomic_json.py", "activation.py", "scheduling.py"]:
            (self.src_dir / f).write_text(f"# {f}", encoding="utf-8")
            
        self.config_env = self.script_dir / "config" / "config.env"
        self.config_env.parent.mkdir(parents=True)
        self.config_env.write_text("TELEGRAM_BOT_TOKEN=123", encoding="utf-8")

        self.notify_src_dir = self.tmp_path / "notify_src"
        self.notify_src_dir.mkdir(parents=True)
        for f in ["notify.sh", "detect_terminal_app.sh"]:
            (self.notify_src_dir / f).write_text(f"#!/bin/bash\n# {f}\n", encoding="utf-8")
        self.notify_install_dir = self.tmp_path / "lib" / "token_alert" / "notify"

        self.patchers = [
            mock.patch("install.INSTALL_LIB_DIR", self.install_lib_dir),
            mock.patch("install.INSTALLED_CONFIG_DIR", self.installed_config_dir),
            mock.patch("install.INSTALLED_CONFIG_ENV", self.installed_config_env),
            mock.patch("install.SCRIPT_DIR", self.script_dir),
            mock.patch("install.CONFIG_ENV", self.config_env),
            mock.patch("install.NOTIFY_SRC_DIR", self.notify_src_dir),
            mock.patch("install.NOTIFY_INSTALL_DIR", self.notify_install_dir),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp_dir.cleanup()

    @mock.patch("shutil.which", return_value="/mock/path/to/claude")
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_runtime_manifest_and_cli_persistence(self, mock_which):
        install.install_watcher_files()
        
        for f in ["watcher.py", "atomic_json.py", "activation.py", "scheduling.py"]:
            self.assertTrue((self.install_lib_dir / f).exists())
            self.assertEqual((self.install_lib_dir / f).read_text(encoding="utf-8"), f"# {f}")
            
        self.assertTrue(self.installed_config_env.exists())
        content = self.installed_config_env.read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_BOT_TOKEN=123", content)
        self.assertIn("CLAUDE_CLI_PATH=/mock/path/to/claude", content)

    @mock.patch("shutil.which")
    @mock.patch.dict(os.environ, {"CLAUDE_CLI_PATH": "/env/path/claude"}, clear=True)
    def test_cli_persistence_environment_precedence(self, mock_which):
        install.install_watcher_files()
        mock_which.assert_not_called()
        content = self.installed_config_env.read_text(encoding="utf-8")
        self.assertIn("CLAUDE_CLI_PATH=/env/path/claude", content)

    @mock.patch("shutil.which")
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_cli_persistence_existing_nonblank_preservation(self, mock_which):
        self.config_env.write_text("CLAUDE_CLI_PATH=/existing/config/claude\n", encoding="utf-8")
        install.install_watcher_files()
        mock_which.assert_not_called()
        content = self.installed_config_env.read_text(encoding="utf-8")
        self.assertIn("CLAUDE_CLI_PATH=/existing/config/claude", content)
        self.assertEqual(content.count("CLAUDE_CLI_PATH="), 1)

    @mock.patch("shutil.which")
    @mock.patch.dict(os.environ, {"CLAUDE_CLI_PATH": "   "}, clear=True)
    def test_cli_persistence_blank_config_and_env(self, mock_which):
        mock_which.return_value = "/mock/path/to/claude"
        self.config_env.write_text("CLAUDE_CLI_PATH=   \n", encoding="utf-8")
        install.install_watcher_files()
        content = self.installed_config_env.read_text(encoding="utf-8")
        self.assertIn("CLAUDE_CLI_PATH=/mock/path/to/claude", content)

    @mock.patch("shutil.which")
    @mock.patch.dict(os.environ, {"CLAUDE_CLI_PATH": "/env/path/claude"}, clear=True)
    def test_update_preserves_existing_installed_config_and_source(self, mock_which):
        self.installed_config_dir.mkdir(parents=True, exist_ok=True)
        self.installed_config_env.write_text(
            "TELEGRAM_BOT_TOKEN=installed\nCLAUDE_CLI_PATH=/installed/claude\n",
            encoding="utf-8",
        )
        self.config_env.write_text(
            "TELEGRAM_BOT_TOKEN=source\nCLAUDE_CLI_PATH=/source/claude\n",
            encoding="utf-8",
        )
        source_before = self.config_env.read_text(encoding="utf-8")

        install.install_watcher_files()

        mock_which.assert_not_called()
        self.assertEqual(
            self.installed_config_env.read_text(encoding="utf-8"),
            "TELEGRAM_BOT_TOKEN=installed\nCLAUDE_CLI_PATH=/installed/claude\n",
        )
        self.assertEqual(self.config_env.read_text(encoding="utf-8"), source_before)

    def test_missing_runtime_module_is_fatal_before_copy(self):
        (self.src_dir / "scheduling.py").unlink()

        with self.assertRaises(FileNotFoundError):
            install.install_watcher_files()

        self.assertFalse(self.install_lib_dir.exists())

    def test_notify_scripts_are_installed_executable(self):
        install.install_notify_scripts()

        for name in ["notify.sh", "detect_terminal_app.sh"]:
            path = self.notify_install_dir / name
            self.assertTrue(path.exists())
            self.assertTrue(os.access(path, os.X_OK))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_claude_hooks_are_idempotent_and_preserve_unrelated_hooks(self):
        settings_path = self.tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "PostToolUse": [{
                    "matcher": "",
                    "hooks": [{
                        "type": "command",
                        "command": "bash unrelated-hook.sh",
                    }],
                }],
            },
        }), encoding="utf-8")

        with mock.patch("install.CLAUDE_SETTINGS_PATH", settings_path):
            install.patch_claude_settings_hooks()
            install.patch_claude_settings_hooks()

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [
            hook.get("command")
            for groups in settings["hooks"].values()
            for group in groups
            for hook in group.get("hooks", [])
        ]
        expected = {
            f"bash {self.notify_install_dir}/detect_terminal_app.sh",
            f"bash {self.notify_install_dir}/notify.sh '✅ Claude Code' 'Task completed'",
        }
        for command in expected:
            self.assertEqual(commands.count(command), 1)
        self.assertIn("bash unrelated-hook.sh", commands)

    @mock.patch("subprocess.run")
    def test_smoke_hook(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0)
        install.verify_smoke()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("import watcher, atomic_json, activation, scheduling;", cmd[2])

    @mock.patch("subprocess.run")
    def test_smoke_failure_is_fatal(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1, stderr="import failed")

        with self.assertRaises(RuntimeError):
            install.verify_smoke()

class TestMisc(TestCase):
    def test_setup_tray_no_src_dependency(self):
        setup_path = Path("platform/macos/setup_tray.py")
        if setup_path.exists():
            content = setup_path.read_text(encoding="utf-8")
            self.assertNotIn("src", content, "setup_tray.py should not depend on src directory")

class TestUninstall(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @mock.patch("uninstall.confirm", return_value=True)
    def test_uninstall_policy_handling(self, mock_confirm):
        policy_file = self.tmp_path / "activation-policy.json"
        policy_file.write_text("{}", encoding="utf-8")
        
        with mock.patch("uninstall.POLICY_FILE", policy_file), \
             mock.patch("uninstall.INSTALL_LIB_DIR", self.tmp_path / "lib"), \
             mock.patch("uninstall.INSTALLED_CONFIG_ENV", self.tmp_path / "config.env"):
            uninstall.remove_installed_files()
            
        self.assertFalse(policy_file.exists())

    def test_remove_notify_hooks_only_removes_owned_commands(self):
        settings_path = self.tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        notify_install_dir = self.tmp_path / "lib" / "token_alert" / "notify"
        target_commands = [
            f"bash {notify_install_dir}/detect_terminal_app.sh",
            f"bash {notify_install_dir}/notify.sh '✅ Claude Code' 'Task completed'",
        ]
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [{
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": target_commands[0]},
                        {"type": "command", "command": "bash unrelated-session-hook.sh"},
                    ],
                }],
                "Stop": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": target_commands[1]}],
                }],
                "PostToolUse": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "bash unrelated-post-hook.sh"}],
                }],
            },
        }), encoding="utf-8")

        with mock.patch("uninstall.CLAUDE_SETTINGS_PATH", settings_path), \
             mock.patch("uninstall.NOTIFY_INSTALL_DIR", notify_install_dir):
            uninstall.remove_notify_hooks()

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [
            hook.get("command")
            for groups in settings["hooks"].values()
            for group in groups
            for hook in group.get("hooks", [])
        ]
        for command in target_commands:
            self.assertNotIn(command, commands)
        self.assertIn("bash unrelated-session-hook.sh", commands)
        self.assertIn("bash unrelated-post-hook.sh", commands)
