"""Offline tests for provider-neutral saved connection lifecycle."""

import json
import threading
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Iterator

import pytest

from data.base_feed import PriceUpdate
from data.connection_manager import (
    ConnectionManager,
    ConnectionManagerError,
)
from data.credential_store import CredentialStore
from data.upstox_feed import UpstoxFeedError

FAKE_TOKEN = "FAKE_ACCESS_TOKEN_CONNECTION_TEST"


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        self.values[(provider, credential_id)] = credential

    def retrieve(self, provider: str, credential_id: str) -> str:
        return self.values[(provider, credential_id)]

    def delete(self, provider: str, credential_id: str) -> None:
        del self.values[(provider, credential_id)]


class FakeManagedFeed:
    def __init__(
        self,
        *,
        fail_connect: bool = False,
        updates: list[PriceUpdate] | None = None,
    ) -> None:
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.received_credential: str | None = None
        self.instrument: str | None = None
        self.instruments: tuple[str, ...] = ()
        self.connect_calls = 0
        self.fail_connect = fail_connect
        self._updates = updates

    def connect(
        self, instrument: str | Sequence[str], credential: str
    ) -> None:
        if self.fail_connect:
            raise RuntimeError(f"failure containing {credential}")
        if isinstance(instrument, str):
            keys = (instrument,)
        else:
            keys = tuple(instrument)
        self.instruments = keys
        self.instrument = keys[0] if keys else None
        self.received_credential = credential
        self.connect_calls += 1
        self.connected.set()

    def disconnect(self) -> None:
        self.closed.set()

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        assert self.connected.wait(timeout=2)
        if self._updates is not None:
            yield from self._updates
        else:
            yield PriceUpdate(
                instrument,
                12.5,
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        self.closed.wait(timeout=2)


def make_manager(
    tmp_path, store: MemoryCredentialStore, feeds: list[FakeManagedFeed]
) -> ConnectionManager:
    def create_feed() -> FakeManagedFeed:
        feed = feeds.pop(0)
        return feed

    return ConnectionManager(
        credential_store=store,
        feed_factories={"upstox": create_feed},
        registry_path=tmp_path / "connections.json",
    )


def test_disconnect_preserves_credential_and_reconnect_reuses_it(
    tmp_path,
) -> None:
    store = MemoryCredentialStore()
    first_feed = FakeManagedFeed()
    second_feed = FakeManagedFeed()
    manager = make_manager(tmp_path, store, [first_feed, second_feed])
    updates: list[PriceUpdate] = []
    manager._on_update = updates.append

    connection = manager.create_connection(
        "Test Upstox", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )
    manager.connect(connection.connection_id)
    assert first_feed.connected.wait(timeout=2)
    manager.disconnect(connection.connection_id)

    assert store.retrieve("upstox", connection.connection_id) == FAKE_TOKEN
    assert first_feed.closed.is_set()
    assert manager.get_status(connection.connection_id).state == "disconnected"

    manager.connect(connection.connection_id)
    assert second_feed.connected.wait(timeout=2)
    manager.disconnect(connection.connection_id)
    assert second_feed.received_credential == FAKE_TOKEN
    assert len(updates) == 2


def test_delete_disconnects_and_removes_saved_connection_and_credential(
    tmp_path,
) -> None:
    store = MemoryCredentialStore()
    feed = FakeManagedFeed()
    manager = make_manager(tmp_path, store, [feed])
    connection = manager.create_connection(
        "Delete me", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )
    manager.connect(connection.connection_id)
    assert feed.connected.wait(timeout=2)

    manager.delete_connection(connection.connection_id)

    assert feed.closed.is_set()
    assert manager.list_connections() == []
    assert store.values == {}
    assert json.loads((tmp_path / "connections.json").read_text()) == []


def test_connection_metadata_persists_without_credential(tmp_path) -> None:
    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [])
    manager.create_connection(
        "Saved", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )

    saved = (tmp_path / "connections.json").read_text()

    assert FAKE_TOKEN not in saved
    assert "NSE_INDEX|TEST" in saved
    restored = ConnectionManager(
        credential_store=store,
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
    )
    assert restored.list_connections() == manager.list_connections()


