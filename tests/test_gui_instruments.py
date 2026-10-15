"""Offline tests for GUI instrument search/save/remove (no network, no tokens)."""

import threading
import time
from typing import Iterator

import pytest

from tests.tk_helpers import destroy_tk, tk_available
from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from data.upstox_instrument_resolver import UpstoxInstrumentSearchError
from gui.session import GuiSession
from gui.ui_queue import SearchErrorQueued, SearchResultQueued
from models.instrument import Instrument
from notifications.notification_queue import Notification

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_GUI_INSTRUMENTS_TEST"
RELIANCE = Instrument(
    "NSE_EQ|INE002A01018",
    "RELIANCE INDUSTRIES LTD",
    "RELIANCE",
    "NSE",
    "NSE_EQ",
    "EQ",
)
TCS = Instrument(
    "NSE_EQ|INE467B01029",
    "TATA CONSULTANCY SERVICES LTD",
    "TCS",
    "NSE",
    "NSE_EQ",
    "EQ",
)
DELTA_BTC = Instrument(
    "delta:BTCUSD",
    "Bitcoin",
    "BTCUSD",
    "Delta Exchange India",
    "PERPETUAL",
    "CRYPTO",
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
    def connect(self, instrument: object, credential: str) -> None:
        del instrument, credential

    def disconnect(self) -> None:
        return None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        del instrument, count
        return iter(())


class FakeResolver:
    def __init__(
        self,
        store: CredentialStore,
        credential_id: str,
        *,
        by_query: dict[str, list[Instrument]] | None = None,
        delay: float = 0,
        error: BaseException | None = None,
        blocker: threading.Event | None = None,
        queries: list[str] | None = None,
        created: list[tuple[CredentialStore, str]] | None = None,
    ) -> None:
        self.store = store
        self.credential_id = credential_id
        self.by_query = by_query or {}
        self.delay = delay
        self.error = error
        self.blocker = blocker
        self.queries = queries if queries is not None else []
        if created is not None:
            created.append((store, credential_id))

    def search(self, query: str, **_kwargs) -> list[Instrument]:
        self.queries.append(query)
        if self.blocker is not None:
            self.blocker.wait(timeout=2)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return list(self.by_query.get(query, []))


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
    resolver_factory=None,
    credential_store: MemoryCredentialStore | None = None,
) -> GuiSession:
    store = credential_store or MemoryCredentialStore()
    return GuiSession(
        credential_store=store,
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
        instrument_resolver_factory=resolver_factory,
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
    }
    values: list[str] = []
    for name, value in vars(session).items():
        if name in skipped:
            continue
        if isinstance(value, str):
            values.append(value)
    return values


def test_search_calls_resolver_and_queues_instruments(tmp_path) -> None:
    queries: list[str] = []
    created: list[tuple[CredentialStore, str]] = []

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(
            store,
            credential_id,
            by_query={"REL": [RELIANCE]},
            queries=queries,
            created=created,
        )

    session = make_session(tmp_path, resolver_factory=factory)
    request_id = session.start_instrument_search("REL", credential=FAKE_TOKEN)

    messages = wait_for_messages(session)
    assert queries == ["REL"]
    assert len(created) == 1
    assert isinstance(messages[0], SearchResultQueued)
    assert messages[0].request_id == request_id
    assert messages[0].results == (RELIANCE,)


def test_provider_neutral_instruments_keep_market_metadata() -> None:
    from gui.instruments_view import InstrumentsView

    assert RELIANCE.provider == "upstox"
    assert DELTA_BTC.provider == "delta"
    assert InstrumentsView._market_category(RELIANCE) == "INDIAN MARKET"
    assert InstrumentsView._market_category(DELTA_BTC) == "CRYPTO"


