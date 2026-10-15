"""Provider-neutral historical OHLC candle types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class HistoricalDataError(RuntimeError):
    """Raised when a provider cannot return valid historical market data."""


@dataclass(frozen=True)
class HistoricalCandle:
    """One validated OHLC candle with a UTC start timestamp."""

    instrument: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        timestamp = self.timestamp
        if not isinstance(timestamp, datetime):
            raise ValueError("candle timestamp must be a datetime")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        object.__setattr__(self, "timestamp", timestamp.astimezone(timezone.utc))
        values = (self.open, self.high, self.low, self.close)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise ValueError("candle prices must be positive finite numbers")
        try:
            open_price, high, low, close = map(float, values)
        except OverflowError:
            raise ValueError(
                "candle prices must be positive finite numbers"
            ) from None
        if any(
            not math.isfinite(value) or value <= 0
            for value in (open_price, high, low, close)
        ):
            raise ValueError("candle prices must be positive finite numbers")
        object.__setattr__(self, "open", open_price)
        object.__setattr__(self, "high", high)
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "close", close)
        if (
            high < low
            or high < open_price
            or high < close
            or low > open_price
            or low > close
        ):
            raise ValueError("candle OHLC values are inconsistent")


class HistoricalCandleProvider(Protocol):
    """Provider adapter that returns chronologically ordered candles."""

    interval_seconds: int

    def fetch_candles(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[HistoricalCandle]: ...