def test_connection_failure_does_not_expose_credential(tmp_path, capsys) -> None:
    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [FakeManagedFeed(fail_connect=True)])
    connection = manager.create_connection(
        "Failure", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )

    with pytest.raises(ConnectionManagerError) as error:
        manager.connect(connection.connection_id)

    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err
    assert manager.get_status(connection.connection_id).error == (
        "Provider connection failed"
    )
    assert store.retrieve("upstox", connection.connection_id) == FAKE_TOKEN


@pytest.mark.parametrize(
    "stage",
    [
        "authorization request",
        "authorization request: network error",
        "authorization request: HTTP 401",
        "authorization request: HTTP 403",
        "invalid authorization response",
        "WebSocket connection",
        "subscription",
    ],
)
def test_upstox_connection_failure_reports_safe_stage_without_credential(
    tmp_path, capsys, stage: str
) -> None:
    class FailingFeed(FakeManagedFeed):
        def connect(self, instrument: str, credential: str) -> None:
            raise UpstoxFeedError(
                f"safe error; secret was {credential}",
                diagnostic_stage=stage,
            )

    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [FailingFeed()])
    connection = manager.create_connection(
        "Failure stage", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )

    with pytest.raises(ConnectionManagerError) as error:
        manager.connect(connection.connection_id)

    expected = f"Provider connection failed: {stage}"
    assert str(error.value) == expected
    assert manager.get_status(connection.connection_id).error == expected
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_connection_manager_forwards_only_safe_upstox_error_details(
    tmp_path, capsys
) -> None:
    class DiagnosticFeed(FakeManagedFeed):
        def connect(self, instrument: str, credential: str) -> None:
            raise UpstoxFeedError(
                "sanitized adapter diagnostic",
                diagnostic_stage="authorization request: HTTP 403",
                diagnostic_code="UDAPI100060",
                diagnostic_error_name="AccessDenied",
                diagnostic_detail=(
                    "Authorization is not available for this request."
                ),
                diagnostic_error_category="AUTHORIZATION",
                diagnostic_retryable=False,
                diagnostic_owner_action_required=True,
                diagnostic_what_you_should_do=(
                    "Review the application API permissions."
                ),
            )

    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [DiagnosticFeed()])
    connection = manager.create_connection(
        "Failure details", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )

    with pytest.raises(ConnectionManagerError) as error:
        manager.connect(connection.connection_id)

    expected = (
        "Provider connection failed: authorization request: HTTP 403; "
        "code=UDAPI100060; detail=Authorization is not available for this "
        "request.; name=AccessDenied; category=AUTHORIZATION; "
        "retryable=false; owner_action_required=true; "
        "what_you_should_do=Review the application API permissions."
    )
    assert str(error.value) == expected
    assert manager.get_status(connection.connection_id).error == expected
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_unknown_and_duplicate_provider_metadata_are_rejected(tmp_path) -> None:
    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [])

    with pytest.raises(ValueError, match="unsupported"):
        manager.create_connection("Name", "unknown", "INSTRUMENT", FAKE_TOKEN)

    path = tmp_path / "connections.json"
    path.write_text(
        json.dumps(
            [
                {
                    "connection_id": "same",
                    "name": "one",
                    "provider": "upstox",
                    "instrument": "A",
                },
                {
                    "connection_id": "same",
                    "name": "two",
                    "provider": "upstox",
                    "instrument": "B",
                },
            ]
        )
    )
    with pytest.raises(ConnectionManagerError, match="metadata is invalid"):
        ConnectionManager(
            credential_store=store,
            feed_factories={"upstox": FakeManagedFeed},
            registry_path=path,
        )


def test_application_close_disconnects_without_deleting_credentials(
    tmp_path,
) -> None:
    store = MemoryCredentialStore()
    feed = FakeManagedFeed()
    manager = make_manager(tmp_path, store, [feed])
    connection = manager.create_connection(
        "Exit", "upstox", "NSE_INDEX|TEST", FAKE_TOKEN
    )
    manager.connect(connection.connection_id)
    assert feed.connected.wait(timeout=2)

    manager.close()

    assert feed.closed.is_set()
    assert store.retrieve("upstox", connection.connection_id) == FAKE_TOKEN