def test_search_starts_without_blocking_caller(tmp_path) -> None:
    release = threading.Event()

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(
            store,
            credential_id,
            by_query={"REL": [RELIANCE]},
            blocker=release,
        )

    session = make_session(tmp_path, resolver_factory=factory)
    started = time.monotonic()
    session.start_instrument_search("REL", credential=FAKE_TOKEN)
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert session.ui_queue.drain() == []
    release.set()
    messages = wait_for_messages(session)
    assert messages and isinstance(messages[0], SearchResultQueued)


def test_invalid_query_does_not_call_resolver(tmp_path) -> None:
    created: list[tuple[CredentialStore, str]] = []

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        created.append((store, credential_id))
        return FakeResolver(store, credential_id)

    session = make_session(tmp_path, resolver_factory=factory)
    with pytest.raises(ValueError, match="empty"):
        session.start_instrument_search("   ", credential=FAKE_TOKEN)
    with pytest.raises(ValueError, match="too long"):
        session.start_instrument_search("x" * 51, credential=FAKE_TOKEN)
    assert created == []
    assert session.current_search_request_id() == 0
    assert session.ui_queue.drain() == []


def test_save_instrument_does_not_create_connection_or_feed(tmp_path) -> None:
    session = make_session(tmp_path)
    connect_calls: list[str] = []
    create_calls: list[str] = []
    original_connect = session.connection_manager.connect
    original_create = session.connection_manager.create_connection

    def tracking_connect(connection_id: str) -> None:
        connect_calls.append(connection_id)
        original_connect(connection_id)

    def tracking_create(*args, **kwargs):
        create_calls.append("created")
        return original_create(*args, **kwargs)

    session.connection_manager.connect = tracking_connect  # type: ignore[method-assign]
    session.connection_manager.create_connection = tracking_create  # type: ignore[method-assign]

    saved = session.save_instrument(RELIANCE)
    assert saved == RELIANCE
    assert session.list_saved_instruments() == [RELIANCE]
    assert connect_calls == []
    assert create_calls == []
    assert session.list_connections() == []


