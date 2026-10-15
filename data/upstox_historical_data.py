"""Retrieve authorized Upstox Historical Candle Data V3."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from data.credential_store import CredentialStore, CredentialStoreError
from data.historical_candle import HistoricalCandle, HistoricalDataError
from data.upstox_feed import UPSTOX_USER_AGENT
from data.upstox_instrument_resolver import _winhttp_search_get

UPSTOX_HISTORICAL_CANDLE_URL = (
    "https://api.upstox.com/v3/historical-candle"
)
_RESPONSE_LIMIT = 2 * 1024 * 1024
_MAX_DAYS_PER_REQUEST = 27


class UpstoxHistoricalData:
    """Fetch one-minute candles using a connection's protected credential."""

    interval_seconds = 60

    def __init__(
        self,
        credential_store: CredentialStore,
        credential_id: str,
        *,
        candles_url: str = UPSTOX_HISTORICAL_CANDLE_URL,
    ) -> None:
        self._credential_store = credential_store
        self._credential_id = credential_id
        self._candles_url = candles_url.rstrip("/")

    def fetch_candles(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[HistoricalCandle]:
        start = self._utc(start)
        end = self._utc(end)
        if end <= start:
            return []
        credential = self._get_credential()
        encoded_instrument = quote(instrument, safe="")
        try:
            candles_by_time: dict[datetime, HistoricalCandle] = {}
            page_span = timedelta(days=_MAX_DAYS_PER_REQUEST)
            page_start = start
            while page_start < end:
                page_end = min(page_start + page_span, end)
                for candle in self._fetch_page(
                    encoded_instrument,
                    instrument,
                    credential,
                    page_start,
                    page_end,
                ):
                    if start <= candle.timestamp <= end:
                        candles_by_time[candle.timestamp] = candle
                page_start = page_end
        finally:
            credential = ""
        return sorted(candles_by_time.values(), key=lambda candle: candle.timestamp)

    def _fetch_page(
        self,
        encoded_instrument: str,
        instrument: str,
        credential: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalCandle]:
        url = (
            f"{self._candles_url}/{encoded_instrument}/minutes/1/"
            f"{end.date().isoformat()}/{start.date().isoformat()}"
        )
        try:
            status_code, body = _winhttp_search_get(
                url,
                credential,
                timeout=15,
                user_agent=UPSTOX_USER_AGENT,
                body_limit=_RESPONSE_LIMIT,
            )
        except Exception:
            raise HistoricalDataError(
                "Upstox historical candle request failed."
            ) from None
        if status_code != 200:
            raise HistoricalDataError(
                f"Upstox historical candle request failed (HTTP {status_code})."
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HistoricalDataError(
                "Upstox returned an invalid historical candle response."
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(payload.get("data"), dict)
            or not isinstance(payload["data"].get("candles"), list)
        ):
            raise HistoricalDataError(
                "Upstox returned an invalid historical candle response."
            )

        return [
            self._decode_candle(item, instrument)
            for item in payload["data"]["candles"]
        ]

    def _get_credential(self) -> str:
        try:
            return self._credential_store.retrieve(
                "upstox", self._credential_id
            )
        except (CredentialStoreError, KeyError, ValueError):
            raise HistoricalDataError(
                "Upstox historical recovery requires a saved credential."
            ) from None
        except Exception:
            raise HistoricalDataError(
                "Upstox historical candle credential could not be retrieved."
            ) from None

    @classmethod
    def _decode_candle(cls, value: object, instrument: str) -> HistoricalCandle:
        if not isinstance(value, list) or len(value) < 5:
            raise HistoricalDataError(
                "Upstox returned an invalid historical candle."
            )
        try:
            timestamp_value = value[0]
            if not isinstance(timestamp_value, str):
                raise ValueError
            timestamp = datetime.fromisoformat(
                timestamp_value.replace("Z", "+00:00")
            )
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            prices = [
                cls._price(item)
                for item in value[1:5]
            ]
            return HistoricalCandle(
                instrument, timestamp.astimezone(timezone.utc), *prices
            )
        except (OverflowError, TypeError, ValueError):
            raise HistoricalDataError(
                "Upstox returned an invalid historical candle."
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