def test_legacy_registry_instrument_loads_as_single_key_tuple(tmp_path) -> None:
    store = MemoryCredentialStore()
    path = tmp_path / "connections.json"
    path.write_text(
        json.dumps(
            [
                {
                    "connection_id": "legacy-id",
                    "name": "Legacy Nifty",
                    "provider": "upstox",
                    "instrument": "NSE_INDEX|Nifty 50",
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = ConnectionManager(
        credential_store=store,
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=path,
    )
    connection = manager.list_connections()[0]

    assert connection.instrument == "NSE_INDEX|Nifty 50"
    assert connection.instruments == ("NSE_INDEX|Nifty 50",)
    assert connection.subscribed_instruments() == ("NSE_INDEX|Nifty 50",)


def test_new_connection_persists_instrument_and_instruments(tmp_path) -> None:
    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [])
    keys = ("NSE_INDEX|Nifty 50", "NSE_EQ|INE002A01018")
    connection = manager.create_connection(
        "Multi",
        "upstox",
        keys[0],
        FAKE_TOKEN,
        instruments=keys,
    )

    assert connection.instrument == keys[0]
    assert connection.instruments == keys
    saved = json.loads((tmp_path / "connections.json").read_text(encoding="utf-8"))
    assert FAKE_TOKEN not in json.dumps(saved)
    assert saved[0]["instrument"] == keys[0]
    assert saved[0]["instruments"] == list(keys)

    restored = ConnectionManager(
        credential_store=store,
        feed_factories={"upstox": FakeManagedFeed},
        registry_path=tmp_path / "connections.json",
    )
    assert restored.list_connections()[0].instruments == keys
    assert restored.list_connections()[0].instrument == keys[0]


def test_duplicate_and_empty_instruments_are_normalized(tmp_path) -> None:
    store = MemoryCredentialStore()
    manager = make_manager(tmp_path, store, [])
    connection = manager.create_connection(
        "Deduped",
        "upstox",
        "NSE_EQ|A",
        FAKE_TOKEN,
        instruments=("NSE_EQ|A", " NSE_EQ|B ", "NSE_EQ|A", ""),
    )
    assert connection.instruments == ("NSE_EQ|A", "NSE_EQ|B")

    with pytest.raises(ValueError, match="instrument"):
        manager.create_connection(
            "Empty", "upstox", "ignored", FAKE_TOKEN, instruments=()
        )


def test_connect_passes_all_instruments_once_and_forwards_mixed_updates(
    tmp_path,
) -> None:
    store = MemoryCredentialStore()
    nifty = "NSE_INDEX|Nifty 50"
    reliance = "NSE_EQ|INE002A01018"
    updates = [
        PriceUpdate(nifty, 22000.0, datetime(2026, 1, 1, tzinfo=timezone.utc)),
        PriceUpdate(reliance, 1400.0, datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    feed = FakeManagedFeed(updates=updates)
    retrieve_calls = {"count": 0}
    original_retrieve = store.retrieve

    def counting_retrieve(provider: str, credential_id: str) -> str:
        retrieve_calls["count"] += 1
        return original_retrieve(provider, credential_id)

    store.retrieve = counting_retrieve  # type: ignore[method-assign]
    manager = make_manager(tmp_path, store, [feed])
    received: list[PriceUpdate] = []
    manager._on_update = received.append
    connection = manager.create_connection(
        "Watchlist",
        "upstox",
        nifty,
        FAKE_TOKEN,
        instruments=(nifty, reliance),
    )
    manager.connect(connection.connection_id)
    assert feed.connected.wait(timeout=2)
    deadline = threading.Event()
    for _ in range(50):
        if received == updates:
            break
        deadline.wait(0.04)

    assert retrieve_calls["count"] == 1
    assert feed.connect_calls == 1
    assert feed.instruments == (nifty, reliance)
    assert feed.received_credential == FAKE_TOKEN
    assert received == updates
    manager.disconnect(connection.connection_id)
    assert store.retrieve("upstox", connection.connection_id) == FAKE_TOKEN
