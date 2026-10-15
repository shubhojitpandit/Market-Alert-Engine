"""GUI-side session that composes existing backend services."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.active_alert_store import ActiveAlertStore, ActiveAlertStoreError
from data.base_feed import PriceUpdate
from data.connection_manager import (
    Connection,
    ConnectionManager,
    ConnectionManagerError,
    ConnectionStatus,
)
from data.delta_historical_data import DeltaHistoricalData
from data.credential_store import CredentialStore, CredentialStoreError, WindowsCredentialStore
from data.delta_feed import DeltaPriceFeed
from data.delta_instrument_resolver import (
    DeltaInstrumentResolver,
    DeltaInstrumentSearchError,
)
from data.instrument_search import (
    InstrumentResolver,
    InstrumentSearchAggregator,
    InstrumentSearchSource,
)
from data.instrument_store import InstrumentStore, InstrumentStoreError
from data.historical_candle import (
    HistoricalCandleProvider,
    HistoricalDataError,
)
from data.market_data_cursor import MarketDataCursorError, MarketDataCursorStore
from data.simulated_feed import SimulatedPriceFeed
from data.upstox_feed import UpstoxPriceFeed
from data.upstox_historical_data import UpstoxHistoricalData
from data.upstox_instrument_resolver import (
    UpstoxInstrumentResolver,
)
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from engine.missed_alert_recovery import MissedAlertRecovery
from gui.ui_queue import UiQueue
from history.alert_history import AlertHistory, AlertHistoryRecord
from models.alert import Alert, AlertCondition
from models.instrument import Instrument
from notifications.notification_handler import NotificationHandler
from notifications.notification_queue import NotificationProcessor, NotificationQueue
from notifications.sound_settings import (
    NotificationSoundSettings,
    SoundChoice,
)

# Matches UpstoxInstrumentResolver query limit without importing its private constant.
_SEARCH_QUERY_MAX_LENGTH = 50
_UPSTOX_ACCOUNT_ID = "account"
_DELTA_ACCOUNT_ID = "account"
_FOREX_API_KEY_ID = "api_key"
_FOREX_API_SECRET_ID = "api_secret"

ResolverFactory = Callable[[CredentialStore, str], InstrumentResolver]
_RECOVERY_WINDOW = timedelta(hours=24)


class GuiSession:
    """Command boundary for the GUI over the existing Market Alert Engine services."""

    def __init__(
        self,
        *,
        credential_store: CredentialStore | None = None,
        instrument_store: InstrumentStore | None = None,
        feed_factories: Mapping[str, Callable[[], object]] | None = None,
        registry_path: Path | None = None,
        notification_handler: NotificationProcessor | None = None,
        simulated_feed: SimulatedPriceFeed | None = None,
        instrument_resolver_factory: ResolverFactory | None = None,
    ) -> None:
        self.ui_queue = UiQueue()
        self.alert_manager = AlertManager(ActiveAlertStore.default_path())
        if self.alert_manager.storage_error is not None:
            self.ui_queue.put_alert_action(
                0, "restore", self.alert_manager.storage_error
            )
        self.notification_queue = NotificationQueue()
        self.alert_history = AlertHistory(AlertHistory.default_path())
        self.alert_engine = AlertEngine(
            self.alert_manager, self.notification_queue, self.alert_history
        )
        self.market_data_cursors = MarketDataCursorStore()
        if self.market_data_cursors.load_error is not None:
            self.ui_queue.put_alert_action(
                0, "recovery", self.market_data_cursors.load_error
            )
        if notification_handler is None:
            self.notification_sound_settings = NotificationSoundSettings()
            self.notification_handler = NotificationHandler(
                self.notification_sound_settings
            )
        elif isinstance(notification_handler, NotificationHandler):
            self.notification_sound_settings = notification_handler.sound_settings
            self.notification_handler = notification_handler
        else:
            self.notification_sound_settings = NotificationSoundSettings()
            self.notification_handler = notification_handler
        self.simulated_feed = simulated_feed or SimulatedPriceFeed(
            update_interval=0.02, seed=42
        )
        self.credential_store = credential_store or WindowsCredentialStore()
        self.instrument_store = instrument_store or InstrumentStore()
        self.connection_manager = ConnectionManager(
            credential_store=self.credential_store,
            feed_factories=feed_factories
            or {"upstox": UpstoxPriceFeed, "delta": DeltaPriceFeed},
            on_update=self._handle_price_update,
            registry_path=registry_path,
            on_market_timestamp=self._record_market_timestamp,
        )
        self._resolver_factory = instrument_resolver_factory
        self._state_lock = threading.Lock()
        self._closed = False
        self._startup_lock = threading.Lock()
        self._startup_started = False
        self._search_lock = threading.Lock()
        self._search_request_id = 0
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_request_id = 0
        self._inflight: dict[str, str] = {}
        self._pending_lifecycle: dict[str, str] = {}
        self._connect_generation: dict[str, int] = {}
        self._deleted_connections: set[str] = set()
        self._alert_lock = threading.Lock()
        self._alert_request_id = 0
        self._alert_state_lock = threading.Lock()
        self._latest_price_updates: dict[str, PriceUpdate] = {}
        self._notification_lock = threading.Lock()

    def is_closed(self) -> bool:
        """Return whether the session has been closed."""
        with self._state_lock:
            return self._closed

    def start_startup_recovery(self) -> None:
        """Reconnect saved feeds, or create a public Delta feed on first startup."""
        with self._startup_lock:
            if self._startup_started or self.is_closed():
                return
            self._startup_started = True
        connections = self.connection_manager.list_connections()
        if connections:
            for connection in connections:
                try:
                    self.connect_connection(connection.connection_id)
                except Exception:
                    self.ui_queue.put_connection_action(
                        0,
                        "connect",
                        connection.connection_id,
                        "Could not start the saved connection.",
                    )
            self._start_missed_alert_recovery()
            return
        thread = threading.Thread(
            target=self._start_initial_delta_connection,
            name="startup-delta-connection",
            daemon=True,
        )
        thread.start()

    def _start_initial_delta_connection(self) -> None:
        if self.is_closed():
            return
        try:
            delta_instruments = [
                item
                for item in self.instrument_store.list_instruments()
                if item.provider == "delta"
            ]
            if delta_instruments:
                instrument = delta_instruments[0]
            else:
                products = DeltaInstrumentResolver().search("BTC")
                if not products:
                    raise DeltaInstrumentSearchError(
                        "No active BTC product was returned."
                    )
                instrument = next(
                    (
                        product
                        for product in products
                        if product.trading_symbol == "BTCUSD"
                    ),
                    products[0],
                )
                self.instrument_store.add(instrument)
            if self.is_closed():
                return
            connection = self.connection_manager.create_connection(
                "CRYPTO",
                "delta",
                instrument.instrument_key,
                "",
                instruments=(instrument.instrument_key,),
            )
            self.ui_queue.put_connection_action(
                0,
                "create",
                connection.connection_id,
                "Starting CRYPTO public market data.",
            )
            self.connect_connection(connection.connection_id)
            self._start_missed_alert_recovery()
        except Exception:
            if not self.is_closed():
                self.ui_queue.put_connection_action(
                    0,
                    "create",
                    None,
                    "Could not start CRYPTO market data. Check your internet connection.",
                )

    def _handle_price_update(self, update: PriceUpdate) -> None:
        """Fan out one feed-thread update without touching Tk widgets."""
        if self.is_closed():
            return
        triggered: list = []
        try:
            with self._alert_state_lock:
                if self.is_closed():
                    return
                self._latest_price_updates[update.instrument] = update
                triggered = self.alert_engine.evaluate(update)
        except ValueError:
            triggered = []
        if self.is_closed():
            return
        self.ui_queue.put_price_update(update)
        if triggered:
            self.ui_queue.put_alert_action(0, "trigger", "Alert triggered.")
        with self._notification_lock:
            self.notification_queue.process_all(self.notification_handler)

    def _record_market_timestamp(
        self, provider: str, instrument: str, timestamp: datetime
    ) -> None:
        if self.is_closed():
            return
        try:
            self.market_data_cursors.record(provider, instrument, timestamp)
        except (
            MarketDataCursorError,
            OverflowError,
            TypeError,
            ValueError,
        ):
            self.ui_queue.put_alert_action(
                0,
                "recovery",
                "Could not save the market-data recovery cursor.",
            )

    def _start_missed_alert_recovery(self) -> None:
        thread = threading.Thread(
            target=self._run_missed_alert_recovery,
            name="missed-alert-recovery",
            daemon=True,
        )
        thread.start()

    def _run_missed_alert_recovery(self) -> None:
        if self.is_closed():
            return
        alerts_by_instrument: dict[str, list[Alert]] = {}
        for alert in self.alert_manager.list_active_alerts():
            alerts_by_instrument.setdefault(alert.symbol, []).append(alert)
        if not alerts_by_instrument:
            return

        connections_by_instrument: dict[str, list[Connection]] = {}
        for connection in self.connection_manager.list_connections():
            for instrument in connection.subscribed_instruments():
                connections_by_instrument.setdefault(instrument, []).append(
                    connection
                )

        now = datetime.now(timezone.utc)
        cursor_snapshot = self.market_data_cursors.snapshot()
        for instrument, alerts in alerts_by_instrument.items():
            if self.is_closed():
                return
            candidates = connections_by_instrument.get(instrument, [])
            providers = {connection.provider for connection in candidates}
            if not candidates:
                self._report_recovery(
                    f"Missed-alert recovery skipped for {instrument}: "
                    "no saved provider connection uses this instrument."
                )
                continue
            if len(providers) != 1:
                self._report_recovery(
                    f"Missed-alert recovery skipped for {instrument}: "
                    "multiple providers use the same instrument key."
                )
                continue
            connection = candidates[0]
            if connection.provider == "upstox":
                connection = next(
                    (
                        candidate
                        for candidate in candidates
                        if self._credential_exists(candidate.connection_id)
                    ),
                    connection,
                )
            try:
                provider = self._historical_provider(connection)
                cursor = cursor_snapshot.get((connection.provider, instrument))
                start = max(now - _RECOVERY_WINDOW, cursor or now - _RECOVERY_WINDOW)
                if cursor is None:
                    earliest_alert = min(
                        self._utc(alert.created_at) for alert in alerts
                    )
                    start = max(start, earliest_alert)
                if start >= now:
                    continue
                candles = provider.fetch_candles(instrument, start, now)
            except HistoricalDataError as error:
                self._report_recovery(
                    f"Missed-alert recovery unavailable for {instrument}: {error}"
                )
                continue
            except Exception:
                self._report_recovery(
                    f"Missed-alert recovery unavailable for {instrument}."
                )
                continue

            for alert in alerts:
                trigger = MissedAlertRecovery.find_trigger(
                    alert,
                    candles,
                    start,
                    now,
                    provider.interval_seconds,
                )
                if trigger is None:
                    continue
                try:
                    with self._alert_state_lock:
                        if self.is_closed() or not any(
                            active.alert_id == alert.alert_id
                            for active in self.alert_manager.list_active_alerts()
                        ):
                            continue
                        self.alert_engine.complete_recovered_trigger(
                            alert.alert_id,
                            trigger.current_price,
                            trigger.triggered_at,
                        )
                    self._report_recovery(
                        f"Recovered {alert.alert_id} using historical "
                        "market data; trigger time is approximate."
                    )
                except (ActiveAlertStoreError, RuntimeError, ValueError, KeyError):
                    self._report_recovery(
                        f"Could not complete recovered alert {alert.alert_id}; "
                        "it remains active."
                    )
                    continue
                with self._notification_lock:
                    try:
                        self.notification_queue.process_all(
                            self.notification_handler
                        )
                    except Exception:
                        self._report_recovery(
                            f"Recovered {alert.alert_id}, but its notification "
                            "could not be delivered."
                        )

    def _historical_provider(
        self, connection: Connection
    ) -> HistoricalCandleProvider:
        if connection.provider == "delta":
            return DeltaHistoricalData()
        if connection.provider == "upstox":
            return UpstoxHistoricalData(
                self.credential_store, connection.connection_id
            )
        raise HistoricalDataError(
            "Historical recovery is unsupported for this provider."
        )

    @staticmethod
    def _utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    def _report_recovery(self, message: str) -> None:
        if not self.is_closed():
            self.ui_queue.put_alert_action(0, "recovery", message)

    def list_alert_sounds(self) -> list[SoundChoice]:
        return self.notification_sound_settings.list_sounds()

    def selected_alert_sound(self) -> str:
        return self.notification_sound_settings.selected_sound_id()

    def alert_sound_playback(self) -> str:
        return self.notification_sound_settings.playback()

    def import_alert_sound(self, path: Path) -> SoundChoice:
        return self.notification_sound_settings.import_sound(path)

    def select_alert_sound(self, sound_id: str) -> None:
        self.notification_sound_settings.select(sound_id)

    def set_alert_sound_playback(self, playback: str) -> None:
        self.notification_sound_settings.set_playback(playback)

    def test_alert_sound(self) -> None:
        if isinstance(self.notification_handler, NotificationHandler):
            self.notification_handler.play_current_sound()
            return
        NotificationHandler(self.notification_sound_settings).play_current_sound()

    def list_saved_instruments(self) -> list[Instrument]:
        """Return persisted instruments from InstrumentStore."""
        return self.instrument_store.list_instruments()

    def save_instrument(self, instrument: Instrument) -> Instrument:
        """Persist one Instrument through InstrumentStore."""
        return self.instrument_store.add(instrument)

    def remove_instrument(self, instrument_key: str) -> None:
        """Remove a saved instrument without changing connections or credentials."""
        self.instrument_store.remove(instrument_key)

    def connections_using_instrument(self, instrument_key: str) -> list[Connection]:
        """Return connections whose subscription list includes instrument_key."""
        key = instrument_key.strip()
        return [
            connection
            for connection in self.connection_manager.list_connections()
            if key in connection.subscribed_instruments()
        ]

    def instrument_removal_warning(self, instrument_key: str) -> str | None:
        """Return a warning if a saved connection still lists this instrument."""
        connections = self.connections_using_instrument(instrument_key)
        if not connections:
            return None
        names = ", ".join(connection.name for connection in connections)
        return (
            f"This instrument is used by connection(s): {names}. "
            "Removing it from Saved Instruments does not remove it from the "
            "connection."
        )

    def upstox_search_connection_id(self) -> str | None:
        """Return a stored Upstox credential id that can authorize search, if any."""
        return self._upstox_credential_id()

    def has_upstox_credential(self) -> bool:
        """Return whether a reusable Upstox credential is already stored."""
        return self._upstox_credential_id() is not None

    def save_upstox_credential(self, credential: str) -> None:
        """Store the Upstox token in CredentialStore; do not keep it on the session."""
        trimmed = credential.strip() if isinstance(credential, str) else ""
        if not trimmed:
            raise ValueError("An INDIAN MARKET API Key is required.")
        if self.is_closed():
            raise ConnectionManagerError("Session is closed.")
        self.credential_store.save("upstox", _UPSTOX_ACCOUNT_ID, trimmed)
        for connection in self.connection_manager.list_connections():
            if connection.provider == "upstox":
                self.credential_store.save(
                    "upstox", connection.connection_id, trimmed
                )

    def save_delta_credentials(self, api_key: str, api_secret: str) -> None:
        """Store optional Delta API credentials in the protected credential store."""
        trimmed_key = api_key.strip() if isinstance(api_key, str) else ""
        trimmed_secret = api_secret.strip() if isinstance(api_secret, str) else ""
        if not trimmed_key or not trimmed_secret:
            raise ValueError("Enter both the API Key and API Secret.")
        if self.is_closed():
            raise ConnectionManagerError("Session is closed.")
        serialized = json.dumps(
            {"api_key": trimmed_key, "api_secret": trimmed_secret}
        )
        self.credential_store.save("delta", _DELTA_ACCOUNT_ID, serialized)
        for connection in self.connection_manager.list_connections():
            if connection.provider == "delta":
                self.credential_store.save(
                    "delta", connection.connection_id, serialized
                )

    def has_delta_credentials(self) -> bool:
        try:
            return bool(
                self.credential_store.retrieve("delta", _DELTA_ACCOUNT_ID)
            )
        except (CredentialStoreError, KeyError, ValueError):
            return False

    def save_forex_credentials(self, api_key: str, api_secret: str) -> None:
        """Store future Forex-provider credentials separately and securely."""
        trimmed_key = api_key.strip() if isinstance(api_key, str) else ""
        trimmed_secret = api_secret.strip() if isinstance(api_secret, str) else ""
        if not trimmed_key or not trimmed_secret:
            raise ValueError("Enter both the API Key and API Secret.")
        if self.is_closed():
            raise ConnectionManagerError("Session is closed.")
        self.credential_store.save("forex", _FOREX_API_KEY_ID, trimmed_key)
        try:
            self.credential_store.save(
                "forex", _FOREX_API_SECRET_ID, trimmed_secret
            )
        except CredentialStoreError:
            self._delete_stored_credential(_FOREX_API_KEY_ID, "forex")
            raise

    def remove_forex_credentials(self) -> None:
        """Remove saved Forex-provider credentials without managing a feed."""
        self._delete_stored_credential(_FOREX_API_KEY_ID, "forex")
        self._delete_stored_credential(_FOREX_API_SECRET_ID, "forex")

    def disconnect_upstox_account(self) -> None:
        """Stop Upstox feeds and remove stored Upstox credentials, keeping metadata."""
        if self.is_closed():
            return
        connection_ids = [
            connection.connection_id
            for connection in self.connection_manager.list_connections()
            if connection.provider == "upstox"
        ]
        self._delete_stored_credential(_UPSTOX_ACCOUNT_ID)
        for connection_id in connection_ids:
            self._delete_stored_credential(connection_id)
            self.disconnect_connection(connection_id)

    def disconnect_delta_account(self) -> None:
        """Stop Delta feeds and remove provider-scoped stored API credentials."""
        if self.is_closed():
            return
        connection_ids = [
            connection.connection_id
            for connection in self.connection_manager.list_connections()
            if connection.provider == "delta"
        ]
        self._delete_stored_credential(_DELTA_ACCOUNT_ID, "delta")
        for connection_id in connection_ids:
            self._delete_stored_credential(connection_id, "delta")
            self.disconnect_connection(connection_id)

    def _delete_stored_credential(
        self, credential_id: str, provider: str = "upstox"
    ) -> None:
        try:
            self.credential_store.delete(provider, credential_id)
        except (CredentialStoreError, KeyError, ValueError):
            pass

    def _upstox_credential_id(self) -> str | None:
        if self._credential_exists(_UPSTOX_ACCOUNT_ID):
            return _UPSTOX_ACCOUNT_ID
        for connection in self.connection_manager.list_connections():
            if connection.provider == "upstox" and self._credential_exists(
                connection.connection_id
            ):
                return connection.connection_id
        return None

    def _credential_exists(self, credential_id: str) -> bool:
        try:
            stored = self.credential_store.retrieve("upstox", credential_id)
        except (CredentialStoreError, KeyError, ValueError):
            return False
        return bool(stored)

    def _retrieve_upstox_credential(self) -> str:
        credential_id = self._upstox_credential_id()
        if credential_id is None:
            raise ValueError("An INDIAN MARKET API Key is required.")
        try:
            return self.credential_store.retrieve("upstox", credential_id)
        except (CredentialStoreError, KeyError, ValueError):
            raise ValueError("An INDIAN MARKET API Key is required.") from None

    def validate_search_query(self, query: str) -> str:
        """Normalize a search query or raise ValueError without calling the resolver."""
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must not be empty")
        if len(normalized) > _SEARCH_QUERY_MAX_LENGTH:
            raise ValueError("search query is too long")
        return normalized

    def start_instrument_search(
        self,
        query: str,
        *,
        credential: str | None = None,
    ) -> int:
        """Search all configured providers on a worker thread."""
        if self.is_closed():
            return 0
        normalized = self.validate_search_query(query)
        if credential:
            self.save_upstox_credential(credential)
        credential_id = self._upstox_credential_id()

        with self._search_lock:
            self._search_request_id += 1
            request_id = self._search_request_id

        thread = threading.Thread(
            target=self._run_global_instrument_search,
            args=(request_id, normalized, credential_id),
            name=f"instrument-search-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def _run_global_instrument_search(
        self,
        request_id: int,
        query: str,
        credential_id: str | None,
    ) -> None:
        if self.is_closed():
            return
        sources = [InstrumentSearchSource("CRYPTO", DeltaInstrumentResolver())]
        warnings: list[str] = []
        if credential_id is None:
            warnings.append(
                "INDIAN MARKET not searched: configure its API Key."
            )
        else:
            try:
                upstox_resolver = self._make_resolver(
                    self.credential_store, credential_id
                )
            except Exception:
                warnings.append("INDIAN MARKET search failed.")
            else:
                sources.insert(
                    0,
                    InstrumentSearchSource("INDIAN MARKET", upstox_resolver),
                )
        results, failures = InstrumentSearchAggregator().search(query, sources)
        warnings.extend(failures)
        self._finish_search(
            request_id,
            results=results,
            warning=" ".join(warnings) or None,
        )

    def current_search_request_id(self) -> int:
        """Return the newest search request id."""
        with self._search_lock:
            return self._search_request_id

    def _finish_search(
        self,
        request_id: int,
        *,
        results: list[Instrument] | None = None,
        error: str | None = None,
        warning: str | None = None,
    ) -> None:
        with self._search_lock:
            if self.is_closed() or request_id != self._search_request_id:
                return
            if error is not None:
                self.ui_queue.put_search_error(request_id, error)
            else:
                self.ui_queue.put_search_result(
                    request_id, results or [], warning
                )

    def _make_resolver(
        self, store: CredentialStore, credential_id: str
    ) -> InstrumentResolver:
        if self._resolver_factory is not None:
            return self._resolver_factory(store, credential_id)
        return UpstoxInstrumentResolver(store, credential_id)

    @staticmethod
    def _safe_ui_error(error: BaseException, credential: str = "") -> str:
        text = str(error)
        if credential and credential in text:
            text = text.replace(credential, "")
        text = " ".join(text.split())
        return text or "The operation failed."

    def get_instrument_display_name(self, instrument_key: str) -> str:
        """Return a saved trading symbol, or the instrument key if unknown."""
        key = instrument_key.strip()
        if not key:
            return instrument_key
        try:
            return self.instrument_store.get(key).trading_symbol
        except (InstrumentStoreError, ValueError):
            return key

    def get_instrument_label(self, instrument_key: str) -> str:
        """Return a user-facing name without exposing the internal key."""
        key = instrument_key.strip()
        if not key:
            return instrument_key
        try:
            instrument = self.instrument_store.get(key)
        except (InstrumentStoreError, ValueError):
            return key
        return instrument.name or instrument.trading_symbol or key

    def connection_instrument_labels(self, connection: Connection) -> str:
        """Return display names for subscribed keys using InstrumentStore when possible."""
        return ", ".join(
            self.get_instrument_label(key)
            for key in connection.subscribed_instruments()
        )

    @staticmethod
    def connection_status_text(status: ConnectionStatus) -> str:
        """Return a short UI label for ConnectionManager status."""
        if status.state == "disconnected":
            return "Disconnected"
        if status.state == "connecting":
            return "Connecting..."
        if status.state == "connected":
            return "Connected"
        if status.state == "error":
            return status.error or "Connection failed"
        return status.state

    def _next_lifecycle_request_id(self) -> int:
        with self._lifecycle_lock:
            return self._next_lifecycle_request_id_locked()

    def _next_lifecycle_request_id_locked(self) -> int:
        self._lifecycle_request_id += 1
        return self._lifecycle_request_id

    def create_upstox_connection(
        self,
        name: str,
        instrument_keys: Sequence[str],
        credential: str = "",
    ) -> int:
        """Validate and create an Upstox connection on a worker thread."""
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("connection name and instrument must not be empty")
        keys = tuple(
            key.strip() for key in instrument_keys if isinstance(key, str) and key.strip()
        )
        if not keys:
            raise ValueError("Select one or more saved instruments.")
        if self.is_closed():
            return 0
        provided = credential.strip() if isinstance(credential, str) else ""
        if provided:
            self.save_upstox_credential(provided)
        elif not self.has_upstox_credential():
            raise ValueError("An INDIAN MARKET API Key is required.")
        request_id = self._next_lifecycle_request_id()
        thread = threading.Thread(
            target=self._run_create_upstox_connection,
            args=(request_id, trimmed_name, keys),
            name=f"connection-create-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def create_connections_for_instruments(
        self,
        name: str,
        instrument_keys: Sequence[str],
        *,
        upstox_credential: str = "",
        delta_api_key: str = "",
        delta_api_secret: str = "",
    ) -> tuple[int, ...]:
        """Create existing provider-specific connections for saved instruments."""
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("connection name and instrument must not be empty")
        instruments = []
        for key in instrument_keys:
            if not isinstance(key, str) or not key.strip():
                continue
            try:
                instruments.append(self.instrument_store.get(key))
            except InstrumentStoreError:
                raise ValueError("Select saved instruments again.") from None
        if not instruments:
            raise ValueError("Select one or more saved instruments.")

        grouped: dict[str, list[str]] = {}
        for instrument in instruments:
            grouped.setdefault(instrument.provider, []).append(
                instrument.instrument_key
            )
        unsupported = set(grouped).difference({"upstox", "delta"})
        if unsupported:
            raise ValueError(
                "No connection feed is configured for the selected instruments."
            )
        if grouped.get("delta") and bool(delta_api_key) != bool(delta_api_secret):
            raise ValueError("Enter both the API Key and API Secret.")

        request_ids: list[int] = []
        if "upstox" in grouped:
            request_ids.append(
                self.create_upstox_connection(
                    trimmed_name,
                    grouped["upstox"],
                    upstox_credential,
                )
            )
        if "delta" in grouped:
            request_ids.append(
                self.create_delta_connection(
                    trimmed_name,
                    grouped["delta"],
                    delta_api_key,
                    delta_api_secret,
                )
            )
        return tuple(request_ids)

    def create_delta_connection(
        self,
        name: str,
        instrument_keys: Sequence[str],
        api_key: str = "",
        api_secret: str = "",
    ) -> int:
        """Create a public Delta feed connection with optional protected API keys."""
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("connection name and instrument must not be empty")
        keys = tuple(
            key.strip()
            for key in instrument_keys
            if isinstance(key, str) and key.strip()
        )
        if not keys:
            raise ValueError("Select one or more saved instruments.")
        if self.is_closed():
            return 0
        api_key = api_key.strip()
        api_secret = api_secret.strip()
        if bool(api_key) != bool(api_secret):
            raise ValueError("Enter both the API Key and API Secret.")
        if api_key and api_secret:
            self.save_delta_credentials(api_key, api_secret)
        elif self.has_delta_credentials():
            try:
                stored_credentials = json.loads(
                    self.credential_store.retrieve("delta", _DELTA_ACCOUNT_ID)
                )
                api_key = stored_credentials["api_key"]
                api_secret = stored_credentials["api_secret"]
            except (CredentialStoreError, KeyError, TypeError, ValueError):
                raise ValueError(
                    "Stored CRYPTO API credentials could not be read."
                ) from None
        request_id = self._next_lifecycle_request_id()
        thread = threading.Thread(
            target=self._run_create_delta_connection,
            args=(request_id, trimmed_name, keys, api_key, api_secret),
            name=f"connection-create-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def _run_create_delta_connection(
        self,
        request_id: int,
        name: str,
        keys: tuple[str, ...],
        api_key: str,
        api_secret: str,
    ) -> None:
        if self.is_closed():
            return
        connection_id: str | None = None
        message = "Connection created."
        credential = ""
        try:
            if api_key and api_secret:
                credential = json.dumps(
                    {"api_key": api_key, "api_secret": api_secret}
                )
            connection = self.connection_manager.create_connection(
                name, "delta", keys[0], credential, instruments=keys
            )
            connection_id = connection.connection_id
        except (ConnectionManagerError, ValueError, CredentialStoreError) as error:
            message = self._safe_ui_error(error, credential)
        except Exception:
            message = "Could not create the connection."
        finally:
            api_key = ""
            api_secret = ""
            credential = ""
        if self.is_closed():
            if connection_id is not None:
                try:
                    self.connection_manager.delete_connection(connection_id)
                except Exception:
                    pass
            return
        self.ui_queue.put_connection_action(
            request_id, "create", connection_id, message
        )

    def connect_connection(self, connection_id: str) -> int:
        """Connect on a worker thread using the stored credential."""
        if self.is_closed():
            return 0
        with self._lifecycle_lock:
            if connection_id in self._deleted_connections:
                return 0
            state = self._inflight.get(connection_id)
            if state in ("connecting", "disconnecting", "deleting"):
                return 0
            self._inflight[connection_id] = "connecting"
            self._connect_generation[connection_id] = (
                self._connect_generation.get(connection_id, 0) + 1
            )
            generation = self._connect_generation[connection_id]
            request_id = self._next_lifecycle_request_id_locked()
        thread = threading.Thread(
            target=self._run_connection_action,
            args=(request_id, "connect", connection_id, generation),
            name=f"connection-connect-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def disconnect_connection(self, connection_id: str) -> int:
        """Disconnect on a worker thread without deleting the credential."""
        if self.is_closed():
            return 0
        with self._lifecycle_lock:
            if connection_id in self._deleted_connections:
                return 0
            if self._inflight.get(connection_id) == "connecting":
                self._pending_lifecycle[connection_id] = "disconnect"
                return 0
            self._inflight[connection_id] = "disconnecting"
            request_id = self._next_lifecycle_request_id_locked()
        thread = threading.Thread(
            target=self._run_connection_action,
            args=(request_id, "disconnect", connection_id, None),
            name=f"connection-disconnect-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def start_delete_connection(self, connection_id: str) -> int:
        """Delete on a worker thread via ConnectionManager.delete_connection."""
        if self.is_closed():
            return 0
        with self._lifecycle_lock:
            if connection_id in self._deleted_connections:
                return 0
            if self._inflight.get(connection_id) == "connecting":
                self._pending_lifecycle[connection_id] = "delete"
                return 0
            self._inflight[connection_id] = "deleting"
            request_id = self._next_lifecycle_request_id_locked()
        thread = threading.Thread(
            target=self._run_connection_action,
            args=(request_id, "delete", connection_id, None),
            name=f"connection-delete-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def _run_create_upstox_connection(
        self,
        request_id: int,
        name: str,
        keys: tuple[str, ...],
    ) -> None:
        if self.is_closed():
            return
        connection_id: str | None = None
        message = "Connection created."
        credential = ""
        try:
            credential = self._retrieve_upstox_credential()
            connection = self.connection_manager.create_connection(
                name, "upstox", keys[0], credential, instruments=keys
            )
            connection_id = connection.connection_id
        except (ConnectionManagerError, ValueError, CredentialStoreError) as error:
            message = self._safe_ui_error(error, credential)
        except Exception:
            message = "Could not create the connection."
        finally:
            credential = ""
        if self.is_closed():
            if connection_id is not None:
                try:
                    self.connection_manager.delete_connection(connection_id)
                except Exception:
                    pass
            return
        self.ui_queue.put_connection_action(
            request_id, "create", connection_id, message
        )

    def _run_connection_action(
        self,
        request_id: int,
        action: str,
        connection_id: str,
        generation: int | None = None,
    ) -> None:
        if self.is_closed() and action == "connect":
            self._clear_inflight(connection_id)
            return
        message = {
            "connect": "Connected",
            "disconnect": "Disconnected",
            "delete": "Connection deleted.",
        }.get(action, "Done.")
        completed_id: str | None = connection_id
        failed = False
        try:
            if action == "connect":
                self.connection_manager.connect(connection_id)
            elif action == "disconnect":
                self.connection_manager.disconnect(connection_id)
            elif action == "delete":
                self.connection_manager.delete_connection(connection_id)
                completed_id = connection_id
            else:
                message = "Unknown connection action."
        except (ConnectionManagerError, ValueError, CredentialStoreError) as error:
            message = self._safe_ui_error(error)
            failed = True
        except Exception:
            message = "The connection operation failed."
            failed = True

        if action == "connect":
            self._finish_connect(
                request_id, connection_id, generation, failed, message
            )
            return
        with self._lifecycle_lock:
            self._inflight.pop(connection_id, None)
            if action == "delete":
                self._deleted_connections.add(connection_id)
        if self.is_closed():
            return
        self.ui_queue.put_connection_action(
            request_id, action, completed_id, message
        )

    def _finish_connect(
        self,
        request_id: int,
        connection_id: str,
        generation: int | None,
        failed: bool,
        message: str,
    ) -> None:
        with self._lifecycle_lock:
            pending = self._pending_lifecycle.pop(connection_id, None)
            current_generation = self._connect_generation.get(connection_id)
            stale = generation is not None and generation != current_generation
            self._inflight.pop(connection_id, None)
        if self.is_closed() or stale:
            try:
                self.connection_manager.disconnect(connection_id)
            except Exception:
                pass
            return
        if pending == "delete":
            try:
                self.connection_manager.delete_connection(connection_id)
                with self._lifecycle_lock:
                    self._deleted_connections.add(connection_id)
                if not self.is_closed():
                    self.ui_queue.put_connection_action(
                        request_id,
                        "delete",
                        connection_id,
                        "Connection deleted.",
                    )
            except Exception:
                if not self.is_closed():
                    self.ui_queue.put_connection_action(
                        request_id,
                        "delete",
                        connection_id,
                        "Could not delete the connection.",
                    )
            return
        if pending == "disconnect":
            try:
                self.connection_manager.disconnect(connection_id)
                if not self.is_closed():
                    self.ui_queue.put_connection_action(
                        request_id, "disconnect", connection_id, "Disconnected"
                    )
            except Exception:
                if not self.is_closed():
                    self.ui_queue.put_connection_action(
                        request_id,
                        "disconnect",
                        connection_id,
                        "Could not disconnect.",
                    )
            return
        if self.is_closed():
            return
        self.ui_queue.put_connection_action(
            request_id, "connect", connection_id, message
        )

    def _clear_inflight(self, connection_id: str) -> None:
        with self._lifecycle_lock:
            self._inflight.pop(connection_id, None)
            self._pending_lifecycle.pop(connection_id, None)

    def search_instruments(
        self, query: str, credential_id: str
    ) -> list[Instrument]:
        """Search via UpstoxInstrumentResolver; returns Instrument objects only."""
        resolver = self._make_resolver(self.credential_store, credential_id)
        return resolver.search(query)

    def create_connection(
        self,
        name: str,
        provider: str,
        instrument: str,
        credential: str,
        instruments: Sequence[str] | None = None,
    ) -> Connection:
        """Create a connection; the credential is passed through and not retained."""
        if self.is_closed():
            raise ConnectionManagerError("Session is closed.")
        return self.connection_manager.create_connection(
            name, provider, instrument, credential, instruments=instruments
        )

    def list_connections(self) -> list[Connection]:
        """Return saved connection metadata without credentials."""
        return self.connection_manager.list_connections()

    def connect(self, connection_id: str) -> None:
        """Start the existing single-stream provider connection."""
        if self.is_closed():
            raise ConnectionManagerError("Session is closed.")
        self.connection_manager.connect(connection_id)

    def disconnect(self, connection_id: str) -> None:
        """Stop an active feed while preserving its saved credential."""
        if self.is_closed():
            return
        self.connection_manager.disconnect(connection_id)

    def delete_connection(self, connection_id: str) -> None:
        """Delete one saved connection and its protected credential."""
        if self.is_closed():
            return
        self.connection_manager.delete_connection(connection_id)

    def get_connection_status(self, connection_id: str) -> ConnectionStatus:
        """Return the connection manager's safe-to-display status."""
        return self.connection_manager.get_status(connection_id)

    def available_alert_instruments(self) -> list[tuple[str, str]]:
        """Return (instrument_key, display_name) pairs from InstrumentStore."""
        return [
            (
                instrument.instrument_key,
                instrument.name or instrument.trading_symbol,
            )
            for instrument in self.list_saved_instruments()
        ]

    def create_alert(
        self, symbol: str, target_price: float, condition: AlertCondition
    ) -> Alert:
        """Register an alert and evaluate it against the latest live price."""
        if self.is_closed():
            raise ValueError("Session is closed.")
        with self._alert_state_lock:
            if self.is_closed():
                raise ValueError("Session is closed.")
            alert = self.alert_manager.create_alert(
                symbol, target_price, condition
            )
            update = self._latest_price_updates.get(symbol)
            triggered = (
                self.alert_engine.evaluate_new_alert(alert, update)
                if update is not None
                else None
            )
        if triggered is not None:
            self.ui_queue.put_alert_action(0, "trigger", "Alert triggered.")
            with self._notification_lock:
                self.notification_queue.process_all(self.notification_handler)
        return alert

    def cancel_alert(self, alert_id: str) -> Alert:
        """Cancel an active alert through AlertManager."""
        if self.is_closed():
            raise ValueError("Session is closed.")
        return self.alert_manager.cancel_alert(alert_id)

    def list_active_alerts(self) -> list[Alert]:
        """Return active alerts from AlertManager."""
        return self.alert_manager.list_active_alerts()

    def list_alert_history(self) -> list[AlertHistoryRecord]:
        """Return triggered-alert records from AlertHistory."""
        return self.alert_history.get_all()

    def get_alert_history(self) -> list[AlertHistoryRecord]:
        """Return triggered-alert records from AlertHistory."""
        return self.list_alert_history()

    def start_create_alert(
        self, symbol: str, target_price: object, condition: str
    ) -> int:
        """Validate inputs and create an alert on a worker thread."""
        if self.is_closed():
            return 0
        key = symbol.strip()
        if not key:
            raise ValueError("Select an instrument.")
        try:
            parsed_condition = AlertCondition(condition.strip().upper())
        except ValueError:
            raise ValueError("unsupported alert condition") from None
        try:
            if isinstance(target_price, bool):
                raise ValueError
            price = float(target_price)  # type: ignore[arg-type]
        except (OverflowError, TypeError, ValueError):
            raise ValueError("Threshold must be a number.") from None
        request_id = self._next_alert_request_id()
        thread = threading.Thread(
            target=self._run_create_alert,
            args=(request_id, key, price, parsed_condition),
            name=f"alert-create-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def start_cancel_alert(self, alert_id: str) -> int:
        """Cancel an alert on a worker thread via AlertManager."""
        if self.is_closed():
            return 0
        trimmed = alert_id.strip()
        if not trimmed:
            raise ValueError("Select an alert to cancel.")
        request_id = self._next_alert_request_id()
        thread = threading.Thread(
            target=self._run_cancel_alert,
            args=(request_id, trimmed),
            name=f"alert-cancel-{request_id}",
            daemon=True,
        )
        thread.start()
        return request_id

    def _next_alert_request_id(self) -> int:
        with self._alert_lock:
            self._alert_request_id += 1
            return self._alert_request_id

    def _run_create_alert(
        self,
        request_id: int,
        symbol: str,
        target_price: float,
        condition: AlertCondition,
    ) -> None:
        if self.is_closed():
            return
        message = "Alert created."
        try:
            alert = self.create_alert(symbol, target_price, condition)
            message = f"Created {alert.alert_id}."
        except ValueError as error:
            message = self._safe_ui_error(error)
        except ActiveAlertStoreError as error:
            message = self._safe_ui_error(error)
        except Exception:
            message = "Could not create the alert."
        if self.is_closed():
            return
        self.ui_queue.put_alert_action(request_id, "create", message)

    def _run_cancel_alert(self, request_id: int, alert_id: str) -> None:
        if self.is_closed():
            return
        message = "Alert cancelled."
        try:
            with self._alert_state_lock:
                if self.is_closed():
                    return
                alert = self.alert_manager.cancel_alert(alert_id)
            message = f"Cancelled {alert.alert_id}."
        except KeyError:
            message = "Alert was not found."
        except ValueError as error:
            message = self._safe_ui_error(error)
        except ActiveAlertStoreError as error:
            message = self._safe_ui_error(error)
        except Exception:
            message = "Could not cancel the alert."
        if self.is_closed():
            return
        self.ui_queue.put_alert_action(request_id, "cancel", message)

    def close(self, *, block: bool = True, timeout: float = 0.25) -> None:
        """Mark the session closed and disconnect providers without deleting credentials."""
        with self._state_lock:
            already = self._closed
            self._closed = True
        self.ui_queue.close()
        if already:
            return
        if block:
            try:
                self.connection_manager.close()
            except ConnectionManagerError:
                pass
            self._flush_market_data_cursors()
            return
        thread = threading.Thread(
            target=self._backend_close,
            name="session-close",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=timeout)

    def _backend_close(self) -> None:
        try:
            self.connection_manager.close()
        except ConnectionManagerError:
            pass
        finally:
            self._flush_market_data_cursors()

    def _flush_market_data_cursors(self) -> None:
        try:
            self.market_data_cursors.flush()
        except MarketDataCursorError as error:
            print(
                f"Could not save market-data recovery cursors: {error}",
                file=sys.stderr,
            )
