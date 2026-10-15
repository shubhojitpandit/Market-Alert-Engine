"""Offline tests for Market Watch PriceUpdate display (no network)."""

import math
import threading
import time
from datetime import datetime, timezone
from typing import Iterator

import pytest

from tests.tk_helpers import (
    destroy_tk,
    isolate_gui_persistent_storage,
    shutdown_app,
    tk_available,
)
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from gui.ui_queue import PriceQueued
from models.instrument import Instrument
from notifications.notification_queue import Notification

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_MARKET_WATCH_TEST"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
RELIANCE_KEY = "NSE_EQ|INE002A01018"
UNKNOWN_KEY = "NSE_EQ|UNKNOWN"
NIFTY = Instrument(NIFTY_KEY, "Nifty 50", "Nifty 50", "NSE", "NSE_INDEX", "INDEX")
RELIANCE = Instrument(
    RELIANCE_KEY, "RELIANCE INDUSTRIES LTD", "RELIANCE", "NSE", "NSE_EQ", "EQ"
)
DELTA_BTC_KEY = "delta:BTCUSD"
DELTA_BTC = Instrument(
    DELTA_BTC_KEY,
    "Bitcoin",
    "BTCUSD",
    "Delta Exchange India",
    "PERPETUAL",
    "CRYPTO",
)
TS_NIFTY = datetime(2026, 1, 1, 10, 2, 4, tzinfo=timezone.utc)
TS_RELIANCE = datetime(2026, 1, 1, 10, 2, 5, tzinfo=timezone.utc)
TS_NIFTY_NEXT = datetime(2026, 1, 1, 10, 2, 6, tzinfo=timezone.utc)


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


def _tk_available() -> bool:
    return tk_available()


def test_price_update_is_queued_through_session(tmp_path) -> None:
    session = make_session(tmp_path)
    update = PriceUpdate(NIFTY_KEY, 22520.45, TS_NIFTY)
    session.connection_manager._on_update(update)
    queued = session.ui_queue.drain()
    assert queued == [PriceQueued(update)]
    assert FAKE_TOKEN not in repr(queued[0])


