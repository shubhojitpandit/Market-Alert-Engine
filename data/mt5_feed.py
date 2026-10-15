"""MetaTrader 5 implementation of the shared market-data feed interface."""

import math
import time
from datetime import datetime, timezone
from importlib import import_module
from typing import Iterator, Protocol, cast

from data.base_feed import PriceFeed, PriceUpdate

DEFAULT_MT5_SYMBOL = "XAUUSDm"


class MT5FeedError(RuntimeError):
    """Raised when MetaTrader 5 cannot provide a valid price update."""


class _MT5Tick(Protocol):
    bid: float
    ask: float
    time: int
    time_msc: int


class _MT5Api(Protocol):
    def initialize(self) -> bool: ...

    def last_error(self) -> object: ...

    def shutdown(self) -> None: ...

    def symbol_select(self, symbol: str, enable: bool) -> bool: ...

    def symbol_info_tick(self, symbol: str) -> _MT5Tick | None: ...


class MT5PriceFeed(PriceFeed):
    """Poll an MT5 terminal and yield normalized price updates."""

    def __init__(
        self, update_interval: float = 1.0, mt5_api: _MT5Api | None = None
    ) -> None:
        if update_interval < 0:
            raise ValueError("update_interval must not be negative")

        self.update_interval = update_interval
        self._mt5_api = mt5_api

    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        """Yield MT5 ticks for a broker symbol, closing the terminal on exit."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        if count is not None and count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return

        mt5 = self._get_mt5_api()
        initialization_attempted = False
        try:
            initialization_attempted = True
            try:
                initialized = mt5.initialize()
            except Exception as error:
                raise MT5FeedError(
                    f"MT5 initialization failed: {error}"
                ) from error
            if not initialized:
                raise MT5FeedError(
                    f"MT5 initialization failed: {mt5.last_error()}"
                )

            try:
                selected = mt5.symbol_select(instrument, True)
            except Exception as error:
                raise MT5FeedError(
                    f"MT5 could not select {instrument}: {error}"
                ) from error
            if not selected:
                raise MT5FeedError(
                    f"MT5 could not select {instrument}: {mt5.last_error()}"
                )

            index = 0
            while count is None or index < count:
                if index > 0:
                    time.sleep(self.update_interval)
                try:
                    tick = mt5.symbol_info_tick(instrument)
                except Exception as error:
                    raise MT5FeedError(
                        f"MT5 could not retrieve a tick for {instrument}: "
                        f"{error}"
                    ) from error
                if tick is None:
                    raise MT5FeedError(
                        f"MT5 returned no tick for {instrument}: "
                        f"{mt5.last_error()}"
                    )

                yield self._to_price_update(instrument, tick)
                index += 1
        finally:
            if initialization_attempted:
                mt5.shutdown()

    def _get_mt5_api(self) -> _MT5Api:
        if self._mt5_api is not None:
            return self._mt5_api

        try:
            mt5 = import_module("MetaTrader5")
        except ModuleNotFoundError as error:
            if error.name != "MetaTrader5":
                raise
            raise MT5FeedError(
                "The MetaTrader5 package is not installed. "
                "Install it with: python -m pip install MetaTrader5"
            ) from error
        return cast(_MT5Api, mt5)

    @staticmethod
    def _to_price_update(instrument: str, tick: _MT5Tick) -> PriceUpdate:
        try:
            bid = float(tick.bid)
            ask = float(tick.ask)
            milliseconds = getattr(tick, "time_msc", None)
            timestamp_value = (
                float(milliseconds) / 1000
                if milliseconds is not None and float(milliseconds) > 0
                else float(tick.time)
            )
            timestamp = datetime.fromtimestamp(
                timestamp_value, tz=timezone.utc
            )
        except (AttributeError, OSError, OverflowError, TypeError, ValueError) as error:
            raise MT5FeedError(
                f"MT5 returned an invalid tick for {instrument}: {error}"
            ) from error

        price = (bid + ask) / 2
        if (
            not math.isfinite(bid)
            or not math.isfinite(ask)
            or bid <= 0
            or ask <= 0
            or not math.isfinite(timestamp_value)
            or not math.isfinite(price)
        ):
            raise MT5FeedError(
                f"MT5 returned an invalid quote for {instrument}"
            )

        return PriceUpdate(
            instrument=instrument,
            price=price,
            timestamp=timestamp,
        )
