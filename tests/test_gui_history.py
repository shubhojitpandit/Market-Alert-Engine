"""Offline tests for the read-only History tab."""

import inspect
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from tests.tk_helpers import (
    destroy_tk,
    isolate_gui_persistent_storage,
    tk_available,
)


def _walk(widget):
    found = [widget]
    for child in widget.winfo_children():
        found.extend(_walk(child))
    return found
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from models.alert import AlertCondition
from models.instrument import Instrument
from notifications.notification_queue import Notification

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_HISTORY_TEST"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
NIFTY = Instrument(NIFTY_KEY, "Nifty 50", "Nifty 50", "NSE", "NSE_INDEX", "INDEX")
TS = datetime(2026, 1, 1, 10, 2, 4, tzinfo=timezone.utc)
TS_NEWER = datetime(2026, 1, 1, 10, 3, 4, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_gui_stores(monkeypatch, tmp_path) -> None:
    isolate_gui_persistent_storage(monkeypatch, tmp_path)


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


def make_session(tmp_path) -> GuiSession:
    return GuiSession(
        credential_store=MemoryCredentialStore(),
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )


def test_history_list_reflects_alert_history(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    alert = session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    session.alert_engine.evaluate(PriceUpdate(NIFTY_KEY, 22520.45, TS))
    records = session.list_alert_history()
    assert records == session.alert_history.get_all()
    assert records[0].alert_id == alert.alert_id
    assert records[0].symbol == NIFTY_KEY
    assert records[0].current_price == 22520.45
    assert records[0].triggered_at == TS


def test_history_view_module_is_read_only() -> None:
    import gui.history_view as history_module

    source = inspect.getsource(history_module)
    assert "create_alert" not in source
    assert "cancel_alert" not in source
    assert "start_create_alert" not in source
    assert "evaluate" not in source
    assert "notification_queue" not in source
    assert "Delete" not in source


def _tk_available() -> bool:
    return tk_available()


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_history_view_displays_backend_fields(tmp_path) -> None:
    import tkinter as tk

    from gui.history_view import HistoryView

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    older_alert = session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    session.alert_engine.evaluate(PriceUpdate(NIFTY_KEY, 22520.45, TS))
    newer_alert = session.create_alert(NIFTY_KEY, 22600, AlertCondition.ABOVE)
    session.alert_engine.evaluate(PriceUpdate(NIFTY_KEY, 22620.45, TS_NEWER))
    records = session.list_alert_history()
    assert [record.alert_id for record in records] == [
        older_alert.alert_id,
        newer_alert.alert_id,
    ]
    root = tk.Tk()
    try:
        view = HistoryView(root, session)
        view.frame.pack(fill="both", expand=True)
        assert view._tree.get_children() == (
            newer_alert.alert_id,
            older_alert.alert_id,
        )
        values = view._tree.item(newer_alert.alert_id, "values")
        assert values[0] == newer_alert.alert_id
        assert values[1] == "Nifty 50"
        assert values[2] == "ABOVE"
        assert values[3] == "22600.00000"
        assert values[4] == "22620.45000"
        assert values[5] == "Live"
        assert values[6] == TS_NEWER.astimezone(
            timezone(timedelta(hours=5, minutes=30))
        ).strftime("%Y-%m-%d %H:%M:%S")
        assert FAKE_TOKEN not in str(values)
        button_texts = [
            child.cget("text")
            for child in _walk(view._frame)
            if getattr(child, "mae_button", False)
        ]
        assert button_texts == ["Refresh"]
    finally:
        destroy_tk(root)
