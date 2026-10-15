"""Persist the current set of active alerts in local application data."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from models.alert import Alert, AlertCondition, AlertStatus


class ActiveAlertStoreError(RuntimeError):
    """Raised when active alerts cannot safely be loaded or persisted."""


class ActiveAlertStore:
    """Read and atomically replace a local snapshot of active alerts."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.default_path()
        self._load_error: str | None = None
        self._next_id = 1

    @staticmethod
    def default_path() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data)
        else:
            root = Path.home() / "AppData" / "Local"
        return root / "MarketAlertEngine" / "active_alerts.json"

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def next_id(self) -> int:
        return self._next_id

    def load(self) -> list[Alert]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            self._load_error = (
                "Saved active alerts are unreadable; the file was preserved."
            )
            return []
        if not isinstance(payload, dict) or not isinstance(
            payload.get("alerts"), list
        ):
            self._load_error = (
                "Saved active alerts are invalid; the file was preserved."
            )
            return []

        alerts: list[Alert] = []
        seen: set[str] = set()
        try:
            stored_next_id = payload.get("next_id", 1)
            if (
                isinstance(stored_next_id, bool)
                or not isinstance(stored_next_id, int)
                or stored_next_id < 1
            ):
                raise ValueError
            for item in payload["alerts"]:
                if not isinstance(item, dict):
                    raise ValueError
                status = AlertStatus(item.get("status", AlertStatus.ACTIVE.value))
                if status is not AlertStatus.ACTIVE:
                    continue
                alert_id = item["alert_id"]
                if not isinstance(alert_id, str) or alert_id in seen:
                    continue
                created_at_value = item.get("created_at")
                if created_at_value is None:
                    created_at = datetime.now(timezone.utc)
                elif isinstance(created_at_value, str):
                    created_at = datetime.fromisoformat(created_at_value)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    else:
                        created_at = created_at.astimezone(timezone.utc)
                else:
                    raise ValueError
                alert = Alert(
                    alert_id=alert_id,
                    symbol=str(item["symbol"]),
                    target_price=float(item["target_price"]),
                    condition=AlertCondition(item["condition"]),
                    created_at=created_at,
                    status=AlertStatus.ACTIVE,
                )
                alerts.append(alert)
                seen.add(alert_id)
        except (KeyError, TypeError, ValueError, OverflowError):
            self._load_error = (
                "Saved active alerts are invalid; the file was preserved."
            )
            return []
        for alert in alerts:
            match = re.fullmatch(r"ALERT-(\d+)", alert.alert_id)
            if match is not None:
                stored_next_id = max(stored_next_id, int(match.group(1)) + 1)
        self._next_id = stored_next_id
        return alerts

    def save(self, alerts: list[Alert], next_id: int) -> None:
        if self._load_error is not None:
            raise ActiveAlertStoreError(
                "Active alert storage is unavailable; the saved file was preserved."
            )
        payload = json.dumps(
            {
                "next_id": next_id,
                "alerts": [
                    {
                        "alert_id": alert.alert_id,
                        "symbol": alert.symbol,
                        "target_price": alert.target_price,
                        "condition": alert.condition.value,
                        "created_at": alert.created_at.isoformat(),
                        "status": AlertStatus.ACTIVE.value,
                    }
                    for alert in alerts
                    if alert.status is AlertStatus.ACTIVE
                ]
            },
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
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
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ActiveAlertStoreError(
                "Could not save active alerts."
            ) from None
