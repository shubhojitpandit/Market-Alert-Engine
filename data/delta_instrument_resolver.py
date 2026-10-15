"""Resolve searchable Delta Exchange India products from its public API."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from models.instrument import Instrument

DELTA_PRODUCTS_URL = "https://api.india.delta.exchange/v2/products"
_RESPONSE_LIMIT = 8 * 1024 * 1024
_INSTRUMENT_PREFIX = "delta:"


class DeltaInstrumentSearchError(RuntimeError):
    """Raised when Delta product metadata cannot be loaded."""


class DeltaInstrumentResolver:
    """Search the live Delta product catalog and return provider-neutral models."""

    def __init__(self, *, products_url: str = DELTA_PRODUCTS_URL) -> None:
        self._products_url = products_url

    def search(self, query: str) -> list[Instrument]:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("Instrument search query must not be empty.")
        request = Request(
            self._products_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "MarketAlertEngine/1.0",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read(_RESPONSE_LIMIT + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise DeltaInstrumentSearchError(
                "Could not load Delta Exchange products."
            ) from None
        if len(body) > _RESPONSE_LIMIT:
            raise DeltaInstrumentSearchError(
                "Delta product response exceeded the allowed size."
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DeltaInstrumentSearchError(
                "Delta returned an invalid product response."
            ) from None
        if not isinstance(payload, dict) or not isinstance(
            payload.get("result"), list
        ):
            raise DeltaInstrumentSearchError(
                "Delta returned an invalid product response."
            )

        results: list[Instrument] = []
        for product in payload["result"]:
            if not isinstance(product, dict):
                continue
            symbol = product.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            symbol = symbol.strip()
            state = product.get("state")
            if isinstance(state, str) and state.casefold() not in {
                "live",
                "active",
            }:
                continue
            name = str(product.get("description") or symbol).strip()
            searchable = f"{symbol} {name}".casefold()
            if normalized not in searchable:
                continue
            segment = str(
                product.get("contract_type")
                or product.get("product_type")
                or "Perpetual"
            ).strip()
            results.append(
                Instrument(
                    instrument_key=f"{_INSTRUMENT_PREFIX}{symbol}",
                    name=name,
                    trading_symbol=symbol,
                    exchange="Delta Exchange India",
                    segment=segment,
                    instrument_type=segment,
                )
            )
        return results
