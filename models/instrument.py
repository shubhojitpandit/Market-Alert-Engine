"""Provider-neutral saved/searchable market instrument metadata."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    """Identify one tradable instrument without storing credentials."""

    instrument_key: str
    name: str
    trading_symbol: str
    exchange: str
    segment: str
    instrument_type: str

    @property
    def provider(self) -> str:
        """Return the provider encoded by this instrument's stable key."""
        if self.instrument_key.startswith("delta:"):
            return "delta"
        if self.instrument_key.startswith("forex:"):
            return "forex"
        return "upstox"

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_key", self.instrument_key.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "trading_symbol", self.trading_symbol.strip())
        object.__setattr__(self, "exchange", self.exchange.strip())
        object.__setattr__(self, "segment", self.segment.strip())
        object.__setattr__(
            self, "instrument_type", self.instrument_type.strip()
        )
        if not self.instrument_key:
            raise ValueError("instrument_key must not be empty")
