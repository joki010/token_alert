import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest import TestCase, mock


ROOT_DIR = Path(__file__).parent.parent


def load_windows_install():
    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.KEY_SET_VALUE = 1
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey = mock.Mock()
    fake_winreg.SetValueEx = mock.Mock()
    path = ROOT_DIR / "platform" / "windows" / "install.py"
    spec = importlib.util.spec_from_file_location("windows_install", str(path))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
        sys.modules["windows_install"] = module
        spec.loader.exec_module(module)
    return module


windows_install = load_windows_install()


class TestWindowsRuntimeInstall(TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.script_root = self.root / "source"
        self.source_directory = self.script_root / "src"
        self.source_directory.mkdir(parents=True)
        for name in windows_install.RUNTIME_MODULE_NAMES:
            (self.source_directory / name).write_text(f"# {name}\n", encoding="utf-8")

        self.config_env = self.script_root / "config" / "config.env"
        self.config_env.parent.mkdir(parents=True)
        self.config_env.write_text("TELEGRAM_BOT_TOKEN=test\n", encoding="utf-8")
        self.install_directory = self.root / "installed" / "src"
        self.installed_config_directory = self.root / "installed-config"
        self.installed_config = self.installed_config_directory / "config.env"
        self.patchers = [
            mock.patch.object(windows_install, "SCRIPT_ROOT", self.script_root),
            mock.patch.object(windows_install, "CONFIG_ENV", self.config_env),
            mock.patch.object(windows_install, "INSTALL_LIB_DIR", self.install_directory),
            mock.patch.object(windows_install, "INSTALLED_CONFIG_DIR", self.installed_config_directory),
            mock.patch.object(windows_install, "INSTALLED_CONFIG_ENV", self.installed_config),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary_directory.cleanup()

    def test_atomic_runtime_manifest_installs_all_sibling_modules(self):
        windows_install.install_watcher_files()

        self.assertEqual(
            sorted(path.name for path in self.install_directory.glob("*.py")),
            sorted(windows_install.RUNTIME_MODULE_NAMES),
        )
        for name in windows_install.RUNTIME_MODULE_NAMES:
            self.assertEqual(
                (self.install_directory / name).read_text(encoding="utf-8"),
                f"# {name}\n",
            )
        self.assertEqual(list(self.install_directory.glob(".*.tmp")), [])

    def test_missing_runtime_module_fails_before_any_copy(self):
        (self.source_directory / "activation.py").unlink()

        with self.assertRaises(FileNotFoundError):
            windows_install.install_watcher_files()

        self.assertFalse(self.install_directory.exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
