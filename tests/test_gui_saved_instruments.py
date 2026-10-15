"""Saved-instrument persistence and one-time Upstox credential reuse."""

from __future__ import annotations

import json
import time
from typing import Iterator

import pytest

from data.base_feed import PriceUpdate
from data.credential_store import CredentialStore
from data.instrument_store import InstrumentStore
from data.upstox_instrument_resolver import UpstoxInstrumentSearchError
from gui.session import GuiSession
from gui.ui_queue import SearchResultQueued
from models.alert import AlertCondition
from models.instrument import Instrument
from notifications.notification_queue import Notification
from tests.tk_helpers import (
    isolate_gui_persistent_storage,
    shutdown_app,
    tk_available,
)
from tests.test_gui_instruments import (
    FAKE_TOKEN,
    FakeResolver,
    MemoryCredentialStore,
    RELIANCE,
    make_session,
    session_owned_strings,
    wait_for_messages,
)

NIFTY = Instrument(
    "NSE_INDEX|Nifty 50",
    "Nifty 50",
    "Nifty 50",
    "NSE",
    "NSE_INDEX",
    "INDEX",
)
NIFTY_KEY = NIFTY.instrument_key
RELIANCE_KEY = RELIANCE.instrument_key
DELTA_BTC = Instrument(
    "delta:BTCUSD",
    "Bitcoin",
    "BTCUSD",
    "Delta Exchange India",
    "PERPETUAL",
    "CRYPTO",
)


@pytest.fixture(autouse=True)
def _isolate_gui_stores(monkeypatch, tmp_path) -> None:
    isolate_gui_persistent_storage(monkeypatch, tmp_path)


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


