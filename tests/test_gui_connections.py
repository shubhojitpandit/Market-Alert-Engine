"""Offline tests for GUI connection create/connect/disconnect/delete."""

import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Iterator

import pytest

from tests.tk_helpers import (
    isolate_gui_persistent_storage,
    shutdown_app,
    tk_available,
)
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore, CredentialStoreError
from data.instrument_store import InstrumentStore
from gui.session import GuiSession
from gui.ui_queue import ConnectionActionQueued
from models.alert import AlertCondition
from models.instrument import Instrument
from notifications.notification_queue import Notification

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_CONNECTIONS_TEST"
RELIANCE = Instrument(
    "NSE_EQ|INE002A01018",
    "RELIANCE INDUSTRIES LTD",
    "RELIANCE",
    "NSE",
    "NSE_EQ",
    "EQ",
)
NIFTY = Instrument(
    "NSE_INDEX|Nifty 50",
    "Nifty 50",
    "Nifty 50",
    "NSE",
    "NSE_INDEX",
    "INDEX",
)
DELTA_BTC = Instrument(
    "delta:BTCUSD",
    "Bitcoin",
    "BTCUSD",
    "Delta Exchange",
    "PERPETUAL",
    "CRYPTO",
)


@pytest.fixture(autouse=True)
def _isolate_gui_stores(monkeypatch, tmp_path) -> None:
    isolate_gui_persistent_storage(monkeypatch, tmp_path)


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.delete_calls: list[tuple[str, str]] = []

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        try:
            return self.values[(provider, credential_id)]
        except KeyError:
            raise CredentialStoreError("No credential was found.") from None

    def delete(self, provider: str, credential_id: str) -> None:
        self.delete_calls.append((provider, credential_id))
        del self.values[(provider, credential_id)]


class SilentNotificationHandler:
    def handle(self, notification: Notification) -> None:
        del notification


class FakeManagedFeed:
    def __init__(
        self,
        *,
        blocker: threading.Event | None = None,
        fail_connect: str | None = None,
    ) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.instruments: tuple[str, ...] = ()
        self.blocker = blocker
        self.fail_connect = fail_connect
        self._closed = threading.Event()

    def connect(self, instrument: object, credential: str) -> None:
        self.connect_calls += 1
        if isinstance(instrument, str):
            self.instruments = (instrument,)
        else:
            self.instruments = tuple(instrument)
        if self.fail_connect is not None:
            raise RuntimeError(self.fail_connect)
        if self.blocker is not None:
            self.blocker.wait(timeout=2)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._closed.set()

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        self._closed.wait(timeout=2)
        return
        yield


def wait_for_messages(session: GuiSession, *, timeout: float = 2) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = session.ui_queue.drain()
        if messages:
            return messages
        time.sleep(0.01)
    return []


