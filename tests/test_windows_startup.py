"""Tests for current-user Windows startup registration."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import gui.windows_startup as windows_startup


def test_frozen_autostart_registers_packaged_executable(monkeypatch) -> None:
    run_key = object()
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=1,
        KEY_QUERY_VALUE=2,
        REG_SZ=3,
        CreateKeyEx=Mock(return_value=_RegistryKey(run_key)),
        SetValueEx=Mock(),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    executable = r"C:\Program Files\Market Alert Engine\MarketAlertEngine.exe"
    monkeypatch.setattr(sys, "executable", executable)

    windows_startup.set_windows_autostart(True)

    registry.SetValueEx.assert_called_once_with(
        run_key,
        "MarketAlertEngine",
        0,
        registry.REG_SZ,
        subprocess.list2cmdline([executable, "--start-hidden"]),
    )


def test_disabling_autostart_deletes_the_registered_value(monkeypatch) -> None:
    run_key = object()
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=1,
        KEY_QUERY_VALUE=2,
        CreateKeyEx=Mock(return_value=_RegistryKey(run_key)),
        DeleteValue=Mock(),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)

    windows_startup.set_windows_autostart(False)

    registry.DeleteValue.assert_called_once_with(run_key, "MarketAlertEngine")


class _RegistryKey:
    def __init__(self, key: object) -> None:
        self.key = key

    def __enter__(self) -> object:
        return self.key

    def __exit__(self, *_args: object) -> None:
        return None
