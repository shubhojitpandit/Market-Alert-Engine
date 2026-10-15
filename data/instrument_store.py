"""Persist provider-neutral instrument metadata in local application data."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from models.instrument import Instrument

_CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "credential",
        "token",
        "access_token",
        "authorization",
        "bearer",
        "password",
        "secret",
    }
)


class InstrumentStoreError(RuntimeError):
    """Raised when saved instrument metadata cannot be managed."""


class InstrumentStore:
    """Add, list, and remove locally saved instruments without credentials."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.default_path()
        self._lock = threading.RLock()
        self._instruments = self._load()

    @staticmethod
    def default_path() -> Path:
        """Return a per-user local path containing no provider credentials."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data)
        else:
            root = Path.home() / "AppData" / "Local"
        return root / "MarketAlertEngine" / "instruments.json"

    def list_instruments(self) -> list[Instrument]:
        """Return saved instruments in insertion order."""
        with self._lock:
            return list(self._instruments.values())

    def contains(self, instrument_key: str) -> bool:
        """Return whether an instrument_key is already saved."""
        key = instrument_key.strip()
        with self._lock:
            return key in self._instruments

    def get(self, instrument_key: str) -> Instrument:
        """Return one saved instrument by instrument_key."""
        key = instrument_key.strip()
        if not key:
            raise ValueError("instrument_key must not be empty")
        with self._lock:
            try:
                return self._instruments[key]
            except KeyError:
                raise InstrumentStoreError("Instrument was not found") from None

    def add(self, instrument: Instrument) -> Instrument:
        """Save an instrument. Duplicate keys keep the existing entry."""
        with self._lock:
            existing = self._instruments.get(instrument.instrument_key)
            if existing is not None:
                return existing
            self._instruments[instrument.instrument_key] = instrument
            self._save()
            return instrument

    def remove(self, instrument_key: str) -> None:
        """Remove one saved instrument by instrument_key."""
        key = instrument_key.strip()
        if not key:
            raise ValueError("instrument_key must not be empty")
        with self._lock:
            if key not in self._instruments:
                raise InstrumentStoreError("Instrument was not found")
            del self._instruments[key]
            self._save()

    def _load(self) -> dict[str, Instrument]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            raise InstrumentStoreError(
                "Could not read saved instrument metadata"
            ) from None
        if not isinstance(raw, dict) or not isinstance(
            raw.get("instruments"), list
        ):
            raise InstrumentStoreError("Saved instrument metadata is invalid")
        if any(
            key.casefold() in _CREDENTIAL_FIELD_NAMES for key in raw
        ):
            raise InstrumentStoreError("Saved instrument metadata is invalid")

        instruments: dict[str, Instrument] = {}
        try:
            for item in raw["instruments"]:
                if not isinstance(item, dict):
                    raise ValueError
                if any(
                    key.casefold() in _CREDENTIAL_FIELD_NAMES for key in item
                ):
                    raise ValueError
                instrument = Instrument(
                    instrument_key=str(item["instrument_key"]),
                    name=str(item.get("name", "")),
                    trading_symbol=str(item.get("trading_symbol", "")),
                    exchange=str(item.get("exchange", "")),
                    segment=str(item.get("segment", "")),
                    instrument_type=str(item.get("instrument_type", "")),
                )
                if instrument.instrument_key in instruments:
                    raise ValueError
                instruments[instrument.instrument_key] = instrument
        except (TypeError, ValueError, KeyError):
            raise InstrumentStoreError(
                "Saved instrument metadata is invalid"
            ) from None
        return instruments

    def _save(self) -> None:
        payload_object = {
            "instruments": [
                asdict(instrument) for instrument in self._instruments.values()
            ]
        }
        payload = json.dumps(payload_object, indent=2)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                delete=False,
            ) as temporary_file:
                temporary_file.write(payload)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(self._path)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise InstrumentStoreError(
                "Could not write saved instrument metadata"
            ) from None
