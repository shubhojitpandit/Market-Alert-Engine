"""Offline tests for GUI alert create/cancel (no network, no tokens)."""

import inspect
import threading
import time
from datetime import datetime, timezone
from typing import Iterator

import pytest

from tests.tk_helpers import destroy_tk, tk_available
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from gui.ui_queue import AlertActionQueued
from models.alert import AlertCondition, AlertStatus
from models.instrument import Instrument
from notifications.notification_queue import Notification

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_ALERTS_TEST"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
RELIANCE_KEY = "NSE_EQ|INE002A01018"
NIFTY = Instrument(NIFTY_KEY, "Nifty 50", "Nifty 50", "NSE", "NSE_INDEX", "INDEX")
RELIANCE = Instrument(
    RELIANCE_KEY, "RELIANCE INDUSTRIES LTD", "RELIANCE", "NSE", "NSE_EQ", "EQ"
)
TS = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


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
    def __init__(self) -> None:
        self.connect_calls = 0

    def connect(self, instrument: object, credential: str) -> None:
        self.connect_calls += 1

    def disconnect(self) -> None:
        return None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        return iter(())


def wait_for_messages(session: GuiSession, *, timeout: float = 2) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = session.ui_queue.drain()
        if messages:
            return messages
        time.sleep(0.01)
    return []


def make_session(tmp_path, *, feed: FakeManagedFeed | None = None) -> GuiSession:
    pending = [feed] if feed is not None else []

    def create_feed() -> FakeManagedFeed:
        if pending:
            return pending.pop(0)
        return FakeManagedFeed()

    return GuiSession(
        credential_store=MemoryCredentialStore(),
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories={"upstox": create_feed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )


def test_create_and_list_delegate_to_alert_manager(tmp_path) -> None:
    session = make_session(tmp_path)
    created: list[str] = []
    original = session.alert_manager.create_alert

    def tracking_create(symbol, target_price, condition):
        created.append(symbol)
        return original(symbol, target_price, condition)

    session.alert_manager.create_alert = tracking_create  # type: ignore[method-assign]
    session.start_create_alert(NIFTY_KEY, "22500", "CROSS_ABOVE")
    messages = wait_for_messages(session)
    session.start_create_alert(RELIANCE_KEY, "1170", "BELOW")
    messages.extend(wait_for_messages(session))
    assert created == [NIFTY_KEY, RELIANCE_KEY]
    alerts = session.list_active_alerts()
    assert [alert.symbol for alert in alerts] == [NIFTY_KEY, RELIANCE_KEY]
    assert [alert.condition for alert in alerts] == [
        AlertCondition.CROSS_ABOVE,
        AlertCondition.BELOW,
    ]
    assert [alert.target_price for alert in alerts] == [22500.0, 1170.0]
    assert all(alert.status is AlertStatus.ACTIVE for alert in alerts)
    assert all(isinstance(message, AlertActionQueued) for message in messages)
    assert all(FAKE_TOKEN not in message.message for message in messages)


def test_cancel_delegates_to_alert_manager_and_keeps_history(tmp_path) -> None:
    session = make_session(tmp_path)
    first = session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    second = session.create_alert(RELIANCE_KEY, 1170, AlertCondition.BELOW)
    session.alert_engine.evaluate(PriceUpdate(NIFTY_KEY, 22501, TS))
    assert session.list_alert_history()[0].alert_id == first.alert_id
    cancelled: list[str] = []
    original = session.alert_manager.cancel_alert

    def tracking_cancel(alert_id: str):
        cancelled.append(alert_id)
        return original(alert_id)

    session.alert_manager.cancel_alert = tracking_cancel  # type: ignore[method-assign]
    session.start_cancel_alert(second.alert_id)
    wait_for_messages(session)
    assert cancelled == [second.alert_id]
    assert [alert.alert_id for alert in session.list_active_alerts()] == []
    assert [record.alert_id for record in session.list_alert_history()] == [
        first.alert_id
    ]


def test_invalid_threshold_and_condition_rejected(tmp_path) -> None:
    session = make_session(tmp_path)
    with pytest.raises(ValueError, match="number"):
        session.start_create_alert(NIFTY_KEY, "abc", "ABOVE")
    with pytest.raises(ValueError, match="unsupported alert condition"):
        session.start_create_alert(NIFTY_KEY, "22500", "SIDEWAYS")
    with pytest.raises(ValueError, match="instrument"):
        session.start_create_alert("  ", "22500", "ABOVE")
    assert session.list_active_alerts() == []
    assert session.ui_queue.drain() == []


def test_create_does_not_start_a_feed(tmp_path) -> None:
    feed = FakeManagedFeed()
    session = make_session(tmp_path, feed=feed)
    session.start_create_alert(NIFTY_KEY, "22500", "ABOVE")
    wait_for_messages(session)
    assert feed.connect_calls == 0
    assert session.list_connections() == []


def test_gui_does_not_evaluate_or_double_process_alerts(tmp_path) -> None:
    import gui.alerts_view as alerts_module

    source = inspect.getsource(alerts_module)
    assert "alert_engine" not in source
    assert "CROSS_ABOVE" in source
    assert "previous_price" not in source
    session = make_session(tmp_path)
    session.create_alert(NIFTY_KEY, 22500, AlertCondition.ABOVE)
    calls = {"count": 0}
    original = session.alert_engine.evaluate

    def counting(update: PriceUpdate):
        calls["count"] += 1
        return original(update)

    session.alert_engine.evaluate = counting  # type: ignore[method-assign]
    session._handle_price_update(PriceUpdate(NIFTY_KEY, 22501, TS))
    assert calls["count"] == 1
    assert session.list_active_alerts() == []
    assert session.list_alert_history()[0].current_price == 22501
    assert session.list_alert_history()[0].triggered_at == TS


def test_create_alert_errors_are_sanitized(tmp_path) -> None:
    session = make_session(tmp_path)
    session.start_create_alert(NIFTY_KEY, "-1", "ABOVE")
    messages = wait_for_messages(session)
    assert messages[0].action == "create"
    assert FAKE_TOKEN not in messages[0].message
    assert "positive" in messages[0].message.lower() or "number" in messages[0].message.lower()


def _tk_available() -> bool:
    return tk_available()


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_alerts_view_create_cancel_refresh(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    app = MarketAlertApp(session)
    try:
        view = app._alerts_view
        assert view is not None
        view._confirm = lambda _title, _message: True
        view.refresh()
        view._instrument.current(0)
        view._condition.set("CROSS_ABOVE")
        view._threshold.insert(0, "22500")
        view.create_alert()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if session.list_active_alerts():
                break
            time.sleep(0.02)
        view._instrument.current(1)
        view._condition.set("BELOW")
        view._threshold.insert(0, "1170")
        view.create_alert()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if len(session.list_active_alerts()) == 2:
                break
            time.sleep(0.02)
        ids = view._tree.get_children()
        assert len(ids) == 2
        assert view._tree.item(ids[0], "values")[1] == "Nifty 50"
        assert view._tree.item(ids[0], "values")[2] == "CROSS_ABOVE"
        assert view._tree.item(ids[1], "values")[1] == "RELIANCE INDUSTRIES LTD"
        assert FAKE_TOKEN not in view._status.cget("text")

        view._tree.selection_set(ids[1])
        view.cancel_selected()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if len(session.list_active_alerts()) == 1:
                break
            time.sleep(0.02)
        assert view._tree.get_children() == (ids[0],)
        assert app._history_view is not None
        assert app._market_watch_view is not None
        assert app._instruments_view is not None
        assert app._connections_view is not None
    finally:
        app._on_close()
        destroy_tk(app.root)
