"""Throttle and persist per-provider market-data timestamps."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class MarketDataCursorError(RuntimeError):
    """Raised when the market-data cursor cannot be persisted."""


class MarketDataCursorStore:
    """Keep the latest confirmed timestamp per provider and instrument."""

    _FLUSH_INTERVAL_SECONDS = 5.0

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.default_path()
        self._timestamps: dict[tuple[str, str], datetime] = {}
        self._pending: dict[tuple[str, str], datetime] = {}
        self._load_error: str | None = None
        self._last_flush = time.monotonic()
        self._lock = threading.RLock()
        self._load()

    @staticmethod
    def default_path() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else (
            Path.home() / "AppData" / "Local"
        )
        return root / "MarketAlertEngine" / "market_data_cursors.json"

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def snapshot(self) -> dict[tuple[str, str], datetime]:
        with self._lock:
            return dict(self._timestamps)

    def record(
        self, provider: str, instrument: str, timestamp: datetime
    ) -> None:
        if not provider.strip() or not instrument.strip():
            raise ValueError("provider and instrument must not be empty")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        key = (provider, instrument)
        with self._lock:
            if timestamp <= self._timestamps.get(
                key, datetime.min.replace(tzinfo=timezone.utc)
            ):
                return
            self._timestamps[key] = timestamp
            self._pending[key] = timestamp
            if (
                time.monotonic() - self._last_flush
                >= self._FLUSH_INTERVAL_SECONDS
            ):
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _load(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            cursors = payload["cursors"]
            if not isinstance(cursors, dict):
                raise ValueError
            loaded: dict[tuple[str, str], datetime] = {}
            for provider, instruments in cursors.items():
                if not isinstance(provider, str) or not isinstance(
                    instruments, dict
                ):
                    raise ValueError
                for instrument, timestamp_value in instruments.items():
                    if not isinstance(instrument, str) or not isinstance(
                        timestamp_value, str
                    ):
                        raise ValueError
                    timestamp = datetime.fromisoformat(timestamp_value)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    loaded[(provider, instrument)] = timestamp.astimezone(
                        timezone.utc
                    )
        except FileNotFoundError:
            return
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            self._load_error = (
                "Saved market-data cursors are unreadable; the file was preserved."
            )
            return
        self._timestamps = loaded

    def _flush_locked(self) -> None:
        self._last_flush = time.monotonic()
        if not self._pending or self._load_error is not None:
            return
        payload: dict[str, dict[str, str]] = {}
        for (provider, instrument), timestamp in self._timestamps.items():
            payload.setdefault(provider, {})[instrument] = timestamp.isoformat()

        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                delete=False,
            ) as temporary_file:
                json.dump({"cursors": payload}, temporary_file, indent=2)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(self._path)
        except OSError:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise MarketDataCursorError(
                "Could not save market-data cursors."
            ) from None
        self._pending.clear()
