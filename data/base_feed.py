"""Shared market-data models and feed interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


@dataclass(frozen=True)
class PriceUpdate:
    """A single price observation for an instrument."""

    instrument: str
    price: float
    timestamp: datetime


class PriceFeed(ABC):
    """Interface implemented by sources of standardized price updates."""

    @abstractmethod
    def stream(
        self, instrument: str, count: int | None = None
    ) -> Iterator[PriceUpdate]:
        """Yield updates for one instrument, optionally stopping after count."""
        raise NotImplementedError