def test_remove_instrument_does_not_touch_connections_or_credentials(
    tmp_path,
) -> None:
    session = make_session(tmp_path)
    session.save_instrument(RELIANCE)
    connection = session.create_connection(
        "Watchlist",
        "upstox",
        RELIANCE.instrument_key,
        FAKE_TOKEN,
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

    warning = session.instrument_removal_warning(RELIANCE.instrument_key)
    assert warning is not None
    assert "Watchlist" in warning
    assert "does not remove it from the connection" in warning
    assert session.connections_using_instrument(RELIANCE.instrument_key) == [
        connection
    ]

    session.remove_instrument(RELIANCE.instrument_key)

    assert session.list_saved_instruments() == []
    assert disconnect_calls == []
    assert delete_connection_calls == []
    assert session.credential_store.delete_calls == []
    assert session.list_connections()[0].connection_id == connection.connection_id
    assert session.list_connections()[0].subscribed_instruments() == (
        RELIANCE.instrument_key,
    )


def test_search_does_not_retain_token_on_session_or_queue(tmp_path) -> None:
    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        assert store.retrieve("upstox", credential_id) == FAKE_TOKEN
        return FakeResolver(store, credential_id, by_query={"REL": [RELIANCE]})

    session = make_session(tmp_path, resolver_factory=factory)
    session.start_instrument_search("REL", credential=FAKE_TOKEN)
    messages = wait_for_messages(session)

    assert FAKE_TOKEN not in session_owned_strings(session)
    assert FAKE_TOKEN not in vars(session).values()
    assert all(FAKE_TOKEN not in repr(message) for message in messages)
    assert session.ui_queue.drain() == []


def test_search_error_is_safe_and_does_not_include_token(tmp_path) -> None:
    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(
            store,
            credential_id,
            error=UpstoxInstrumentSearchError(
                "Upstox instrument search failed: HTTP 401 "
                f"token={FAKE_TOKEN}"
            ),
        )

    session = make_session(tmp_path, resolver_factory=factory)
    request_id = session.start_instrument_search("REL", credential=FAKE_TOKEN)
    messages = wait_for_messages(session)

    assert len(messages) == 1
    assert isinstance(messages[0], SearchErrorQueued)
    assert messages[0].request_id == request_id
    assert "401" in messages[0].message
    assert FAKE_TOKEN not in messages[0].message
    assert FAKE_TOKEN not in repr(messages[0])


def test_stale_search_results_do_not_overwrite_newer_search(tmp_path) -> None:
    rel_release = threading.Event()
    tcs_release = threading.Event()

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        class RoutingResolver:
            def search(self, query: str, **_kwargs) -> list[Instrument]:
                if query == "REL":
                    rel_release.wait(timeout=2)
                    return [RELIANCE]
                tcs_release.wait(timeout=2)
                return [TCS]

        return RoutingResolver()

    session = make_session(tmp_path, resolver_factory=factory)
    first = session.start_instrument_search("REL", credential=FAKE_TOKEN)
    second = session.start_instrument_search("TCS", credential=FAKE_TOKEN)
    tcs_release.set()
    messages = wait_for_messages(session)
    assert [message.request_id for message in messages] == [second]
    assert isinstance(messages[0], SearchResultQueued)
    assert messages[0].results == (TCS,)

    rel_release.set()
    time.sleep(0.1)
    assert session.ui_queue.drain() == []
    assert session.current_search_request_id() == second
    assert first != second


def _tk_available() -> bool:
    return tk_available()


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_instruments_view_save_and_confirmed_remove(tmp_path) -> None:
    import tkinter as tk

    from gui.instruments_view import InstrumentsView

    session = make_session(tmp_path)
    confirms: list[str] = []
    token_prompts = {"count": 0}

    def ask_token() -> str | None:
        token_prompts["count"] += 1
        return None

    def confirm(_title: str, message: str) -> bool:
        confirms.append(message)
        return True

    root = tk.Tk()
    try:
        view = InstrumentsView(
            root, session, ask_token=ask_token, confirm=confirm
        )
        view._query.insert(0, "   ")
        view.start_search()
        assert "empty" in view._status.cget("text")
        assert token_prompts["count"] == 0

        view._show_results([RELIANCE])
        view._results.selection_set(RELIANCE.instrument_key)
        view.save_selected()
        assert session.list_saved_instruments() == [RELIANCE]
        assert session.list_connections() == []
        assert RELIANCE.instrument_key in view._saved.get_children()

        session.create_connection(
            "Watchlist", "upstox", RELIANCE.instrument_key, FAKE_TOKEN
        )
        disconnect_calls: list[str] = []
        session.connection_manager.disconnect = (  # type: ignore[method-assign]
            lambda connection_id: disconnect_calls.append(connection_id)
        )
        view.refresh_saved()
        view._saved.selection_set(RELIANCE.instrument_key)
        view.remove_selected()
        assert confirms and "Watchlist" in confirms[0]
        assert session.list_saved_instruments() == []
        assert disconnect_calls == []
        assert session.credential_store.delete_calls == []
        assert FAKE_TOKEN not in view._status.cget("text")
    finally:
        destroy_tk(root)


@pytest.mark.skipif(not _tk_available(), reason="Tkinter is unavailable")
def test_app_drains_search_results_onto_instruments_tab(tmp_path) -> None:
    from gui.app import MarketAlertApp

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(store, credential_id, by_query={"REL": [RELIANCE]})

    session = make_session(tmp_path, resolver_factory=factory)
    app = MarketAlertApp(session)
    try:
        view = app._instruments_view
        assert view is not None
        view._ask_token = lambda: FAKE_TOKEN
        view._query.insert(0, "REL")
        view.start_search()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if RELIANCE.instrument_key in view._result_instruments:
                break
            time.sleep(0.02)
        assert RELIANCE.instrument_key in view._results.get_children()
        assert FAKE_TOKEN not in view._status.cget("text")
        assert session.list_connections() == []
    finally:
        app._on_close()
        destroy_tk(app.root)
