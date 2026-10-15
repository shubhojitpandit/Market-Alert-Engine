"""Persistent history of completed alert trigger events."""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models.alert import Alert, AlertCondition, AlertStatus


@dataclass(frozen=True)
class AlertHistoryRecord:
    """Capture an alert and the market update that triggered it."""

    alert_id: str
    symbol: str
    condition: AlertCondition
    target_price: float
    current_price: float
    triggered_at: datetime
    recovered: bool = False


class AlertHistory:
    """Record unique triggered-alert events in trigger order and local storage."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._records: list[AlertHistoryRecord] = []
        self._recorded_alert_ids: set[str] = set()
        if self._path is not None:
            self._load()

    @staticmethod
    def default_path() -> Path:
        """Return the per-user append-only alert-history path."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data)
        else:
            root = Path.home() / "AppData" / "Local"
        return root / "MarketAlertEngine" / "alert_history.jsonl"

    def record_trigger(
        self,
        alert: Alert,
        current_price: float,
        triggered_at: datetime,
        *,
        recovered: bool = False,
    ) -> bool:
        """Record a triggered alert once; return whether it was newly added."""
        if alert.status is not AlertStatus.TRIGGERED:
            raise ValueError("only triggered alerts can be recorded")
        if alert.alert_id in self._recorded_alert_ids:
            return False

        if triggered_at.tzinfo is None:
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        else:
            triggered_at = triggered_at.astimezone(timezone.utc)

        record = AlertHistoryRecord(
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            condition=alert.condition,
            target_price=alert.target_price,
            current_price=current_price,
            triggered_at=triggered_at,
            recovered=recovered,
        )
        self._append(record)
        self._records.append(record)
        self._recorded_alert_ids.add(alert.alert_id)
        return True

    def get_all(self) -> list[AlertHistoryRecord]:
        """Return all trigger records in chronological insertion order."""
        return list(self._records)

    def get_recent(self, limit: int = 10) -> list[AlertHistoryRecord]:
        """Return up to limit most recent records, oldest to newest."""
        if limit < 0:
            raise ValueError("limit must not be negative")
        if limit == 0:
            return []
        return self._records[-limit:]

    def _load(self) -> None:
        try:
            with self._path.open("r", encoding="utf-8") as history_file:
                for line in history_file:
                    if not line.strip():
                        continue
                    record = self._decode_record(json.loads(line))
                    if record.alert_id not in self._recorded_alert_ids:
                        self._records.append(record)
                        self._recorded_alert_ids.add(record.alert_id)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Could not read saved alert history; the history file was preserved."
            ) from None

    @staticmethod
    def _decode_record(value: object) -> AlertHistoryRecord:
        if not isinstance(value, dict):
            raise ValueError("invalid alert history record")
        triggered_at = datetime.fromisoformat(value["triggered_at"])
        if triggered_at.tzinfo is None:
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        else:
            triggered_at = triggered_at.astimezone(timezone.utc)
        return AlertHistoryRecord(
            alert_id=str(value["alert_id"]),
            symbol=str(value["symbol"]),
            condition=AlertCondition(value["condition"]),
            target_price=float(value["target_price"]),
            current_price=float(value["current_price"]),
            triggered_at=triggered_at,
            recovered=value.get("recovered", False) is True,
        )

    def _append(self, record: AlertHistoryRecord) -> None:
        if self._path is None:
            return
        payload = json.dumps(
            {
                "alert_id": record.alert_id,
                "symbol": record.symbol,
                "condition": record.condition.value,
                "target_price": record.target_price,
                "current_price": record.current_price,
                "triggered_at": record.triggered_at.isoformat(),
                "recovered": record.recovered,
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        descriptor: int | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self._path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("history append did not complete")
                remaining = remaining[written:]
            os.fsync(descriptor)
        except OSError:
            raise RuntimeError("Could not append alert history.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
