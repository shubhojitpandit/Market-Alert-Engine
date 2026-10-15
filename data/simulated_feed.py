"""Configuration-driven random-walk price simulation."""

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Mapping

from data.base_feed import PriceFeed, PriceUpdate


@dataclass(frozen=True)
class InstrumentConfig:
    """Settings used by the generic simulator for one instrument."""

    symbol: str
    asset_class: str
    starting_price: float
    precision: int
    volatility: float


DEFAULT_INSTRUMENTS: dict[str, InstrumentConfig] = {
    "EURUSD": InstrumentConfig("EURUSD", "Forex", 1.0850, 5, 0.0005),
    "GBPUSD": InstrumentConfig("GBPUSD", "Forex", 1.2700, 5, 0.0005),
    "USDJPY": InstrumentConfig("USDJPY", "Forex", 149.50, 3, 0.0005),
    "XAUUSD": InstrumentConfig("XAUUSD", "Commodities", 2150.00, 2, 0.001),
    "XAGUSD": InstrumentConfig("XAGUSD", "Commodities", 25.00, 2, 0.002),
    "USOIL": InstrumentConfig("USOIL", "Commodities", 78.00, 2, 0.002),
    "BTCUSD": InstrumentConfig("BTCUSD", "Crypto", 65000.00, 2, 0.001),
    "ETHUSD": InstrumentConfig("ETHUSD", "Crypto", 3500.00, 2, 0.001),
    "AAPL": InstrumentConfig("AAPL", "US equities", 190.00, 2, 0.001),
    "NVDA": InstrumentConfig("NVDA", "US equities", 900.00, 2, 0.0015),
    "TSLA": InstrumentConfig("TSLA", "US equities", 175.00, 2, 0.0015),
    "US30": InstrumentConfig("US30", "Indices", 39000.0, 1, 0.0005),
    "NAS100": InstrumentConfig("NAS100", "Indices", 18000.0, 1, 0.0005),
    "RELIANCE": InstrumentConfig("RELIANCE", "Indian instruments", 2900.00, 2, 0.001),
    "TCS": InstrumentConfig("TCS", "Indian instruments", 3900.00, 2, 0.001),
    "NIFTY50": InstrumentConfig("NIFTY50", "Indian instruments", 22500.0, 2, 0.0005),
}


class SimulatedPriceFeed(PriceFeed):
    """Generate simulated prices using a seeded, configurable random walk."""

    def __init__(
        self,
        update_interval: float = 1.0,
        seed: int | None = None,
        instruments: Mapping[str, InstrumentConfig] | None = None,
    ) -> None:
        if update_interval < 0:
            raise ValueError("update_interval must not be negative")

        self.update_interval = update_interval
        self._random = random.Random(seed)
        self._instruments = dict(instruments or DEFAULT_INSTRUMENTS)
        self._prices: dict[str, float] = {}

        for key, config in self._instruments.items():
            if key != config.symbol:
                raise ValueError(f"Instrument key {key!r} must match its symbol")
            if config.starting_price <= 0:
                raise ValueError(f"{key} starting_price must be positive")
            if config.precision < 0:
                raise ValueError(f"{key} precision must not be negative")
            if config.volatility < 0:
                raise ValueError(f"{key} volatility must not be negative")
            self._prices[key] = round(config.starting_price, config.precision)

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        """Yield successive updates for one configured instrument."""
        if instrument not in self._instruments:
            raise ValueError(f"Unknown instrument: {instrument}")
        if count is not None and count < 0:
            raise ValueError("count must not be negative")

        config = self._instruments[instrument]
        index = 0
        while count is None or index < count:
            if index > 0:
                time.sleep(self.update_interval)

            previous_price = self._prices[instrument]
            movement = self._random.gauss(0, config.volatility)
            next_price = max(
                10 ** -config.precision, previous_price * (1 + movement)
            )
            next_price = round(next_price, config.precision)
            self._prices[instrument] = next_price

            yield PriceUpdate(
                instrument=instrument,
                price=next_price,
                timestamp=datetime.now(timezone.utc),
            )
            index += 1
