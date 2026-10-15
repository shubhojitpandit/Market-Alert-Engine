"""Provider-neutral connection metadata, credential, and lifecycle management."""

import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol

from data.base_feed import PriceFeed, PriceUpdate
from data.credential_store import CredentialStore, CredentialStoreError

_SAFE_PROVIDER_FAILURE_STAGES = {
    "authorization request",
    "authorization request: network error",
    "invalid authorization response",
    "WebSocket connection",
    "subscription",
}
_SAFE_PROVIDER_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_SAFE_PROVIDER_ERROR_NAME = re.compile(r"[A-Za-z0-9_. -]{1,64}\Z")
_SAFE_PROVIDER_ERROR_CATEGORY = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_SAFE_PROVIDER_ERROR_MESSAGE = re.compile(
    r"[A-Za-z0-9 .,;:!?()'/-]{1,300}\Z"
)
_SENSITIVE_PROVIDER_ERROR_CONTENT = re.compile(
    r"(?:https?://|wss?://|www\.|bearer\s|authorization\s*:|"
    r"(?:api[_ -]?)?(?:key|secret|token|credential|payload)\s*[:=])",
    re.IGNORECASE,
)


class ConnectionManagerError(RuntimeError):
    """Raised when a saved market-data connection cannot be managed."""


def _normalize_instrument_keys(values: Sequence[object]) -> tuple[str, ...]:
    """Return unique non-empty instrument keys, preserving first-seen order."""
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("instrument keys must be strings")
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        raise ValueError("instrument must not be empty")
    return tuple(keys)


def _safe_stream_error(error: Exception, credential: str) -> str:
    """Format a feed exception without exposing credentials or raw payloads."""
    error_name = type(error).__name__
    if not _SAFE_PROVIDER_ERROR_NAME.fullmatch(error_name):
        error_name = "ProviderError"
    try:
        message = " ".join(str(error).split())
    except Exception:
        return error_name
    if credential:
        message = message.replace(credential, "[redacted]")
        try:
            credential_data = json.loads(credential)
        except (TypeError, json.JSONDecodeError):
            credential_data = None
        if isinstance(credential_data, dict):
            for field, value in credential_data.items():
                if (
                    isinstance(field, str)
                    and any(
                        marker in field.casefold()
                        for marker in ("key", "secret", "token", "credential")
                    )
                    and isinstance(value, str)
                    and value
                ):
                    message = message.replace(value, "[redacted]")
    if (
        not _SAFE_PROVIDER_ERROR_MESSAGE.fullmatch(message)
        or _SENSITIVE_PROVIDER_ERROR_CONTENT.search(message)
    ):
        message = "diagnostic details redacted"
    return f"{error_name}: {message}"


@dataclass(frozen=True)
class Connection:
    """Non-secret configuration for one provider connection."""

    connection_id: str
    name: str
    provider: str
    instrument: str
    instruments: tuple[str, ...] = ()

    def subscribed_instruments(self) -> tuple[str, ...]:
        """Return subscribed keys, falling back to the legacy single instrument."""
        return self.instruments if self.instruments else (self.instrument,)


@dataclass(frozen=True)
class ConnectionStatus:
    """Safe-to-display lifecycle state for a saved connection."""

    state: str
    error: str | None = None


class ManagedPriceFeed(Protocol):
    """PriceFeed with explicit provider connection lifecycle methods."""

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]: ...

    def connect(
        self, instrument: str | Sequence[str], credential: str
    ) -> None: ...

    def disconnect(self) -> None: ...


@dataclass
class _ActiveConnection:
    feed: ManagedPriceFeed
    stop_event: threading.Event
    thread: threading.Thread | None = None


