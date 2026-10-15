"""Offline tests for the MetaTrader 5 feed adapter."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from data.base_feed import PriceFeed, PriceUpdate
from data.mt5_feed import MT5FeedError, MT5PriceFeed
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from models.alert import AlertCondition


_DEFAULT_TICK = object()


class FakeMT5:
    def __init__(
        self,
        *,
        initialized: bool = True,
        initialize_error: Exception | None = None,
        selected: bool = True,
        tick: object = _DEFAULT_TICK,
        tick_error: Exception | None = None,
    ) -> None:
        self.initialized = initialized
        self.initialize_error = initialize_error
        self.selected = selected
        self.tick = (
            SimpleNamespace(
                bid=2000.0,
                ask=2002.0,
                time=1_782_000_000,
                time_msc=1_782_000_000_123,
            )
            if tick is _DEFAULT_TICK
            else tick
        )
        self.tick_error = tick_error
        self.shutdown_calls = 0
        self.selected_symbols: list[tuple[str, bool]] = []
        self.requested_symbols: list[str] = []

    def initialize(self) -> bool:
        if self.initialize_error is not None:
            raise self.initialize_error
        return self.initialized

    def last_error(self) -> tuple[int, str]:
        return (1, "fake MT5 error")

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.selected_symbols.append((symbol, enable))
        return self.selected

    def symbol_info_tick(self, symbol: str) -> object | None:
        self.requested_symbols.append(symbol)
        if self.tick_error is not None:
            raise self.tick_error
        return self.tick


def test_mt5_feed_implements_price_feed_and_normalizes_xauusdm_tick() -> None:
    mt5 = FakeMT5()
    feed = MT5PriceFeed(update_interval=0, mt5_api=mt5)

    assert isinstance(feed, PriceFeed)
    updates = list(feed.stream("XAUUSDm", count=1))

    assert updates == [
        PriceUpdate(
            instrument="XAUUSDm",
            price=2001.0,
            timestamp=datetime.fromtimestamp(
                1_782_000_000.123, tz=timezone.utc
            ),
        )
    ]
    assert mt5.selected_symbols == [("XAUUSDm", True)]
    assert mt5.requested_symbols == ["XAUUSDm"]
    assert mt5.shutdown_calls == 1

    manager = AlertManager()
    alert = manager.create_alert("XAUUSDm", 2000.5, AlertCondition.ABOVE)
    assert AlertEngine(manager).evaluate(updates[0]) == [alert]


def test_mt5_feed_reports_initialization_failure_and_cleans_up() -> None:
    mt5 = FakeMT5(initialized=False)

    with pytest.raises(MT5FeedError, match="initialization failed"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1


def test_mt5_feed_wraps_initialization_runtime_error_and_cleans_up() -> None:
    mt5 = FakeMT5(initialize_error=OSError("terminal unavailable"))

    with pytest.raises(MT5FeedError, match="terminal unavailable"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1


def test_mt5_feed_reports_symbol_selection_failure_and_shuts_down() -> None:
    mt5 = FakeMT5(selected=False)

    with pytest.raises(MT5FeedError, match="could not select XAUUSDm"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1
    assert mt5.requested_symbols == []


def test_mt5_feed_reports_missing_tick_and_shuts_down() -> None:
    mt5 = FakeMT5(tick=None)

    with pytest.raises(MT5FeedError, match="returned no tick for XAUUSDm"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1


def test_mt5_feed_wraps_runtime_errors_and_shuts_down() -> None:
    mt5 = FakeMT5(tick_error=OSError("terminal disconnected"))

    with pytest.raises(MT5FeedError, match="terminal disconnected"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1


def test_mt5_feed_rejects_invalid_quotes_and_shuts_down() -> None:
    mt5 = FakeMT5(
        tick=SimpleNamespace(
            bid=0,
            ask=2002.0,
            time=1_782_000_000,
            time_msc=1_782_000_000_123,
        )
    )

    with pytest.raises(MT5FeedError, match="invalid quote"):
        next(MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm"))

    assert mt5.shutdown_calls == 1


def test_mt5_feed_shuts_down_when_stream_is_closed_early() -> None:
    mt5 = FakeMT5()
    updates = MT5PriceFeed(mt5_api=mt5).stream("XAUUSDm")

    next(updates)
    updates.close()

    assert mt5.shutdown_calls == 1


def test_mt5_feed_rejects_negative_count_and_interval() -> None:
    with pytest.raises(ValueError, match="count"):
        next(MT5PriceFeed(mt5_api=FakeMT5()).stream("XAUUSDm", count=-1))

    with pytest.raises(ValueError, match="update_interval"):
        MT5PriceFeed(update_interval=-1, mt5_api=FakeMT5())
