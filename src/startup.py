"""Windows startup registration via the current user's Run key."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from src.utils import APP_NAME, AppError, ScreamerError

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_ARG = "--startup"


def is_supported() -> bool:
    return platform.system() == "Windows"


def _pythonw_or_python() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def startup_command() -> str:
    """Return the command stored in HKCU Run."""
    if getattr(sys, "frozen", False):
        argv = [sys.executable, STARTUP_ARG]
    else:
        argv = [_pythonw_or_python(), "-m", "src.main", STARTUP_ARG]

    return subprocess.list2cmdline(argv)


def get_registered_command() -> str | None:
    if not is_supported():
        return None

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ScreamerError(AppError.STARTUP_REGISTRATION_FAILED, str(e))


def set_enabled(enabled: bool) -> None:
    if not is_supported():
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "Windows startup requires Windows")

    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        raise ScreamerError(AppError.STARTUP_REGISTRATION_FAILED, str(e))


def is_enabled() -> bool:
    return get_registered_command() is not None


def sync_enabled(enabled: bool) -> None:
    registered_command = get_registered_command()
    if enabled:
        if registered_command == startup_command():
            return
    elif registered_command is None:
        return

    set_enabled(enabled)