class ConnectionManager:
    """Persist safe connection metadata and manage provider adapter lifecycles."""

    def __init__(
        self,
        credential_store: CredentialStore,
        feed_factories: Mapping[str, Callable[[], ManagedPriceFeed]],
        on_update: Callable[[PriceUpdate], None] | None = None,
        registry_path: Path | None = None,
        on_market_timestamp: Callable[[str, str, datetime], None] | None = None,
    ) -> None:
        self._credential_store = credential_store
        self._feed_factories = dict(feed_factories)
        self._on_update = on_update or (lambda _update: None)
        self._on_market_timestamp = on_market_timestamp
        self._registry_path = registry_path or self.default_registry_path()
        self._connections = self._load_connections()
        self._active: dict[str, _ActiveConnection] = {}
        self._statuses = {
            connection_id: ConnectionStatus("disconnected")
            for connection_id in self._connections
        }
        self._lock = threading.RLock()

    @staticmethod
    def default_registry_path() -> Path:
        """Return a per-user local path containing no provider credentials."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data)
        else:
            root = Path.home() / "AppData" / "Local"
        return root / "MarketAlertEngine" / "connections.json"

    def list_connections(self) -> list[Connection]:
        """Return saved connection metadata without reading credentials."""
        with self._lock:
            return list(self._connections.values())

    def get_status(self, connection_id: str) -> ConnectionStatus:
        """Return a connection's safe-to-display runtime state."""
        with self._lock:
            self._require_connection(connection_id)
            return self._statuses[connection_id]

    def create_connection(
        self,
        name: str,
        provider: str,
        instrument: str,
        credential: str,
        instruments: Sequence[str] | None = None,
    ) -> Connection:
        """Save connection metadata and its provider-scoped protected credential."""
        if not name.strip():
            raise ValueError("connection name and instrument must not be empty")
        if provider not in self._feed_factories:
            raise ValueError("unsupported market-data provider")
        if not credential and getattr(
            self._feed_factories[provider], "requires_credentials", True
        ):
            raise ValueError("credential must not be empty")
        try:
            keys = _normalize_instrument_keys(
                instruments if instruments is not None else (instrument,)
            )
        except ValueError:
            raise ValueError("connection name and instrument must not be empty") from None

        connection = Connection(
            connection_id=uuid.uuid4().hex,
            name=name.strip(),
            provider=provider,
            instrument=keys[0],
            instruments=keys,
        )
        with self._lock:
            if credential:
                self._credential_store.save(
                    provider, connection.connection_id, credential
                )
            self._connections[connection.connection_id] = connection
            self._statuses[connection.connection_id] = ConnectionStatus(
                "disconnected"
            )
            try:
                self._save_connections()
            except Exception:
                self._connections.pop(connection.connection_id, None)
                self._statuses.pop(connection.connection_id, None)
                try:
                    if credential:
                        self._credential_store.delete(
                            provider, connection.connection_id
                        )
                except Exception:
                    raise ConnectionManagerError(
                        "Could not save connection metadata or clean up its "
                        "protected credential"
                    ) from None
                raise ConnectionManagerError(
                    "Could not save connection metadata"
                ) from None
        return connection

    def connect(self, connection_id: str) -> None:
        """Establish a provider connection and start forwarding normalized updates."""
        with self._lock:
            connection = self._require_connection(connection_id)
            if connection_id in self._active:
                return
            if self._statuses[connection_id].state == "connecting":
                return
            self._statuses[connection_id] = ConnectionStatus("connecting")

        feed: ManagedPriceFeed | None = None
        credential: str | None = None
        credential_missing = False
        try:
            feed = self._feed_factories[connection.provider]()
            try:
                credential = self._credential_store.retrieve(
                    connection.provider, connection.connection_id
                )
            except CredentialStoreError:
                if getattr(
                    self._feed_factories[connection.provider],
                    "requires_credentials",
                    True,
                ):
                    credential_missing = True
                    raise
                credential = ""
            feed.connect(connection.subscribed_instruments(), credential)
        except Exception as error:
            if feed is not None:
                try:
                    feed.disconnect()
                except Exception:
                    pass
            diagnostic_stage = getattr(error, "diagnostic_stage", None)
            safe_http_stage = False
            prefix = "authorization request: HTTP "
            if (
                isinstance(diagnostic_stage, str)
                and diagnostic_stage.startswith(prefix)
            ):
                status_code = diagnostic_stage.removeprefix(prefix)
                safe_http_stage = (
                    status_code.isdigit()
                    and 100 <= int(status_code) <= 599
                    and str(int(status_code)) == status_code
                )
            status_code = (
                diagnostic_stage.removeprefix(prefix)
                if safe_http_stage
                else ""
            )
            if credential_missing and connection.provider == "upstox":
                failure_message = "Credential required - update credential"
            elif (
                connection.provider == "upstox"
                and status_code in {"401", "403"}
            ):
                failure_message = "Credential invalid or expired - update credential"
            else:
                failure_message = "Provider connection failed"
            if (
                not credential_missing
                and not (
                    connection.provider == "upstox"
                    and status_code in {"401", "403"}
                )
                and (
                diagnostic_stage in _SAFE_PROVIDER_FAILURE_STAGES
                or safe_http_stage
                )
            ):
                failure_message += f": {diagnostic_stage}"
                if safe_http_stage:
                    diagnostic_code = getattr(error, "diagnostic_code", None)
                    if (
                        isinstance(diagnostic_code, str)
                        and _SAFE_PROVIDER_ERROR_CODE.fullmatch(diagnostic_code)
                        and not (
                            credential
                            and credential in diagnostic_code
                        )
                    ):
                        failure_message += f"; code={diagnostic_code}"
                    diagnostic_message = getattr(
                        error, "diagnostic_message", None
                    )
                    if (
                        isinstance(diagnostic_message, str)
                        and _SAFE_PROVIDER_ERROR_MESSAGE.fullmatch(
                            diagnostic_message
                        )
                        and not (
                            credential
                            and credential in diagnostic_message
                        )
                        and not _SENSITIVE_PROVIDER_ERROR_CONTENT.search(
                            diagnostic_message
                        )
                    ):
                        failure_message += (
                            f"; message={diagnostic_message}"
                        )

                    diagnostic_detail = getattr(
                        error, "diagnostic_detail", None
                    )
                    if (
                        isinstance(diagnostic_detail, str)
                        and _SAFE_PROVIDER_ERROR_MESSAGE.fullmatch(
                            diagnostic_detail
                        )
                        and not (
                            credential and credential in diagnostic_detail
                        )
                        and not _SENSITIVE_PROVIDER_ERROR_CONTENT.search(
                            diagnostic_detail
                        )
                    ):
                        failure_message += f"; detail={diagnostic_detail}"

                    diagnostic_error_name = getattr(
                        error, "diagnostic_error_name", None
                    )
                    if (
                        isinstance(diagnostic_error_name, str)
                        and _SAFE_PROVIDER_ERROR_NAME.fullmatch(
                            diagnostic_error_name
                        )
                        and not (
                            credential
                            and credential in diagnostic_error_name
                        )
                    ):
                        failure_message += (
                            f"; name={diagnostic_error_name}"
                        )

                    diagnostic_error_category = getattr(
                        error, "diagnostic_error_category", None
                    )
                    if (
                        isinstance(diagnostic_error_category, str)
                        and _SAFE_PROVIDER_ERROR_CATEGORY.fullmatch(
                            diagnostic_error_category
                        )
                        and not (
                            credential
                            and credential in diagnostic_error_category
                        )
                    ):
                        failure_message += (
                            f"; category={diagnostic_error_category}"
                        )

                    diagnostic_retryable = getattr(
                        error, "diagnostic_retryable", None
                    )
                    if type(diagnostic_retryable) is bool:
                        failure_message += (
                            f"; retryable={str(diagnostic_retryable).lower()}"
                        )

                    diagnostic_owner_action_required = getattr(
                        error, "diagnostic_owner_action_required", None
                    )
                    if type(diagnostic_owner_action_required) is bool:
                        failure_message += (
                            "; owner_action_required="
                            f"{str(diagnostic_owner_action_required).lower()}"
                        )

                    diagnostic_what_you_should_do = getattr(
                        error, "diagnostic_what_you_should_do", None
                    )
                    if (
                        isinstance(diagnostic_what_you_should_do, str)
                        and _SAFE_PROVIDER_ERROR_MESSAGE.fullmatch(
                            diagnostic_what_you_should_do
                        )
                        and not (
                            credential
                            and credential in diagnostic_what_you_should_do
                        )
                        and not _SENSITIVE_PROVIDER_ERROR_CONTENT.search(
                            diagnostic_what_you_should_do
                        )
                    ):
                        failure_message += (
                            "; what_you_should_do="
                            f"{diagnostic_what_you_should_do}"
                        )
            with self._lock:
                self._statuses[connection_id] = ConnectionStatus(
                    "error", failure_message
                )
            raise ConnectionManagerError(failure_message) from None

        active = _ActiveConnection(feed=feed, stop_event=threading.Event())
        thread = threading.Thread(
            target=self._consume,
            args=(connection, active),
            name=f"market-feed-{connection_id}",
            daemon=True,
        )
        active.thread = thread
        with self._lock:
            self._active[connection_id] = active
            self._statuses[connection_id] = ConnectionStatus("connected")
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._active.pop(connection_id, None)
                self._statuses[connection_id] = ConnectionStatus(
                    "error", "Could not start provider data processing"
                )
            try:
                feed.disconnect()
            except Exception:
                pass
            raise ConnectionManagerError(
                "Could not start provider data processing"
            ) from None

    def disconnect(self, connection_id: str) -> None:
        """Stop an active feed while preserving its saved credential."""
        with self._lock:
            self._require_connection(connection_id)
            active = self._active.pop(connection_id, None)
            self._statuses[connection_id] = ConnectionStatus("disconnected")
        if active is None:
            return

        active.stop_event.set()
        try:
            active.feed.disconnect()
        except Exception:
            with self._lock:
                self._statuses[connection_id] = ConnectionStatus(
                    "error", "Provider disconnect failed"
                )
            raise ConnectionManagerError("Provider disconnect failed") from None
        if active.thread is not None:
            active.thread.join()

    def delete_connection(self, connection_id: str) -> None:
        """Disconnect and remove both saved metadata and protected credential."""
        with self._lock:
            connection = self._require_connection(connection_id)
        self.disconnect(connection_id)
        try:
            requires_credentials = getattr(
                self._feed_factories[connection.provider],
                "requires_credentials",
                True,
            )
            try:
                self._credential_store.retrieve(
                    connection.provider, connection.connection_id
                )
            except CredentialStoreError:
                if requires_credentials:
                    raise
            else:
                self._credential_store.delete(
                    connection.provider, connection.connection_id
                )
        except Exception:
            raise ConnectionManagerError(
                "Could not remove the protected connection credential"
            ) from None

        with self._lock:
            del self._connections[connection_id]
            del self._statuses[connection_id]
            try:
                self._save_connections()
            except Exception:
                self._connections[connection_id] = connection
                self._statuses[connection_id] = ConnectionStatus("error")
                raise ConnectionManagerError(
                    "Credential removed, but connection metadata could not "
                    "be updated"
                ) from None

    def close(self) -> None:
        """Disconnect every active provider without deleting saved credentials."""
        with self._lock:
            connection_ids = list(self._active)
        errors = False
        for connection_id in connection_ids:
            try:
                self.disconnect(connection_id)
            except ConnectionManagerError:
                errors = True
        if errors:
            raise ConnectionManagerError(
                "One or more provider connections could not be disconnected"
            )

    def _consume(
        self, connection: Connection, active: _ActiveConnection
    ) -> None:
        stream_failed = False
        try:
            for update in active.feed.stream(connection.instrument):
                if active.stop_event.is_set():
                    break
                self._on_update(update)
                if self._on_market_timestamp is not None:
                    self._on_market_timestamp(
                        connection.provider,
                        update.instrument,
                        update.timestamp,
                    )
        except Exception as error:
            stream_failed = True
            credential = ""
            try:
                credential = self._credential_store.retrieve(
                    connection.provider, connection.connection_id
                )
            except Exception:
                pass
            with self._lock:
                self._statuses[connection.connection_id] = ConnectionStatus(
                    "error",
                    f"Provider data stream failed: "
                    f"{_safe_stream_error(error, credential)}",
                )
        finally:
            try:
                active.feed.disconnect()
            except Exception:
                with self._lock:
                    self._statuses[connection.connection_id] = ConnectionStatus(
                        "error", "Provider cleanup failed"
                    )

            with self._lock:
                if (
                    not stream_failed
                    and not active.stop_event.is_set()
                    and self._statuses[connection.connection_id].state
                    != "error"
                ):
                    self._statuses[connection.connection_id] = ConnectionStatus(
                        "disconnected"
                    )
                if self._active.get(connection.connection_id) is active:
                    self._active.pop(connection.connection_id, None)

    def _require_connection(self, connection_id: str) -> Connection:
        try:
            return self._connections[connection_id]
        except KeyError:
            raise ConnectionManagerError("Connection was not found") from None

    def _load_connections(self) -> dict[str, Connection]:
        try:
            raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            raise ConnectionManagerError(
                "Could not read saved connection metadata"
            ) from None
        if not isinstance(raw, list):
            raise ConnectionManagerError("Saved connection metadata is invalid")

        connections: dict[str, Connection] = {}
        try:
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError
                keys = _normalize_instrument_keys(
                    item.get("instruments") or (item.get("instrument"),)
                )
                connection = Connection(
                    connection_id=str(item["connection_id"]),
                    name=str(item["name"]),
                    provider=str(item["provider"]),
                    instrument=keys[0],
                    instruments=keys,
                )
                if (
                    not connection.connection_id
                    or not connection.name
                    or connection.provider not in self._feed_factories
                    or not connection.instrument
                ):
                    raise ValueError
                if connection.connection_id in connections:
                    raise ValueError
                connections[connection.connection_id] = connection
        except (TypeError, ValueError, KeyError):
            raise ConnectionManagerError(
                "Saved connection metadata is invalid"
            ) from None
        return connections

    def _save_connections(self) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [asdict(connection) for connection in self._connections.values()],
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._registry_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_file.write(payload)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(self._registry_path)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
