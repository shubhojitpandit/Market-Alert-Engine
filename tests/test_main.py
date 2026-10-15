"""Offline checks for the CLI's provider composition."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from data.base_feed import PriceFeed, PriceUpdate
from data.connection_manager import ConnectionManager
from data.credential_store import CredentialStore
from data.mt5_feed import DEFAULT_MT5_SYMBOL
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from data.instrument_store import InstrumentStore
from main import (
    add_alert,
    manage_upstox_connections,
    select_saved_instrument,
    select_saved_instruments,
    start_live_feed,
)
from models.alert import AlertCondition, AlertStatus
from models.instrument import Instrument
from notifications.notification_queue import Notification, NotificationQueue


class OneUpdateFeed(PriceFeed):
    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        assert instrument == DEFAULT_MT5_SYMBOL
        yield PriceUpdate(
            instrument,
            2001.0,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


class RecordingHandler:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def handle(self, notification: Notification) -> None:
        self.notifications.append(notification)


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        return self.values[(provider, credential_id)]

    def delete(self, provider: str, credential_id: str) -> None:
        del self.values[(provider, credential_id)]


def test_live_feed_updates_flow_into_alert_engine_and_notification_queue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager = AlertManager()
    alert = manager.create_alert(
        DEFAULT_MT5_SYMBOL, 2000.5, AlertCondition.ABOVE
    )
    queue = NotificationQueue()
    handler = RecordingHandler()

    start_live_feed(
        OneUpdateFeed(),
        AlertEngine(manager, queue),
        queue,
        handler,
    )

    assert alert.status is AlertStatus.TRIGGERED
    assert [item.alert for item in handler.notifications] == [alert]
    assert [item.current_price for item in handler.notifications] == [2001.0]
    assert queue.is_empty()
    assert "XAUUSDm 2001.00000" in capsys.readouterr().out


def test_add_alert_canonicalizes_mt5_symbol_case(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter(("XAUUSDM", "ABOVE", "2000"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    manager = AlertManager()

    add_alert(manager)

    assert manager.list_active_alerts()[0].symbol == DEFAULT_MT5_SYMBOL
    assert "XAUUSDm" in capsys.readouterr().out


def test_connection_creation_hides_fake_token_from_cli_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_token = "FAKE_ACCESS_TOKEN_CLI_TEST"
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    instrument_store.add(
        Instrument(
            "NSE_INDEX|Nifty 50",
            "Nifty 50",
            "Nifty 50",
            "NSE",
            "NSE_INDEX",
            "INDEX",
        )
    )
    responses = iter(("2", "My Upstox", "1", "6"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("main.getpass", lambda _prompt: fake_token)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    connection_manager = ConnectionManager(
        credential_store=MemoryCredentialStore(),
        feed_factories={"upstox": OneUpdateFeed},
        registry_path=tmp_path / "connections.json",
    )

    manage_upstox_connections(
        connection_manager, instrument_store=instrument_store
    )

    assert fake_token not in capsys.readouterr().out
    assert (
        connection_manager.list_connections()[0].instrument
        == "NSE_INDEX|Nifty 50"
    )


def test_instrument_search_cli_saves_result_without_exposing_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_token = "FAKE_ACCESS_TOKEN_CLI_SEARCH"
    credential_store = MemoryCredentialStore()
    connection_manager = ConnectionManager(
        credential_store=credential_store,
        feed_factories={"upstox": OneUpdateFeed},
        registry_path=tmp_path / "connections.json",
    )
    connection_manager.create_connection(
        "My Upstox", "upstox", "NSE_EQ|PLACEHOLDER", fake_token
    )
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    found = Instrument(
        "NSE_EQ|INE002A01018",
        "RELIANCE INDUSTRIES LTD",
        "RELIANCE",
        "NSE",
        "NSE_EQ",
        "EQ",
    )

    class FakeResolver:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def search(self, query: str) -> list[Instrument]:
            assert query == "REL"
            return [found]

    responses = iter(("7", "1", "REL", "1", "4", "6"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("main.UpstoxInstrumentResolver", FakeResolver)

    manage_upstox_connections(
        connection_manager,
        credential_store=credential_store,
        instrument_store=instrument_store,
    )

    output = capsys.readouterr().out
    assert fake_token not in output
    assert "Saved NSE_EQ|INE002A01018." in output
    assert instrument_store.list_instruments() == [found]
    saved_file = (tmp_path / "instruments.json").read_text(encoding="utf-8")
    assert fake_token not in saved_file


SAVED_RELIANCE = Instrument(
    "NSE_EQ|INE002A01018",
    "RELIANCE INDUSTRIES LTD",
    "RELIANCE",
    "NSE",
    "NSE_EQ",
    "EQ",
)


def test_select_saved_instrument_returns_chosen_instrument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    store.add(SAVED_RELIANCE)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    selected = select_saved_instrument(store)

    assert selected == SAVED_RELIANCE
    assert selected.instrument_key == "NSE_EQ|INE002A01018"


def test_select_saved_instrument_empty_store_does_not_ask_for_raw_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")

    assert select_saved_instrument(store) is None
    output = capsys.readouterr().out
    assert "No saved instruments" in output
    assert "search and save" in output.lower()


def test_select_saved_instrument_rejects_invalid_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    store.add(SAVED_RELIANCE)
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")

    assert select_saved_instrument(store) is None
    assert "listed saved instrument number" in capsys.readouterr().out


def test_create_connection_without_saved_instruments_does_not_use_raw_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter(("2", "My Upstox", "6"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    connection_manager = ConnectionManager(
        credential_store=MemoryCredentialStore(),
        feed_factories={"upstox": OneUpdateFeed},
        registry_path=tmp_path / "connections.json",
    )

    manage_upstox_connections(
        connection_manager,
        instrument_store=InstrumentStore(tmp_path / "instruments.json"),
    )

    output = capsys.readouterr().out
    assert connection_manager.list_connections() == []
    assert "No saved instruments" in output
    assert "Upstox instrument key" not in output


class RecordingLiveFeed:
    def __init__(self) -> None:
        self.connected_instrument: str | None = None
        self.connected_credential: str | None = None

    def connect(self, instrument: str | tuple[str, ...], credential: str) -> None:
        self.connected_instrument = instrument
        self.connected_credential = credential

    def disconnect(self) -> None:
        return None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        return iter(())


def test_live_feed_connect_uses_saved_instrument_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_token = "FAKE_ACCESS_TOKEN_LIVE_SAVED_INSTRUMENT"
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    instrument_store.add(SAVED_RELIANCE)
    feed = RecordingLiveFeed()
    credential_store = MemoryCredentialStore()
    connection_manager = ConnectionManager(
        credential_store=credential_store,
        feed_factories={"upstox": lambda: feed},
        registry_path=tmp_path / "connections.json",
    )
    created: list[str] = []
    menu_choices = iter(("2", "3", "6"))
    original_create = connection_manager.create_connection

    def create_and_record(
        name: str,
        provider: str,
        instrument: str,
        credential: str,
        instruments=None,
    ):
        connection = original_create(
            name,
            provider,
            instrument,
            credential,
            instruments=instruments,
        )
        created.append(connection.connection_id)
        return connection

    connection_manager.create_connection = create_and_record  # type: ignore[method-assign]

    def inputs(prompt: str) -> str:
        if prompt == "Choose an option: ":
            return next(menu_choices)
        if prompt == "Connection name: ":
            return "Reliance Live"
        if prompt == "Select saved instrument numbers (e.g. 1,2,4): ":
            return "1"
        if prompt == "Connection ID: ":
            return created[0]
        raise AssertionError(prompt)

    monkeypatch.setattr("builtins.input", inputs)
    monkeypatch.setattr("main.getpass", lambda _prompt: fake_token)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    manage_upstox_connections(
        connection_manager, instrument_store=instrument_store
    )

    output = capsys.readouterr().out
    assert fake_token not in output
    assert feed.connected_instrument == (SAVED_RELIANCE.instrument_key,)
    assert connection_manager.list_connections()[0].instrument == (
        SAVED_RELIANCE.instrument_key
    )
    connection_manager.disconnect(created[0])


SAVED_NIFTY = Instrument(
    "NSE_INDEX|Nifty 50",
    "Nifty 50",
    "Nifty 50",
    "NSE",
    "NSE_INDEX",
    "INDEX",
)


def test_select_saved_instruments_multi_select_dedupes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InstrumentStore(tmp_path / "instruments.json")
    store.add(SAVED_RELIANCE)
    store.add(SAVED_NIFTY)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1,2,1")

    selected = select_saved_instruments(store)

    assert selected == [SAVED_RELIANCE, SAVED_NIFTY]


def test_cli_multi_select_saved_instruments_creates_multi_key_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_token = "FAKE_ACCESS_TOKEN_MULTI_SELECT"
    instrument_store = InstrumentStore(tmp_path / "instruments.json")
    instrument_store.add(SAVED_RELIANCE)
    instrument_store.add(SAVED_NIFTY)
    responses = iter(("2", "Watchlist", "1,2,1", "6"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    monkeypatch.setattr("main.getpass", lambda _prompt: fake_token)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    connection_manager = ConnectionManager(
        credential_store=MemoryCredentialStore(),
        feed_factories={"upstox": OneUpdateFeed},
        registry_path=tmp_path / "connections.json",
    )

    manage_upstox_connections(
        connection_manager, instrument_store=instrument_store
    )

    output = capsys.readouterr().out
    connection = connection_manager.list_connections()[0]
    assert fake_token not in output
    assert connection.instrument == SAVED_RELIANCE.instrument_key
    assert connection.instruments == (
        SAVED_RELIANCE.instrument_key,
        SAVED_NIFTY.instrument_key,
    )
