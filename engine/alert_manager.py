"""Manage alert registration, retrieval, and lifecycle state."""

from pathlib import Path

from data.active_alert_store import ActiveAlertStore
from models.alert import Alert, AlertCondition, AlertStatus


class AlertManager:
    """Store alerts in registration order and manage their state."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._store = (
            ActiveAlertStore(storage_path) if storage_path is not None else None
        )
        self._alerts: dict[str, Alert] = {}
        self._next_id = 1
        if self._store is not None:
            for alert in self._store.load():
                self._alerts[alert.alert_id] = alert
            self._next_id = self._store.next_id

    @property
    def storage_error(self) -> str | None:
        return self._store.load_error if self._store is not None else None

    def create_alert(
        self, symbol: str, target_price: float, condition: AlertCondition
    ) -> Alert:
        """Create and register an active alert with the next sequential ID."""
        alert_id = f"ALERT-{self._next_id:03d}"
        alert = Alert(
            alert_id=alert_id,
            symbol=symbol,
            target_price=target_price,
            condition=condition,
        )
        alert.status = AlertStatus.ACTIVE
        self._save_active(
            [*self.list_active_alerts(), alert], next_id=self._next_id + 1
        )
        self._alerts[alert_id] = alert
        self._next_id += 1
        return alert

    def get_alert(self, alert_id: str) -> Alert:
        """Return an alert by ID, raising KeyError when it is not managed."""
        return self._alerts[alert_id]

    def list_active_alerts(self) -> list[Alert]:
        """Return active alerts in registration order."""
        return [
            alert
            for alert in self._alerts.values()
            if alert.status is AlertStatus.ACTIVE
        ]

    def list_alerts(self) -> list[Alert]:
        """Return all managed alerts in registration order."""
        return list(self._alerts.values())

    def cancel_alert(self, alert_id: str) -> Alert:
        """Cancel an active alert and return the updated alert."""
        alert = self.get_alert(alert_id)
        if alert.status is not AlertStatus.ACTIVE:
            raise ValueError(f"Cannot cancel alert in {alert.status.value} state")
        self._save_active(
            [
                item
                for item in self.list_active_alerts()
                if item.alert_id != alert_id
            ]
        )
        alert.status = AlertStatus.CANCELLED
        return alert

    def mark_triggered(self, alert_id: str) -> Alert:
        """Mark an active alert as triggered and return the updated alert."""
        alert = self.get_alert(alert_id)
        if alert.status is not AlertStatus.ACTIVE:
            raise ValueError(
                f"Cannot trigger alert in {alert.status.value} state"
            )
        self._save_active(
            [
                item
                for item in self.list_active_alerts()
                if item.alert_id != alert_id
            ]
        )
        alert.status = AlertStatus.TRIGGERED
        return alert

    def _save_active(
        self, alerts: list[Alert], next_id: int | None = None
    ) -> None:
        if self._store is not None:
            self._store.save(alerts, next_id or self._next_id)
