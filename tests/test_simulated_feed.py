"""Deterministic component checks for the simulated price feed."""

from datetime import timezone

import pytest

from data.simulated_feed import (
    DEFAULT_INSTRUMENTS,
    InstrumentConfig,
    SimulatedPriceFeed,
)


def test_configured_instruments_are_available() -> None:
    feed = SimulatedPriceFeed(update_interval=0, seed=1)

    for symbol in ("EURUSD", "BTCUSD", "AAPL"):
        update = next(feed.stream(symbol, count=1))
        assert update.instrument == symbol


def test_price_update_contains_positive_price_and_utc_timestamp() -> None:
    update = next(
        SimulatedPriceFeed(update_interval=0, seed=2).stream(
            "EURUSD", count=1
        )
    )

    assert update.instrument == "EURUSD"
    assert update.price > 0
    assert update.timestamp.tzinfo is timezone.utc


@pytest.mark.parametrize("symbol", DEFAULT_INSTRUMENTS)
def test_generated_prices_respect_configured_precision(symbol: str) -> None:
    config = DEFAULT_INSTRUMENTS[symbol]
    updates = SimulatedPriceFeed(update_interval=0, seed=3).stream(
        symbol, count=5
    )

    assert all(
        round(update.price, config.precision) == update.price
        for update in updates
    )


def test_same_seed_reproduces_updates_for_multiple_instruments() -> None:
    def sample() -> list[tuple[str, float]]:
        feed = SimulatedPriceFeed(update_interval=0, seed=42)
        return [
            (symbol, update.price)
            for symbol in ("EURUSD", "BTCUSD", "AAPL")
            for update in feed.stream(symbol, count=4)
        ]

    assert sample() == sample()


def test_instrument_prices_are_kept_separately() -> None:
    instruments = {
        "FIRST": InstrumentConfig("FIRST", "Test", 100.0, 2, 0),
        "SECOND": InstrumentConfig("SECOND", "Test", 10.0, 2, 0),
    }
    feed = SimulatedPriceFeed(
        update_interval=0, seed=5, instruments=instruments
    )

    assert next(feed.stream("FIRST", count=1)).price == 100.0
    assert next(feed.stream("SECOND", count=1)).price == 10.0
    assert next(feed.stream("FIRST", count=1)).price == 100.0


def test_configured_interval_and_custom_configuration_are_used() -> None:
    instruments = {
        "TEST": InstrumentConfig("TEST", "Test", 12.345, 2, 0),
    }
    feed = SimulatedPriceFeed(
        update_interval=0.125, seed=6, instruments=instruments
    )

    assert feed.update_interval == 0.125
    assert list(feed.stream("TEST", count=2))[0].price == 12.35


def test_unknown_instrument_and_invalid_count_are_rejected() -> None:
    feed = SimulatedPriceFeed(update_interval=0, seed=7)

    with pytest.raises(ValueError, match="Unknown instrument"):
        next(feed.stream("UNKNOWN", count=1))
    with pytest.raises(ValueError, match="count"):
        next(feed.stream("EURUSD", count=-1))
    assert list(feed.stream("EURUSD", count=0)) == []
