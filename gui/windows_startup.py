"""Manage the current user's Windows Run entry for Market Alert Engine."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "MarketAlertEngine"


def is_windows_autostart_enabled() -> bool:
    """Return whether the current user's Windows Run entry exists."""
    if os.name != "nt":
        return False

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as run_key:
            winreg.QueryValueEx(run_key, _VALUE_NAME)
    except FileNotFoundError:
        return False
    return True


def set_windows_autostart(enabled: bool) -> None:
    """Enable or disable login startup for the current Windows user."""
    if os.name != "nt":
        raise OSError("Windows autostart is available only on Windows.")

    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        _RUN_KEY,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
    ) as run_key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = subprocess.list2cmdline(
                    [sys.executable, "--start-hidden"]
                )
            else:
                interpreter = Path(sys.executable)
                gui_interpreter = interpreter.with_name("pythonw.exe")
                if gui_interpreter.is_file():
                    interpreter = gui_interpreter
                entry_script = Path(__file__).resolve().parent / "__main__.py"
                command = subprocess.list2cmdline(
                    [
                        str(interpreter),
                        str(entry_script),
                        "--start-hidden",
                    ]
                )
            winreg.SetValueEx(
                run_key, _VALUE_NAME, 0, winreg.REG_SZ, command
            )
            return

        try:
            winreg.DeleteValue(run_key, _VALUE_NAME)
        except FileNotFoundError:
            pass
