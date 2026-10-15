"""Offline tests for the GUI session skeleton (no Tk, no network)."""

from datetime import datetime, timezone
from typing import Iterator

from data.base_feed import PriceUpdate
from data.connection_manager import ConnectionManager
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from data.simulated_feed import SimulatedPriceFeed
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from gui.session import GuiSession
from gui.ui_queue import PriceQueued, UiQueue
from history.alert_history import AlertHistory
from models.instrument import Instrument
from notifications.notification_queue import Notification, NotificationQueue

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_SESSION_TEST"
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


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.delete_calls: list[tuple[str, str]] = []

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        return self.values[(provider, credential_id)]

    def delete(self, provider: str, credential_id: str) -> None:
        self.delete_calls.append((provider, credential_id))
        del self.values[(provider, credential_id)]


class SilentNotificationHandler:
    def handle(self, notification: Notification) -> None:
        del notification


class FakeManagedFeed:
    def __init__(self) -> None:
        self.disconnect_calls = 0

    def connect(self, instrument: object, credential: str) -> None:
        del instrument, credential

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        return iter(())


def make_session(tmp_path, *, feeds: list[FakeManagedFeed] | None = None) -> GuiSession:
    store = MemoryCredentialStore()
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    pending = list(feeds or [FakeManagedFeed()])

    def create_feed() -> FakeManagedFeed:
        return pending.pop(0)

    return GuiSession(
        credential_store=store,
        instrument_store=instrument_store,
        feed_factories={"upstox": create_feed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )


def session_owned_strings(session: GuiSession) -> list[str]:
    """Return string values stored directly on GuiSession, excluding backend services."""
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
    }
    values: list[str] = []
    for name, value in vars(session).items():
        if name in skipped:
            continue
        if isinstance(value, str):
            values.append(value)
    return values


def test_price_update_callback_is_queued(tmp_path) -> None:
    session = make_session(tmp_path)
    update = PriceUpdate(
        RELIANCE.instrument_key,
        1170.30,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    session.connection_manager._on_update(update)

    queued = session.ui_queue.drain()
    assert queued == [PriceQueued(update)]


def test_create_connection_does_not_retain_token_on_session_or_queue(
    tmp_path,
) -> None:
    session = make_session(tmp_path)
    session.save_instrument(RELIANCE)

    connection = session.create_connection(
        "Watchlist",
        "upstox",
        RELIANCE.instrument_key,
        FAKE_TOKEN,
        instruments=(RELIANCE.instrument_key,),
    )

    assert FAKE_TOKEN not in session_owned_strings(session)
    assert FAKE_TOKEN not in vars(session).values()
    queued = session.ui_queue.drain()
    assert queued == []
    assert all(FAKE_TOKEN not in repr(item) for item in queued)
    stored = session.credential_store.retrieve("upstox", connection.connection_id)
    assert stored == FAKE_TOKEN
    assert connection.instrument == RELIANCE.instrument_key


def test_remove_instrument_only_calls_instrument_store(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(RELIANCE)
    session.save_instrument(NIFTY)
    connection = session.create_connection(
        "Watchlist",
        "upstox",
        RELIANCE.instrument_key,
        FAKE_TOKEN,
        instruments=(RELIANCE.instrument_key, NIFTY.instrument_key),
    )

    disconnect_calls: list[str] = []
    delete_connection_calls: list[str] = []
    original_disconnect = session.connection_manager.disconnect
    original_delete = session.connection_manager.delete_connection

    def tracking_disconnect(connection_id: str) -> None:
        disconnect_calls.append(connection_id)
        original_disconnect(connection_id)

    def tracking_delete(connection_id: str) -> None:
        delete_connection_calls.append(connection_id)
        original_delete(connection_id)

    session.connection_manager.disconnect = tracking_disconnect  # type: ignore[method-assign]
    session.connection_manager.delete_connection = tracking_delete  # type: ignore[method-assign]

    assert session.connections_using_instrument(RELIANCE.instrument_key) == [
        connection
    ]
    session.remove_instrument(RELIANCE.instrument_key)

    assert session.instrument_store.contains(RELIANCE.instrument_key) is False
    assert session.instrument_store.contains(NIFTY.instrument_key) is True
    assert disconnect_calls == []
    assert delete_connection_calls == []
    assert session.credential_store.delete_calls == []
    assert session.credential_store.retrieve("upstox", connection.connection_id) == (
        FAKE_TOKEN
    )
    remaining = session.list_connections()
    assert len(remaining) == 1
    assert remaining[0].connection_id == connection.connection_id
    assert remaining[0].subscribed_instruments() == (
        RELIANCE.instrument_key,
        NIFTY.instrument_key,
    )


def test_session_composes_existing_backend_services(tmp_path) -> None:
    credential_store = MemoryCredentialStore()
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    session = GuiSession(
        credential_store=credential_store,
        instrument_store=instrument_store,
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )

    assert isinstance(session.ui_queue, UiQueue)
    assert isinstance(session.alert_manager, AlertManager)
    assert isinstance(session.alert_engine, AlertEngine)
    assert isinstance(session.alert_history, AlertHistory)
    assert isinstance(session.notification_queue, NotificationQueue)
    assert isinstance(session.connection_manager, ConnectionManager)
    assert isinstance(session.simulated_feed, SimulatedPriceFeed)
    assert session.credential_store is credential_store
    assert session.instrument_store is instrument_store
    assert session.connection_manager._credential_store is credential_store
    assert session.connection_manager._on_update == session._handle_price_update


def test_shutdown_closes_connection_manager(tmp_path) -> None:
    feed = FakeManagedFeed()
    session = make_session(tmp_path, feeds=[feed])
    connection = session.create_connection(
        "Watchlist",
        "upstox",
        NIFTY.instrument_key,
        FAKE_TOKEN,
    )
    session.connect(connection.connection_id)

    session.close()

    assert feed.disconnect_calls >= 1
    assert session.get_connection_status(connection.connection_id).state == (
        "disconnected"
    )
    assert session.credential_store.retrieve("upstox", connection.connection_id) == (
        FAKE_TOKEN
    )
