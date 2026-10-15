"""Shared Tk create/destroy helpers so tests do not leak Tcl interpreters."""

from __future__ import annotations

import gc
import tkinter as tk
from pathlib import Path

import pytest


def isolate_gui_persistent_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep each GUI test's alert stores out of per-user application data."""
    from data.active_alert_store import ActiveAlertStore
    from history.alert_history import AlertHistory

    monkeypatch.setattr(
        ActiveAlertStore,
        "default_path",
        staticmethod(lambda: tmp_path / "active_alerts.json"),
    )
    monkeypatch.setattr(
        AlertHistory,
        "default_path",
        staticmethod(lambda: tmp_path / "alert_history.jsonl"),
    )


def shutdown_app(app: object) -> None:
    """Perform real app shutdown before destroying its Tk interpreter."""
    root = getattr(app, "root")
    try:
        getattr(app, "_shutdown")()
    finally:
        destroy_tk(root)


def tk_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    except ImportError:
        return False
    destroy_tk(root)
    return True


def destroy_tk(root: tk.Misc) -> None:
    """Destroy a Tk root on this thread and drop leftover Tcl callbacks."""
    try:
        info = str(root.tk.call("after", "info"))
        for item in info.split():
            try:
                root.after_cancel(item)
            except tk.TclError:
                pass
    except tk.TclError:
        pass
    try:
        root.destroy()
    except tk.TclError:
        pass
    try:
        root.update()
    except tk.TclError:
        pass
    gc.collect()