def make_session(
    tmp_path,
    *,
    feeds: list[FakeManagedFeed] | None = None,
    feed_factories: Mapping[str, Callable[[], object]] | None = None,
    credential_store: MemoryCredentialStore | None = None,
) -> GuiSession:
    pending = list(feeds or [])

    def create_feed() -> FakeManagedFeed:
        if pending:
            return pending.pop(0)
        return FakeManagedFeed()

    return GuiSession(
        credential_store=credential_store or MemoryCredentialStore(),
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories=feed_factories
        or {"upstox": create_feed, "delta": create_feed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )


def session_owned_strings(session: GuiSession) -> list[str]:
    skipped = {
        "credential_store",
        "connection_manager",
        "instrument_store",
        "alert_manager",
        "alert_engine",
        "alert_history",
        "notification_queue",
        "notification_handler",
        "simulated_feed",
        "ui_queue",
        "_resolver_factory",
        "_search_lock",
        "_lifecycle_lock",
    }
    values: list[str] = []
    for name, value in vars(session).items():
        if name in skipped:
            continue
        if isinstance(value, str):
            values.append(value)
    return values


def test_list_connections_reads_from_connection_manager(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    created = session.create_connection(
        "Watchlist", "upstox", NIFTY.instrument_key, FAKE_TOKEN
    )
    listed = session.list_connections()
    assert listed == session.connection_manager.list_connections()
    assert listed[0].connection_id == created.connection_id
    assert listed[0].name == "Watchlist"


def test_create_upstox_connection_passes_selected_keys(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    request_id = session.create_upstox_connection(
        "Upstox Market Watch",
        (NIFTY.instrument_key, RELIANCE.instrument_key),
        FAKE_TOKEN,
    )
    messages = wait_for_messages(session)
    assert messages
    assert isinstance(messages[0], ConnectionActionQueued)
    assert messages[0].request_id == request_id
    assert messages[0].action == "create"
    assert FAKE_TOKEN not in messages[0].message
    connection = session.list_connections()[0]
    assert connection.provider == "upstox"
    assert connection.instruments == (
        NIFTY.instrument_key,
        RELIANCE.instrument_key,
    )
    assert connection.instrument == NIFTY.instrument_key
    assert session.connection_instrument_labels(connection) == (
        "Nifty 50, RELIANCE INDUSTRIES LTD"
    )


def test_mixed_saved_instruments_create_and_connect_separate_provider_feeds(
    tmp_path,
) -> None:
    upstox_feed = FakeManagedFeed()
    delta_feed = FakeManagedFeed()

    class DeltaFeedFactory:
        requires_credentials = False

        def __call__(self) -> FakeManagedFeed:
            return delta_feed

    session = make_session(
        tmp_path,
        feed_factories={
            "upstox": lambda: upstox_feed,
            "delta": DeltaFeedFactory(),
        },
    )
    session.save_instrument(NIFTY)
    session.save_instrument(DELTA_BTC)
    session.save_upstox_credential(FAKE_TOKEN)

    session.create_connections_for_instruments(
        "Market Watch",
        (NIFTY.instrument_key, DELTA_BTC.instrument_key),
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        connections = session.list_connections()
        if len(connections) == 2:
            break
        time.sleep(0.01)

    connections = session.list_connections()
    assert {connection.provider for connection in connections} == {
        "upstox",
        "delta",
    }
    assert {
        connection.provider: connection.subscribed_instruments()
        for connection in connections
    } == {
        "upstox": (NIFTY.instrument_key,),
        "delta": (DELTA_BTC.instrument_key,),
    }

    for connection in connections:
        session.connect_connection(connection.connection_id)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if all(
            session.get_connection_status(connection.connection_id).state
            == "connected"
            for connection in connections
        ):
            break
        time.sleep(0.01)
    assert {
        connection.provider
        for connection in connections
        if session.get_connection_status(connection.connection_id).state
        == "connected"
    } == {"upstox", "delta"}
    assert upstox_feed.connect_calls == 1
    assert upstox_feed.instruments == (NIFTY.instrument_key,)
    assert delta_feed.connect_calls == 1
    assert delta_feed.instruments == (DELTA_BTC.instrument_key,)

    timestamp = datetime.now(timezone.utc)
    session._handle_price_update(
        PriceUpdate(NIFTY.instrument_key, 22501, timestamp)
    )
    session._handle_price_update(
        PriceUpdate(DELTA_BTC.instrument_key, 65001, timestamp)
    )
    session.create_alert(NIFTY.instrument_key, 22500, AlertCondition.ABOVE)
    session.create_alert(
        DELTA_BTC.instrument_key, 65000, AlertCondition.ABOVE
    )
    assert {
        record.symbol for record in session.list_alert_history()
    } == {NIFTY.instrument_key, DELTA_BTC.instrument_key}
    session.close()


def test_empty_name_and_no_instruments_are_rejected(tmp_path) -> None:
    session = make_session(tmp_path)
    with pytest.raises(ValueError, match="connection name"):
        session.create_upstox_connection(
            "  ", (NIFTY.instrument_key,), FAKE_TOKEN
        )
    with pytest.raises(ValueError, match="saved instruments"):
        session.create_upstox_connection("Watchlist", (), FAKE_TOKEN)
    with pytest.raises(ValueError, match="saved instruments"):
        session.create_upstox_connection("Watchlist", ("  ",), FAKE_TOKEN)
    assert session.list_connections() == []
    assert session.ui_queue.drain() == []


def test_create_does_not_start_a_feed(tmp_path) -> None:
    feed = FakeManagedFeed()
    session = make_session(tmp_path, feeds=[feed])
    session.create_upstox_connection(
        "Watchlist", (NIFTY.instrument_key,), FAKE_TOKEN
    )
    wait_for_messages(session)
    assert feed.connect_calls == 0
    assert session.get_connection_status(
        session.list_connections()[0].connection_id
    ).state == "disconnected"


def test_connect_and_disconnect_are_asynchronous(tmp_path) -> None:
    blocker = threading.Event()
    feed = FakeManagedFeed(blocker=blocker)
    session = make_session(tmp_path, feeds=[feed, FakeManagedFeed()])
    connection = session.create_connection(
        "Watchlist", "upstox", NIFTY.instrument_key, FAKE_TOKEN
    )
    started = time.monotonic()
    session.connect_connection(connection.connection_id)
    assert time.monotonic() - started < 0.2
    assert session.ui_queue.drain() == []
    blocker.set()
    messages = wait_for_messages(session)
    assert messages[0].action == "connect"
    assert messages[0].message == "Connected"
    assert session.get_connection_status(connection.connection_id).state == (
        "connected"
    )

    session.disconnect_connection(connection.connection_id)
    messages = wait_for_messages(session)
    assert messages[0].action == "disconnect"
    assert session.get_connection_status(connection.connection_id).state == (
        "disconnected"
    )
    assert session.credential_store.retrieve(
        "upstox", connection.connection_id
    ) == FAKE_TOKEN


def test_delete_uses_connection_manager_and_keeps_instruments(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    connection = session.create_connection(
        "Watchlist",
        "upstox",
        NIFTY.instrument_key,
        FAKE_TOKEN,
        instruments=(NIFTY.instrument_key, RELIANCE.instrument_key),
    )
    session.start_delete_connection(connection.connection_id)
    messages = wait_for_messages(session)
    assert messages[0].action == "delete"
    assert session.list_connections() == []
    assert session.list_saved_instruments() == [NIFTY, RELIANCE]
    assert session.credential_store.delete_calls == [
        ("upstox", connection.connection_id)
    ]


def test_create_does_not_retain_token_on_session_or_queue(tmp_path) -> None:
    session = make_session(tmp_path)
    session.create_upstox_connection(
        "Watchlist", (NIFTY.instrument_key,), FAKE_TOKEN
    )
    messages = wait_for_messages(session)
    assert FAKE_TOKEN not in session_owned_strings(session)
    assert FAKE_TOKEN not in vars(session).values()
    assert all(FAKE_TOKEN not in repr(message) for message in messages)
    stored = session.credential_store.retrieve(
        "upstox", session.list_connections()[0].connection_id
    )
    assert stored == FAKE_TOKEN


def test_connection_errors_are_sanitized(tmp_path) -> None:
    feed = FakeManagedFeed(fail_connect=f"boom {FAKE_TOKEN}")
    session = make_session(tmp_path, feeds=[feed])
    connection = session.create_connection(
        "Watchlist", "upstox", NIFTY.instrument_key, FAKE_TOKEN
    )
    session.connect_connection(connection.connection_id)
    messages = wait_for_messages(session)
    assert isinstance(messages[0], ConnectionActionQueued)
    assert FAKE_TOKEN not in messages[0].message
    assert FAKE_TOKEN not in repr(messages[0])
    status = session.get_connection_status(connection.connection_id)
    assert status.state == "error"
    assert FAKE_TOKEN not in (status.error or "")
    assert FAKE_TOKEN not in session.connection_status_text(status)


def _tk_available() -> bool:
    return tk_available()


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_connections_view_controls_create_connect_disconnect_delete(
    tmp_path,
) -> None:
    import tkinter as tk

    from gui.app import MarketAlertApp

    feed = FakeManagedFeed()
    session = make_session(tmp_path, feeds=[feed, FakeManagedFeed()])
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    confirms: list[str] = []

    app = MarketAlertApp(session)
    try:
        view = app._connections_view
        assert view is not None
        view._confirm = lambda _title, message: confirms.append(message) or True
        assert app.module_names == [
            "Market Watch",
            "Instruments",
            "Connections",
            "Alerts",
            "History",
        ]
        assert "Create Connection" in [
            child.cget("text")
            for child in view._buttons.winfo_children()
            if getattr(child, "mae_button", False)
        ]

        session.create_upstox_connection(
            "Upstox Market Watch",
            (NIFTY.instrument_key, RELIANCE.instrument_key),
            FAKE_TOKEN,
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if session.list_connections():
                break
            time.sleep(0.02)
        view.refresh()
        connection_id = session.list_connections()[0].connection_id
        assert connection_id in view._tree.get_children()
        values = view._tree.item(connection_id, "values")
        assert values[0] == "Upstox Market Watch"
        assert values[1] == "INDIAN MARKET"
        assert "Nifty 50" in values[2]
        assert "RELIANCE" in values[2]
        assert values[3] == "● DISCONNECTED"
        assert FAKE_TOKEN not in view._status.cget("text")

        view._tree.selection_set(connection_id)
        view.connect_selected()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if session.get_connection_status(connection_id).state == "connected":
                break
            time.sleep(0.02)
        assert session.get_connection_status(connection_id).state == "connected"
        assert feed.connect_calls == 1
        assert feed.instruments == (
            NIFTY.instrument_key,
            RELIANCE.instrument_key,
        )

        view.disconnect_selected()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if session.get_connection_status(connection_id).state == "disconnected":
                break
            time.sleep(0.02)
        assert session.get_connection_status(connection_id).state == "disconnected"
        assert session.credential_store.retrieve("upstox", connection_id) == FAKE_TOKEN

        view.delete_selected()
        assert confirms
        assert "Saved instruments will not be deleted" in confirms[0]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if not session.list_connections():
                break
            time.sleep(0.02)
        assert session.list_connections() == []
        assert session.list_saved_instruments() == [NIFTY, RELIANCE]
        assert FAKE_TOKEN not in view._status.cget("text")
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_create_connection_dialog_dispatches_mixed_saved_instruments(
    tmp_path,
) -> None:
    import tkinter as tk

    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(DELTA_BTC)
    calls: list[tuple[str, tuple[str, ...]]] = []
    session.create_connections_for_instruments = (
        lambda name, keys, **_credentials: calls.append((name, tuple(keys)))
    )
    app = MarketAlertApp(session)

    def descendants(widget: tk.Misc) -> list[tk.Misc]:
        children = list(widget.winfo_children())
        return children + [
            descendant
            for child in children
            for descendant in descendants(child)
        ]

    def open_dialog() -> tuple[tk.Toplevel, list[tk.Misc]]:
        view = app._connections_view
        assert view is not None
        view.open_create()
        dialog = next(
            child
            for child in app.root.winfo_children()
            if isinstance(child, tk.Toplevel)
            and child.title() == "Create Connection"
        )
        return dialog, descendants(dialog)

    try:
        dialog, widgets = open_dialog()
        labels = [
            widget.cget("text")
            for widget in widgets
            if widget.winfo_class() in {"Label", "TLabel"}
        ]
        assert "Provider:" not in labels
        assert "Connection name:" in labels
        assert "Saved instruments:" in labels
        buttons = {
            widget.cget("text"): widget
            for widget in widgets
            if getattr(widget, "mae_button", False)
        }
        assert {"Create", "Cancel"} <= buttons.keys()

        listbox = next(widget for widget in widgets if isinstance(widget, tk.Listbox))
        listbox.selection_set(0, 1)
        listbox.event_generate("<<ListboxSelect>>")
        app.root.update()
        buttons["Create"].invoke()
        assert calls == [
            ("", (NIFTY.instrument_key, DELTA_BTC.instrument_key))
        ]
    finally:
        shutdown_app(app)
