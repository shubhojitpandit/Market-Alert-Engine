"""Retrieve public Delta Exchange India historical candles."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.delta_feed import delta_symbol
from data.historical_candle import HistoricalCandle, HistoricalDataError

DELTA_CANDLES_URL = "https://api.india.delta.exchange/v2/history/candles"
_RESPONSE_LIMIT = 2 * 1024 * 1024
_MAX_CANDLES_PER_REQUEST = 2000


class DeltaHistoricalData:
    """Fetch one-minute OHLC candles without authentication."""

    interval_seconds = 60

    def __init__(self, *, candles_url: str = DELTA_CANDLES_URL) -> None:
        self._candles_url = candles_url

    def fetch_candles(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[HistoricalCandle]:
        symbol = delta_symbol(instrument)
        start = self._utc(start)
        end = self._utc(end)
        if end <= start:
            return []
        candles_by_time: dict[datetime, HistoricalCandle] = {}
        page_span = timedelta(minutes=_MAX_CANDLES_PER_REQUEST - 1)
        page_start = start
        while page_start < end:
            page_end = min(page_start + page_span, end)
            for candle in self._fetch_page(instrument, symbol, page_start, page_end):
                if start <= candle.timestamp <= end:
                    candles_by_time[candle.timestamp] = candle
            page_start = page_end
        return sorted(candles_by_time.values(), key=lambda candle: candle.timestamp)

    def _fetch_page(
        self,
        instrument: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalCandle]:
        query = urlencode(
            {
                "symbol": symbol,
                "resolution": "1m",
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
            }
        )
        request = Request(
            f"{self._candles_url}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "MarketAlertEngine/1.0",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read(_RESPONSE_LIMIT + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise HistoricalDataError(
                "Delta historical candle request failed."
            ) from None
        if len(body) > _RESPONSE_LIMIT:
            raise HistoricalDataError(
                "Delta historical candle response exceeded the allowed size."
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HistoricalDataError(
                "Delta returned an invalid historical candle response."
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
        ):
            raise HistoricalDataError(
                "Delta reported a historical candle request failure."
            )
        if not isinstance(payload.get("result"), list):
            raise HistoricalDataError(
                "Delta returned an invalid historical candle response."
            )

        return [
            self._decode_candle(item, instrument)
            for item in payload["result"]
        ]

    @classmethod
    def _decode_candle(cls, value: object, instrument: str) -> HistoricalCandle:
        if not isinstance(value, dict):
            raise HistoricalDataError(
                "Delta returned an invalid historical candle."
            )
        try:
            timestamp_value = value["time"]
            if isinstance(timestamp_value, bool) or not isinstance(
                timestamp_value, (int, float)
            ):
                raise ValueError
            timestamp = datetime.fromtimestamp(
                float(timestamp_value), tz=timezone.utc
            )
            prices = [
                cls._price(value[field])
                for field in ("open", "high", "low", "close")
            ]
            return HistoricalCandle(instrument, timestamp, *prices)
        except (KeyError, OverflowError, OSError, TypeError, ValueError):
            raise HistoricalDataError(
                "Delta returned an invalid historical candle."
            ) from None

    @staticmethod
    def _price(value: object) -> float:
        if isinstance(value, bool):
            raise ValueError
        price = float(value)
        if not math.isfinite(price) or price <= 0:
            raise ValueError
        return price

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