def test_display_name_uses_saved_instrument_or_key(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    assert session.get_instrument_display_name(NIFTY_KEY) == "Nifty 50"
    assert session.get_instrument_display_name(UNKNOWN_KEY) == UNKNOWN_KEY


def test_disconnect_does_not_generate_price_updates(tmp_path) -> None:
    session = make_session(tmp_path)
    connection = session.create_connection(
        "Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN
    )
    session.ui_queue.drain()
    session.disconnect(connection.connection_id)
    assert session.ui_queue.drain() == []


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_market_watch_rows_and_empty_state(tmp_path) -> None:
    import tkinter as tk

    from gui.market_watch import MarketWatchView

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    root = tk.Tk()
    try:
        view = MarketWatchView(root, session)
        view.frame.pack(fill="both", expand=True)
        root.update_idletasks()
        assert view._empty.cget("text") == "NO ACTIVE MARKET DATA"
        assert view._has_data is False
        assert view._tree.get_children() == ()

        view.apply_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS_NIFTY))
        root.update_idletasks()
        assert view._has_data is True
        assert view._tree.get_children() == (NIFTY_KEY,)
        assert view._tree.item(NIFTY_KEY, "values") == (
            "Nifty 50",
            "22520.45",
            "NSE",
            "10:02:04",
            "LIVE",
        )

        view.apply_price_update(PriceUpdate(RELIANCE_KEY, 1170.30, TS_RELIANCE))
        assert view._tree.get_children() == (NIFTY_KEY, RELIANCE_KEY)
        assert view._tree.item(RELIANCE_KEY, "values") == (
            "RELIANCE",
            "1170.30",
            "NSE",
            "10:02:05",
            "LIVE",
        )

        view.apply_price_update(PriceUpdate(NIFTY_KEY, 22521.10, TS_NIFTY_NEXT))
        assert view._tree.get_children() == (NIFTY_KEY, RELIANCE_KEY)
        assert view._tree.item(NIFTY_KEY, "values") == (
            "Nifty 50",
            "22521.10",
            "NSE",
            "10:02:06",
            "LIVE",
        )
        assert view._tree.item(RELIANCE_KEY, "values") == (
            "RELIANCE",
            "1170.30",
            "NSE",
            "10:02:05",
            "LIVE",
        )
    finally:
        destroy_tk(root)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_unknown_instrument_falls_back_to_key(tmp_path) -> None:
    import tkinter as tk

    from gui.market_watch import MarketWatchView

    session = make_session(tmp_path)
    root = tk.Tk()
    try:
        view = MarketWatchView(root, session)
        view.apply_price_update(PriceUpdate(UNKNOWN_KEY, 1.5, TS_NIFTY))
        assert view._tree.item(UNKNOWN_KEY, "values")[0] == UNKNOWN_KEY
    finally:
        destroy_tk(root)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_malformed_update_does_not_crash_or_create_row(tmp_path) -> None:
    import tkinter as tk

    from gui.market_watch import MarketWatchView

    session = make_session(tmp_path)
    root = tk.Tk()
    try:
        view = MarketWatchView(root, session)
        view.apply_price_update(PriceUpdate("", 22520.45, TS_NIFTY))
        view.apply_price_update(PriceUpdate(NIFTY_KEY, math.nan, TS_NIFTY))
        view.apply_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS_NIFTY))
        broken = PriceUpdate(NIFTY_KEY, 1.0, TS_NIFTY)
        object.__setattr__(broken, "timestamp", "not-a-datetime")
        view.apply_price_update(broken)
        assert view._tree.get_children() == (NIFTY_KEY,)
    finally:
        destroy_tk(root)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_feed_thread_does_not_touch_tree_until_ui_drain(tmp_path) -> None:
    import tkinter as tk

    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    app = MarketAlertApp(session)
    try:
        view = app._market_watch_view
        assert view is not None
        app.root.update_idletasks()
        assert view._tree.get_children() == ()

        def post() -> None:
            session._handle_price_update(
                PriceUpdate(NIFTY_KEY, 22520.45, TS_NIFTY)
            )

        thread = threading.Thread(target=post)
        thread.start()
        thread.join(timeout=2)
        app.root.update_idletasks()
        assert view._tree.get_children() == ()
        app._schedule_queue_drain()
        app.root.update_idletasks()
        assert view._tree.get_children() == (NIFTY_KEY,)
        assert view._tree.item(NIFTY_KEY, "values")[1] == "22520.45"
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_app_market_watch_receives_queued_multi_instrument_updates(
    tmp_path,
) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    session.save_instrument(DELTA_BTC)
    app = MarketAlertApp(session)
    try:
        view = app._market_watch_view
        assert view is not None
        assert app.module_names == [
            "Market Watch",
            "Instruments",
            "Connections",
            "Alerts",
            "History",
        ]
        assert app._instruments_view is not None
        assert app._connections_view is not None
        assert "Search" in app._instruments_view._search_button.cget("text")
        assert app._connections_view._tree.winfo_exists()

        app.root.update_idletasks()
        assert view._empty.cget("text") == "NO ACTIVE MARKET DATA"
        assert view._has_data is False

        session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS_NIFTY))
        session._handle_price_update(
            PriceUpdate(RELIANCE_KEY, 1170.30, TS_RELIANCE)
        )
        session._handle_price_update(
            PriceUpdate(NIFTY_KEY, 22521.10, TS_NIFTY_NEXT)
        )
        session._handle_price_update(
            PriceUpdate(DELTA_BTC_KEY, 65000, TS_RELIANCE)
        )
        app._schedule_queue_drain()
        app.root.update_idletasks()
        assert view._tree.get_children() == (
            NIFTY_KEY,
            RELIANCE_KEY,
            DELTA_BTC_KEY,
        )
        assert view._tree.item(NIFTY_KEY, "values") == (
            "Nifty 50",
            "22521.10",
            "NSE",
            "10:02:06",
            "LIVE",
        )
        assert view._tree.item(RELIANCE_KEY, "values") == (
            "RELIANCE",
            "1170.30",
            "NSE",
            "10:02:05",
            "LIVE",
        )
        assert view._tree.item(DELTA_BTC_KEY, "values") == (
            "BTCUSD",
            "65000.00",
            "Delta Exchange India",
            "10:02:05",
            "LIVE",
        )
        assert FAKE_TOKEN not in str(view._tree.item(NIFTY_KEY, "values"))
    finally:
        shutdown_app(app)
