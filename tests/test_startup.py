import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import startup


class StartupTests(unittest.TestCase):
    def test_is_supported_only_on_windows(self) -> None:
        with patch("src.startup.platform.system", return_value="Windows"):
            self.assertTrue(startup.is_supported())

        with patch("src.startup.platform.system", return_value="Linux"):
            self.assertFalse(startup.is_supported())

    def test_startup_command_uses_frozen_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Screamer Test ") as tmp:
            exe = str(Path(tmp) / "Screamer.exe")

            with patch.object(sys, "executable", exe), patch.object(sys, "frozen", True, create=True):
                command = startup.startup_command()

        self.assertEqual(command, f'"{exe}" --startup')

    def test_startup_command_prefers_pythonw_for_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Python Test ") as tmp:
            python = Path(tmp) / "python.exe"
            pythonw = Path(tmp) / "pythonw.exe"
            python.touch()
            pythonw.touch()

            with patch.object(sys, "executable", str(python)):
                command = startup.startup_command()

        self.assertEqual(command, f'"{pythonw}" -m src.main --startup')

    def test_sync_enabled_skips_current_enabled_registration(self) -> None:
        with (
            patch("src.startup.get_registered_command", return_value="current"),
            patch("src.startup.startup_command", return_value="current"),
            patch("src.startup.set_enabled") as set_enabled,
        ):
            startup.sync_enabled(True)

        set_enabled.assert_not_called()

    def test_sync_enabled_repairs_stale_enabled_registration(self) -> None:
        with (
            patch("src.startup.get_registered_command", return_value="old"),
            patch("src.startup.startup_command", return_value="current"),
            patch("src.startup.set_enabled") as set_enabled,
        ):
            startup.sync_enabled(True)

        set_enabled.assert_called_once_with(True)

    def test_sync_enabled_skips_current_disabled_registration(self) -> None:
        with (
            patch("src.startup.get_registered_command", return_value=None),
            patch("src.startup.set_enabled") as set_enabled,
        ):
            startup.sync_enabled(False)

        set_enabled.assert_not_called()


if __name__ == "__main__":
    unittest.main()