def test_saved_instruments_persist_across_store_reload(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    store = MemoryCredentialStore()
    first = GuiSession(
        credential_store=store,
        instrument_store=InstrumentStore(path),
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )
    first.save_instrument(NIFTY)
    first.save_instrument(RELIANCE)
    first.save_instrument(DELTA_BTC)
    second = GuiSession(
        credential_store=store,
        instrument_store=InstrumentStore(path),
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
        notification_handler=SilentNotificationHandler(),
    )
    saved = second.list_saved_instruments()
    assert saved == [NIFTY, RELIANCE, DELTA_BTC]
    assert [instrument.provider for instrument in saved] == [
        "upstox",
        "upstox",
        "delta",
    ]


def test_search_does_not_remove_existing_saved_instruments(tmp_path) -> None:
    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(store, credential_id, by_query={"REL": [RELIANCE]})

    session = make_session(tmp_path, resolver_factory=factory)
    session.save_instrument(NIFTY)
    session.start_instrument_search("REL", credential=FAKE_TOKEN)
    wait_for_messages(session)
    assert session.list_saved_instruments() == [NIFTY]


def test_removing_one_instrument_leaves_others(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    session.remove_instrument(RELIANCE_KEY)
    assert session.list_saved_instruments() == [NIFTY]


def test_alerts_available_instruments_come_from_instrument_store(tmp_path) -> None:
    session = make_session(tmp_path)
    session.create_connection("Watchlist", "upstox", RELIANCE_KEY, FAKE_TOKEN)
    assert session.available_alert_instruments() == []
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    session.save_instrument(DELTA_BTC)
    assert session.available_alert_instruments() == [
        (NIFTY_KEY, "Nifty 50"),
        (RELIANCE_KEY, "RELIANCE INDUSTRIES LTD"),
        (DELTA_BTC.instrument_key, "Bitcoin"),
    ]


def test_adding_instrument_updates_alerts_without_restart(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    assert [key for key, _name in session.available_alert_instruments()] == [
        NIFTY_KEY
    ]
    session.save_instrument(RELIANCE)
    assert [key for key, _name in session.available_alert_instruments()] == [
        NIFTY_KEY,
        RELIANCE_KEY,
    ]


def test_removing_instrument_drops_it_from_new_alert_selector_only(
    tmp_path,
) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    alert = session.create_alert(RELIANCE_KEY, 1170, AlertCondition.BELOW)
    session.remove_instrument(RELIANCE_KEY)
    keys = [key for key, _name in session.available_alert_instruments()]
    assert keys == [NIFTY_KEY]
    assert RELIANCE_KEY not in keys
    assert session.list_active_alerts() == [alert]
    assert alert.symbol == RELIANCE_KEY


def test_first_search_saves_credential_later_searches_reuse_it(tmp_path) -> None:
    created: list[str] = []

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        created.append(credential_id)
        assert store.retrieve("upstox", credential_id) == FAKE_TOKEN
        return FakeResolver(store, credential_id, by_query={"NIFTY": [NIFTY], "REL": [RELIANCE]})

    session = make_session(tmp_path, resolver_factory=factory)
    assert session.has_upstox_credential() is False
    session.start_instrument_search("NIFTY", credential=FAKE_TOKEN)
    wait_for_messages(session)
    assert session.has_upstox_credential() is True
    session.start_instrument_search("REL")
    wait_for_messages(session)
    assert created == ["account", "account"]
    assert FAKE_TOKEN not in session_owned_strings(session)


def test_credential_is_not_written_to_instrument_or_connection_json(
    tmp_path,
) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_upstox_credential(FAKE_TOKEN)
    session.create_upstox_connection("Watchlist", (NIFTY_KEY,))
    wait_for_messages(session)
    instruments_text = (tmp_path / "instruments.json").read_text(encoding="utf-8")
    connections_text = (tmp_path / "connections.json").read_text(encoding="utf-8")
    assert FAKE_TOKEN not in instruments_text
    assert FAKE_TOKEN not in connections_text
    payload = json.loads(connections_text)
    dumped = json.dumps(payload)
    assert "access_token" not in dumped
    assert FAKE_TOKEN not in dumped
    assert FAKE_TOKEN not in session_owned_strings(session)
    queued = session.ui_queue.drain()
    assert all(FAKE_TOKEN not in repr(item) for item in queued)


def test_authentication_failure_is_sanitized(tmp_path) -> None:
    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        return FakeResolver(
            store,
            credential_id,
            error=UpstoxInstrumentSearchError(
                f"Upstox instrument search failed: HTTP 401 token={FAKE_TOKEN}"
            ),
        )

    session = make_session(tmp_path, resolver_factory=factory)
    session.start_instrument_search("REL", credential=FAKE_TOKEN)
    messages = wait_for_messages(session)
    assert isinstance(messages[0], SearchResultQueued)
    assert messages[0].warning is not None
    assert "INDIAN MARKET search failed." in messages[0].warning
    assert FAKE_TOKEN not in messages[0].warning
    assert FAKE_TOKEN not in repr(messages[0])


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_gui_saves_api_key_then_reuses_stored_credential(
    tmp_path, monkeypatch
) -> None:
    from gui.app import MarketAlertApp

    class EmptyCryptoResolver:
        def search(self, query: str) -> list:
            del query
            return []

    monkeypatch.setattr(
        "gui.session.DeltaInstrumentResolver", EmptyCryptoResolver
    )
    resolved_credentials: list[str] = []

    def factory(store: CredentialStore, credential_id: str) -> FakeResolver:
        assert store.retrieve("upstox", credential_id) == FAKE_TOKEN
        resolved_credentials.append(credential_id)
        return FakeResolver(
            store, credential_id, by_query={"NIFTY": [NIFTY], "REL": [RELIANCE]}
        )

    def walk_widgets(widget):
        found = [widget]
        for child in widget.winfo_children():
            found.extend(walk_widgets(child))
        return found

    session = make_session(tmp_path, resolver_factory=factory)
    app = MarketAlertApp(session)
    try:
        view = app._instruments_view
        assert view is not None

        view.open_credentials_dialog()
        dialog = view._dialog
        token_entry = next(
            widget
            for widget in walk_widgets(dialog)
            if widget.winfo_class() == "TEntry"
        )
        token_entry.insert(0, FAKE_TOKEN)
        save_button = next(
            widget
            for widget in walk_widgets(dialog)
            if getattr(widget, "mae_button", False)
            and widget.cget("text") == "Save API Key"
        )
        save_button.invoke()
        assert session.has_upstox_credential()

        def unexpected_prompt() -> str:
            raise AssertionError("Instrument search must reuse the saved API Key.")

        view._ask_token = unexpected_prompt
        view._query.insert(0, "NIFTY")
        view.start_search()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if NIFTY.instrument_key in view._result_instruments:
                break
            time.sleep(0.02)
        assert NIFTY.instrument_key in view._result_instruments
        view._results.selection_set(NIFTY.instrument_key)
        view.save_selected()
        view._query.delete(0, "end")
        view._query.insert(0, "REL")
        view.start_search()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app._schedule_queue_drain()
            if RELIANCE.instrument_key in view._result_instruments:
                break
            time.sleep(0.02)
        assert RELIANCE.instrument_key in view._result_instruments
        view._results.selection_set(RELIANCE.instrument_key)
        view.save_selected()
        assert resolved_credentials == ["account", "account"]
        assert session.list_saved_instruments() == [NIFTY, RELIANCE]
        alerts = app._alerts_view
        assert alerts is not None
        alerts.refresh_instruments()
        assert tuple(alerts._instrument["values"]) == (
            "Nifty 50",
            "RELIANCE INDUSTRIES LTD",
        )
        assert NIFTY_KEY not in str(alerts._instrument["values"])
        assert FAKE_TOKEN not in view._status.cget("text")
        assert session.has_upstox_credential()
        assert app._connections_view is not None
        assert app._connections_view.open_create
        session.remove_instrument(RELIANCE_KEY)
        view.refresh_saved()
        alerts.refresh_instruments()
        assert session.list_saved_instruments() == [NIFTY]
        assert tuple(alerts._instrument["values"]) == ("Nifty 50",)
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_create_connection_uses_saved_names_without_token_field(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    session.save_upstox_credential(FAKE_TOKEN)
    app = MarketAlertApp(session)
    try:
        view = app._connections_view
        assert view is not None
        saved = session.list_saved_instruments()
        labels = [
            instrument.name or instrument.trading_symbol for instrument in saved
        ]
        assert labels == ["Nifty 50", "RELIANCE INDUSTRIES LTD"]
        assert session.has_upstox_credential() is True
        session.create_upstox_connection("Watchlist", (NIFTY_KEY, RELIANCE_KEY))
        wait_for_messages(session)
        app._schedule_queue_drain()
        view.refresh()
        connection_id = session.list_connections()[0].connection_id
        values = view._tree.item(connection_id, "values")
        assert "Nifty 50" in values[2]
        assert "RELIANCE INDUSTRIES LTD" in values[2]
        assert NIFTY_KEY not in values[2]
        assert FAKE_TOKEN not in values[2]
        assert FAKE_TOKEN not in view._status.cget("text")
    finally:
        shutdown_app(app)
