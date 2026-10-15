"""Thread-safe queue for GUI-facing events."""

from collections.abc import Iterator
from dataclasses import dataclass
from queue import Empty, Queue
import threading

from data.base_feed import PriceUpdate
from models.instrument import Instrument


@dataclass(frozen=True)
class PriceQueued:
    """A live price update posted from the feed thread."""

    update: PriceUpdate


@dataclass(frozen=True)
class SearchResultQueued:
    """Completed instrument search results for one request id."""

    request_id: int
    results: tuple[Instrument, ...]
    warning: str | None = None


@dataclass(frozen=True)
class SearchErrorQueued:
    """Safe, user-facing instrument search failure for one request id."""

    request_id: int
    message: str


@dataclass(frozen=True)
class ConnectionActionQueued:
    """Sanitized result of a create/connect/disconnect/delete worker."""

    request_id: int
    action: str
    connection_id: str | None
    message: str


@dataclass(frozen=True)
class AlertActionQueued:
    """Sanitized result of an alert create/cancel worker."""

    request_id: int
    action: str
    message: str


UiMessage = (
    PriceQueued
    | SearchResultQueued
    | SearchErrorQueued
    | ConnectionActionQueued
    | AlertActionQueued
)


class UiQueue:
    """Hold GUI events posted from worker/feed threads for later UI consumption."""

    def __init__(self) -> None:
        self._queue: Queue[UiMessage] = Queue()
        self._lock = threading.Lock()
        self._latest_prices: dict[str, PriceUpdate] = {}
        self._accepting = True

    def close(self) -> None:
        """Stop accepting new GUI messages and drop coalesced prices."""
        with self._lock:
            self._accepting = False
            self._latest_prices.clear()

    def put_price_update(self, update: PriceUpdate) -> None:
        """Keep the latest PriceUpdate per instrument. Safe from any thread."""
        with self._lock:
            if not self._accepting:
                return
            self._latest_prices[update.instrument] = update

    def pending_price_count(self) -> int:
        """Return how many coalesced instrument prices are waiting."""
        with self._lock:
            return len(self._latest_prices)

    def put_search_result(
        self,
        request_id: int,
        results: list[Instrument] | tuple[Instrument, ...],
        warning: str | None = None,
    ) -> None:
        """Enqueue completed search results. Safe to call from any thread."""
        self._put_event(SearchResultQueued(request_id, tuple(results), warning))

    def put_search_error(self, request_id: int, message: str) -> None:
        """Enqueue a sanitized search error. Safe to call from any thread."""
        self._put_event(SearchErrorQueued(request_id, message))

    def put_connection_action(
        self,
        request_id: int,
        action: str,
        connection_id: str | None,
        message: str,
    ) -> None:
        """Enqueue a sanitized connection-lifecycle result."""
        self._put_event(
            ConnectionActionQueued(request_id, action, connection_id, message)
        )

    def put_alert_action(self, request_id: int, action: str, message: str) -> None:
        """Enqueue a sanitized alert-lifecycle result."""
        self._put_event(AlertActionQueued(request_id, action, message))

    def _put_event(self, message: UiMessage) -> None:
        with self._lock:
            if not self._accepting:
                return
        self._queue.put(message)

    def get_nowait(self) -> UiMessage | None:
        """Return the next queued non-price message, or None if empty."""
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def drain(self) -> list[UiMessage]:
        """Return control events plus the latest price per instrument."""
        messages: list[UiMessage] = []
        while True:
            message = self.get_nowait()
            if message is None:
                break
            messages.append(message)
        with self._lock:
            prices = list(self._latest_prices.values())
            self._latest_prices.clear()
        messages.extend(PriceQueued(update) for update in prices)
        return messages

    def __iter__(self) -> Iterator[UiMessage]:
        return iter(self.drain())
