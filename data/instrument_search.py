"""Aggregate provider instrument resolvers into one global search."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from models.instrument import Instrument


class InstrumentResolver(Protocol):
    """Search one provider for instruments."""

    def search(self, query: str) -> list[Instrument]: ...


@dataclass(frozen=True)
class InstrumentSearchSource:
    """One resolver and its user-facing market category."""

    market: str
    resolver: InstrumentResolver


class InstrumentSearchAggregator:
    """Merge resolver results while keeping provider failures independent."""

    def search(
        self,
        query: str,
        sources: Sequence[InstrumentSearchSource],
    ) -> tuple[list[Instrument], list[str]]:
        results: list[Instrument] = []
        failures: list[str] = []
        seen_keys: set[str] = set()
        for source in sources:
            try:
                instruments = source.resolver.search(query)
            except Exception:
                failures.append(f"{source.market} search failed.")
                continue
            for instrument in instruments:
                if instrument.instrument_key in seen_keys:
                    continue
                seen_keys.add(instrument.instrument_key)
                results.append(instrument)
        return results, failures
