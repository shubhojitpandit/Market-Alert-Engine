"""Regression tests for GUI Slice 7B lifecycle hardening."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import Mock

import pytest

from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from gui.ui_queue import AlertActionQueued, ConnectionActionQueued, PriceQueued
from models.alert import AlertCondition
from models.instrument import Instrument
from notifications.notification_queue import Notification
from tests.tk_helpers import (
    isolate_gui_persistent_storage,
    shutdown_app,
    tk_available,
)

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_HARDENING_TEST"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
RELIANCE_KEY = "NSE_EQ|INE002A01018"
TS = datetime(2026, 1, 1, 10, 2, 4, tzinfo=timezone.utc)
NIFTY = Instrument(NIFTY_KEY, "Nifty 50", "Nifty 50", "NSE", "NSE_INDEX", "INDEX")
RELIANCE = Instrument(
    RELIANCE_KEY, "RELIANCE INDUSTRIES LTD", "RELIANCE", "NSE", "NSE_EQ", "EQ"
)


@pytest.fixture(autouse=True)
def _isolate_gui_stores(monkeypatch, tmp_path) -> None:
    isolate_gui_persistent_storage(monkeypatch, tmp_path)


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.save_calls = 0

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.save_calls += 1
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        return self.values[(provider, credential_id)]

    def delete(self, provider: str, credential_id: str) -> None:
        del self.values[(provider, credential_id)]


class SilentNotificationHandler:
    def __init__(self) -> None:
        self.calls = 0

    def handle(self, notification: Notification) -> None:
        del notification
        self.calls += 1


class FakeManagedFeed:
    def __init__(
        self,
        *,
        blocker: threading.Event | None = None,
        disconnect_delay: float = 0,
    ) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.blocker = blocker
        self.disconnect_delay = disconnect_delay
        self._closed = threading.Event()

    def connect(self, instrument: object, credential: str) -> None:
        del instrument, credential
        self.connect_calls += 1
        if self.blocker is not None:
            self.blocker.wait(timeout=2)

    def disconnect(self) -> None:
        if self.disconnect_delay:
            time.sleep(self.disconnect_delay)
        self.disconnect_calls += 1
        self._closed.set()

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        self._closed.wait(timeout=2)
        return
        yield


def make_session(
    tmp_path,
    *,
    feeds: list[FakeManagedFeed] | None = None,
    notification_handler: SilentNotificationHandler | None = None,
) -> GuiSession:
    pending = list(feeds or [])

    def create_feed() -> FakeManagedFeed:
        if pending:
            return pending.pop(0)
        return FakeManagedFeed()

    return GuiSession(
        credential_store=MemoryCredentialStore(),
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories={"upstox": create_feed},
        registry_path=tmp_path / "connections.json",
        notification_handler=notification_handler or SilentNotificationHandler(),
    )


def wait_until(predicate, *, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def collect_messages(session: GuiSession, *, timeout: float = 0.4) -> list:
    collected: list = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        collected.extend(session.ui_queue.drain())
        time.sleep(0.02)
    return collected


def test_closed_session_discards_worker_result(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    started = threading.Event()
    release = threading.Event()
    original = session.connection_manager.create_connection

    def blocked_create(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        return original(*args, **kwargs)

    session.connection_manager.create_connection = blocked_create  # type: ignore[method-assign]
    request_id = session.create_upstox_connection(
        "Watchlist", (NIFTY_KEY,), FAKE_TOKEN
    )
    assert request_id != 0
    assert started.wait(timeout=2)
    session.close()
    release.set()
    time.sleep(0.15)
    assert session.ui_queue.drain() == []
    assert session.is_closed()


def test_feed_callback_after_close_does_not_enqueue(tmp_path) -> None:
    session = make_session(tmp_path)
    session.close()
    session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS))
    assert session.ui_queue.pending_price_count() == 0
    assert session.ui_queue.drain() == []


def test_create_connection_after_close_does_not_call_backend(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.close()
    session.connection_manager.create_connection = Mock()  # type: ignore[method-assign]
    assert session.create_upstox_connection("Watchlist", (NIFTY_KEY,), FAKE_TOKEN) == 0
    with pytest.raises(Exception):
        session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    session.connection_manager.create_connection.assert_not_called()
    assert session.credential_store.save_calls == 0


def test_connect_after_close_does_not_call_backend(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    connection = session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    session.close()
    session.connection_manager.connect = Mock()  # type: ignore[method-assign]
    assert session.connect_connection(connection.connection_id) == 0
    with pytest.raises(Exception):
        session.connect(connection.connection_id)
    session.connection_manager.connect.assert_not_called()


def test_alert_action_after_close_does_not_call_backend(tmp_path) -> None:
    session = make_session(tmp_path)
    session.close()
    session.alert_manager.create_alert = Mock()  # type: ignore[method-assign]
    session.alert_manager.cancel_alert = Mock()  # type: ignore[method-assign]
    assert session.start_create_alert(NIFTY_KEY, 22500, "ABOVE") == 0
    assert session.start_cancel_alert("alert-1") == 0
    session.alert_manager.create_alert.assert_not_called()
    session.alert_manager.cancel_alert.assert_not_called()


def test_close_is_idempotent(tmp_path) -> None:
    session = make_session(tmp_path)
    session.close()
    session.close()
    assert session.is_closed()


def test_duplicate_connect_while_connecting_is_ignored(tmp_path) -> None:
    blocker = threading.Event()
    feed = FakeManagedFeed(blocker=blocker)
    session = make_session(tmp_path, feeds=[feed])
    session.save_instrument(NIFTY)
    connection = session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    first = session.connect_connection(connection.connection_id)
    assert first != 0
    assert wait_until(lambda: feed.connect_calls == 1)
    assert session.connect_connection(connection.connection_id) == 0
    assert feed.connect_calls == 1
    blocker.set()
    assert wait_until(lambda: session.get_connection_status(connection.connection_id).state == "connected")
    session.close()


def test_delete_during_connect_does_not_resurrect_feed(tmp_path) -> None:
    blocker = threading.Event()
    feed = FakeManagedFeed(blocker=blocker)
    session = make_session(tmp_path, feeds=[feed])
    session.save_instrument(NIFTY)
    connection = session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    cid = connection.connection_id
    session.connect_connection(cid)
    assert wait_until(lambda: feed.connect_calls == 1)
    assert session.start_delete_connection(cid) == 0
    blocker.set()
    assert wait_until(lambda: not session.list_connections())
    messages = collect_messages(session, timeout=0.3)
    connected = [
        item
        for item in messages
        if isinstance(item, ConnectionActionQueued)
        and item.action == "connect"
        and "Connected" in item.message
    ]
    assert connected == []
    assert feed.disconnect_calls >= 1
    assert session.list_connections() == []
    session.close()


def test_cancel_and_evaluate_do_not_fail_the_feed(tmp_path) -> None:
    handler = SilentNotificationHandler()
    session = make_session(tmp_path, notification_handler=handler)
    session.save_instrument(NIFTY)
    connection = session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    session.connect(connection.connection_id)
    alert = session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    original = session.alert_engine.evaluate
    evaluate_calls = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def slow_evaluate(update: PriceUpdate):
        evaluate_calls["n"] += 1
        started.set()
        release.wait(timeout=2)
        return original(update)

    session.alert_engine.evaluate = slow_evaluate  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def run_price() -> None:
        try:
            session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS))
        except BaseException as error:
            errors.append(error)

    price_thread = threading.Thread(target=run_price)
    price_thread.start()
    assert started.wait(timeout=2)
    cancel_id = session.start_cancel_alert(alert.alert_id)
    assert cancel_id != 0
    time.sleep(0.05)
    release.set()
    price_thread.join(timeout=2)
    assert wait_until(lambda: not session.list_active_alerts())
    leftover = collect_messages(session, timeout=0.2)
    assert errors == []
    assert evaluate_calls["n"] == 1
    assert handler.calls <= 1
    assert session.get_connection_status(connection.connection_id).state == "connected"
    del leftover
    session.close()


def test_price_queue_coalesces_per_instrument_but_evaluates_every_tick(tmp_path) -> None:
    session = make_session(tmp_path)
    original = session.alert_engine.evaluate
    evaluate_calls = {"n": 0}

    def counting(update: PriceUpdate):
        evaluate_calls["n"] += 1
        return original(update)

    session.alert_engine.evaluate = counting  # type: ignore[method-assign]
    for index in range(20):
        session._handle_price_update(
            PriceUpdate(NIFTY_KEY, 22500 + index, TS)
        )
        session._handle_price_update(
            PriceUpdate(RELIANCE_KEY, 1170 + index, TS)
        )
    assert evaluate_calls["n"] == 40
    assert session.ui_queue.pending_price_count() == 2
    queued = session.ui_queue.drain()
    prices = [item.update for item in queued if isinstance(item, PriceQueued)]
    instruments = {item.instrument for item in prices}
    assert instruments == {NIFTY_KEY, RELIANCE_KEY}
    assert len(prices) == 2
    by_key = {item.instrument: item.price for item in prices}
    assert by_key[NIFTY_KEY] == 22519
    assert by_key[RELIANCE_KEY] == 1189
    session.close()


def test_triggered_alert_still_enqueues_history_refresh(tmp_path) -> None:
    session = make_session(tmp_path)
    session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS))
    messages = session.ui_queue.drain()
    actions = [item for item in messages if isinstance(item, AlertActionQueued)]
    assert any(item.action == "trigger" for item in actions)
    assert session.list_alert_history()
    session.close()


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_queue_drain_survives_refresh_exception(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    app = MarketAlertApp(session)
    try:
        view = app._market_watch_view
        assert view is not None
        original = view.handle_ui_message

        def boom(_message) -> None:
            raise RuntimeError("refresh failed")

        view.handle_ui_message = boom  # type: ignore[method-assign]
        session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS))
        app._schedule_queue_drain()
        assert app._running is True
        assert app._after_id is not None
        view.handle_ui_message = original  # type: ignore[method-assign]
        later = PriceUpdate(NIFTY_KEY, 22521.10, TS)
        session._handle_price_update(later)
        app._schedule_queue_drain()
        app.root.update_idletasks()
        assert view._tree.get_children() == (NIFTY_KEY,)
        assert view._tree.item(NIFTY_KEY, "values")[1] == "22521.10"
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_on_close_does_not_wait_for_slow_disconnect(tmp_path) -> None:
    from gui.app import MarketAlertApp

    feed = FakeManagedFeed(disconnect_delay=1.5)
    session = make_session(tmp_path, feeds=[feed])
    session.save_instrument(NIFTY)
    connection = session.create_connection("Watchlist", "upstox", NIFTY_KEY, FAKE_TOKEN)
    session.connect(connection.connection_id)
    close_started = {"n": 0}
    original_close = session.connection_manager.close

    def tracking_close() -> None:
        close_started["n"] += 1
        original_close()

    session.connection_manager.close = tracking_close  # type: ignore[method-assign]
    app = MarketAlertApp(session)
    try:
        started = time.monotonic()
        app._shutdown()
        elapsed = time.monotonic() - started
        assert elapsed < 0.8
        assert session.is_closed()
        assert close_started["n"] == 1
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_alert_and_history_are_not_rebuilt_per_price_tick(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    app = MarketAlertApp(session)
    try:
        alerts = app._alerts_view
        history = app._history_view
        assert alerts is not None and history is not None
        alert_refreshes = {"n": 0}
        history_refreshes = {"n": 0}
        original_alerts = alerts.refresh
        original_history = history.refresh

        def count_alerts() -> None:
            alert_refreshes["n"] += 1
            original_alerts()

        def count_history() -> None:
            history_refreshes["n"] += 1
            original_history()

        alerts.refresh = count_alerts  # type: ignore[method-assign]
        history.refresh = count_history  # type: ignore[method-assign]
        alert_refreshes["n"] = 0
        history_refreshes["n"] = 0
        for index in range(8):
            session._handle_price_update(PriceUpdate(NIFTY_KEY, 22400 + index, TS))
        app._schedule_queue_drain()
        app.root.update_idletasks()
        assert alert_refreshes["n"] == 0
        assert history_refreshes["n"] == 0
        session._handle_price_update(PriceUpdate(NIFTY_KEY, 22520.45, TS))
        app._schedule_queue_drain()
        app.root.update_idletasks()
        assert history_refreshes["n"] == 1
        assert history._tree.get_children()
        assert app._market_watch_view is not None
        assert app._market_watch_view._tree.get_children() == (NIFTY_KEY,)
    finally:
        shutdown_app(app)
