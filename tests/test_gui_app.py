"""Minimal import and shell checks for the Tkinter GUI entry (no screenshots)."""

from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import Mock

import pytest

import gui.app as app_module
from tests.tk_helpers import destroy_tk, tk_available
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from notifications.notification_queue import Notification

NIFTY_KEY = "NSE_INDEX|Nifty 50"


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        return self.values[(provider, credential_id)]

    def delete(self, provider: str, credential_id: str) -> None:
        del self.values[(provider, credential_id)]


class SilentNotificationHandler:
    def handle(self, notification: Notification) -> None:
        del notification


class FakeManagedFeed:
    def connect(self, instrument: object, credential: str) -> None:
        del instrument, credential

    def disconnect(self) -> None:
        return None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        return iter(())


def _tk_available() -> bool:
    return tk_available()


def test_autostart_toggle_updates_after_success(monkeypatch) -> None:
    app = app_module.MarketAlertApp.__new__(app_module.MarketAlertApp)
    app._autostart_enabled = False
    app._autostart_switch = Mock()
    app.root = Mock()
    calls: list[bool] = []
    monkeypatch.setattr(
        app_module, "set_windows_autostart", lambda enabled: calls.append(enabled)
    )

    assert app._toggle_autostart() == "break"

    assert calls == [True]
    assert app._autostart_enabled is True
    app._autostart_switch.create_text.assert_called_once()


def test_autostart_toggle_preserves_state_and_reports_failure(monkeypatch) -> None:
    app = app_module.MarketAlertApp.__new__(app_module.MarketAlertApp)
    app._autostart_enabled = True
    app._autostart_switch = Mock()
    app.root = Mock()
    errors: list[tuple[object, ...]] = []

    def fail_to_set(_enabled: bool) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(app_module, "set_windows_autostart", fail_to_set)
    monkeypatch.setattr(
        app_module.messagebox, "showerror", lambda *args, **kwargs: errors.append(args)
    )

    assert app._toggle_autostart() == "break"

    assert app._autostart_enabled is True
    assert errors and errors[0][0] == "Autostart"
    app._autostart_switch.create_text.assert_not_called()


def test_gui_package_and_entry_import() -> None:
    import gui
    import gui.app
    import gui.__main__

    assert gui.GuiSession is GuiSession
    assert callable(gui.app.main)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_shell_drains_queue_on_ui_thread_and_closes_session(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = GuiSession(
        credential_store=MemoryCredentialStore(),
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )
    close_calls = {"count": 0}
    original_close = session.close

    def tracking_close(*args, **kwargs) -> None:
        close_calls["count"] += 1
        original_close(*args, **kwargs)

    session.close = tracking_close  # type: ignore[method-assign]
    app = MarketAlertApp(session)
    update = PriceUpdate(
        NIFTY_KEY, 22520.45, datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    session.ui_queue.put_price_update(update)
    app._schedule_queue_drain()
    assert session.ui_queue.drain() == []
    app._on_close()
    destroy_tk(app.root)
    assert close_calls["count"] == 1
