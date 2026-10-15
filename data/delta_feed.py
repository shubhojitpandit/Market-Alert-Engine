"""Delta Exchange India public ticker WebSocket adapter."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from importlib import import_module
from typing import Callable, Iterator

from data.base_feed import PriceFeed, PriceUpdate

DELTA_WEBSOCKET_URL = "wss://public-socket.india.delta.exchange"
_DELTA_INSTRUMENT_PREFIX = "delta:"


class DeltaFeedError(RuntimeError):
    """Raised when Delta cannot provide valid live market data."""

    def __init__(self, message: str, *, diagnostic_stage: str | None = None) -> None:
        super().__init__(message)
        self.diagnostic_stage = diagnostic_stage


def delta_symbol(instrument_key: str) -> str:
    """Return the exchange ticker symbol stored in a provider-scoped key."""
    key = instrument_key.strip()
    if key.startswith(_DELTA_INSTRUMENT_PREFIX):
        key = key[len(_DELTA_INSTRUMENT_PREFIX) :]
    if not key:
        raise ValueError("instrument must not be empty")
    return key


class DeltaPriceFeed(PriceFeed):
    """Subscribe to multiple Delta ticker symbols on its public WebSocket."""

    requires_credentials = False

    def __init__(
        self,
        *,
        websocket_connect: Callable[..., object] | None = None,
        websocket_url: str = DELTA_WEBSOCKET_URL,
        timeout: float = 10.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._websocket_connect = websocket_connect
        self._websocket_url = websocket_url
        self._timeout = timeout
        self._websocket: object | None = None
        self._instruments: dict[str, str] = {}
        self._lock = threading.Lock()

    def connect(
        self, instrument: str | Sequence[str], credential: str = ""
    ) -> None:
        """Connect and subscribe to public ticker data; credentials are not used."""
        values = (instrument,) if isinstance(instrument, str) else instrument
        instruments: dict[str, str] = {}
        for value in values:
            if not isinstance(value, str):
                raise ValueError("instrument must not be empty")
            key = value.strip()
            if key:
                instruments[key] = delta_symbol(key)
        if not instruments:
            raise ValueError("instrument must not be empty")
        with self._lock:
            if self._websocket is not None:
                raise DeltaFeedError("Delta feed is already connected")

        try:
            websocket = self._get_websocket_connect()(
                self._websocket_url, open_timeout=self._timeout
            )
        except Exception:
            raise DeltaFeedError(
                "Delta WebSocket connection failed",
                diagnostic_stage="WebSocket connection",
            ) from None
        try:
            request = {
                "type": "subscribe",
                "payload": {
                    "channels": [
                        {
                            "name": "ticker",
                            "symbols": list(instruments.values()),
                        }
                    ]
                },
            }
            websocket.send(json.dumps(request))
        except Exception:
            try:
                websocket.close()
            except Exception:
                pass
            raise DeltaFeedError(
                "Delta WebSocket subscription failed",
                diagnostic_stage="subscription",
            ) from None

        with self._lock:
            self._websocket = websocket
            self._instruments = instruments

    def disconnect(self) -> None:
        """Close the socket without affecting saved provider credentials."""
        with self._lock:
            websocket = self._websocket
            self._websocket = None
            self._instruments = {}
        if websocket is None:
            return
        try:
            websocket.close()
        except Exception:
            raise DeltaFeedError("Could not close the Delta WebSocket") from None

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        """Yield standardized ticker updates from the active socket."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        if count is not None and count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return
        with self._lock:
            websocket = self._websocket
            subscribed = dict(self._instruments)
        if websocket is None or instrument not in subscribed:
            raise DeltaFeedError("Delta feed is not connected to this instrument")

        emitted = 0
        while count is None or emitted < count:
            try:
                payload = websocket.recv(timeout=1)
            except TimeoutError:
                with self._lock:
                    if self._websocket is not websocket:
                        return
                continue
            except Exception:
                with self._lock:
                    if self._websocket is not websocket:
                        return
                raise DeltaFeedError("Delta WebSocket receive failed") from None
            updates = self._decode(payload, subscribed)
            for update in updates:
                yield update
                emitted += 1
                if count is not None and emitted >= count:
                    return

    def _get_websocket_connect(self) -> Callable[..., object]:
        if self._websocket_connect is not None:
            return self._websocket_connect
        try:
            module = import_module("websockets.sync.client")
        except ModuleNotFoundError:
            raise DeltaFeedError(
                "The websockets package is required for Delta feeds"
            ) from None
        return module.connect

    @staticmethod
    def _decode(
        payload: object, subscribed: dict[str, str]
    ) -> list[PriceUpdate]:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise DeltaFeedError("Delta returned a malformed ticker message") from None
        if not isinstance(payload, str):
            raise DeltaFeedError("Delta returned a non-text ticker message")
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            raise DeltaFeedError("Delta returned a malformed ticker message") from None
        if not isinstance(message, dict):
            return []
        channel = message.get("channel")
        if message.get("type") != "ticker" and not (
            isinstance(channel, str)
            and (channel == "ticker" or channel.endswith("/ticker"))
        ) and not ("sy" in message and "d" in message):
            return []
        symbol = message.get("sy")
        if not isinstance(symbol, str):
            return []
        instrument = next(
            (key for key, value in subscribed.items() if value == symbol), None
        )
        if instrument is None:
            return []
        data = message.get("d")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise DeltaFeedError("Delta returned an invalid ticker message")
        ohlc = data[0].get("ohlc")
        if not isinstance(ohlc, list) or len(ohlc) <= 3:
            raise DeltaFeedError("Delta returned an invalid ticker message")
        price_value = ohlc[3]
        try:
            price = float(price_value)
        except (TypeError, ValueError):
            raise DeltaFeedError("Delta returned an invalid ticker price") from None
        if not math.isfinite(price) or price <= 0:
            raise DeltaFeedError("Delta returned an invalid ticker price")
        timestamp_value = message.get("ts")
        timestamp = datetime.now(timezone.utc)
        if timestamp_value is not None:
            try:
                if isinstance(timestamp_value, (int, float)):
                    seconds = float(timestamp_value) / 1_000_000
                    timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
                elif isinstance(timestamp_value, str):
                    try:
                        seconds = float(timestamp_value)
                    except ValueError:
                        normalized = timestamp_value.replace("Z", "+00:00")
                        timestamp = datetime.fromisoformat(normalized)
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        timestamp = timestamp.astimezone(timezone.utc)
                    else:
                        seconds /= 1_000_000
                        timestamp = datetime.fromtimestamp(
                            seconds, tz=timezone.utc
                        )
            except (OSError, OverflowError, TypeError, ValueError):
                raise DeltaFeedError(
                    "Delta returned an invalid ticker timestamp"
                ) from None
        return [PriceUpdate(instrument=instrument, price=price, timestamp=timestamp)]
