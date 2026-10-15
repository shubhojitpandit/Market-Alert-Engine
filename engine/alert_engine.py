"""Evaluate price updates against active alerts."""

import sys
from dataclasses import replace
from datetime import datetime

from data.active_alert_store import ActiveAlertStoreError
from data.base_feed import PriceUpdate
from engine.alert_manager import AlertManager
from history.alert_history import AlertHistory
from models.alert import Alert, AlertCondition, AlertStatus
from notifications.notification_queue import NotificationQueue


class AlertEngine:
    """Evaluate alerts and track the previous price for each instrument."""

    def __init__(
        self,
        alert_manager: AlertManager,
        notification_queue: NotificationQueue | None = None,
        alert_history: AlertHistory | None = None,
    ) -> None:
        self._alert_manager = alert_manager
        self.notification_queue = (
            notification_queue
            if notification_queue is not None
            else NotificationQueue()
        )
        self.alert_history = (
            alert_history if alert_history is not None else AlertHistory()
        )
        self._previous_prices: dict[str, float] = {}

    def evaluate(self, update: PriceUpdate) -> list[Alert]:
        """Evaluate one update and return the alerts it triggered."""
        previous_price = self._previous_prices.get(update.instrument)
        triggered: list[Alert] = []
        persistence_failed = False

        for alert in self._alert_manager.list_active_alerts():
            if alert.symbol != update.instrument:
                continue
            if self._condition_is_satisfied(alert, previous_price, update.price):
                try:
                    triggered_alert = self._complete_trigger(
                        alert, update.price, update.timestamp, recovered=False
                    )
                except (
                    ActiveAlertStoreError,
                ) as error:
                    persistence_failed = True
                    print(
                        f"Active alert persistence failed for "
                        f"{alert.alert_id}: {error}",
                        file=sys.stderr,
                    )
                    continue
                except (
                    AttributeError,
                    OverflowError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as error:
                    print(
                        f"Alert history recording failed for "
                        f"{alert.alert_id}: {error}",
                        file=sys.stderr,
                    )
                    continue
                triggered.append(triggered_alert)

        if not persistence_failed:
            self._previous_prices[update.instrument] = update.price
        return triggered

    def evaluate_new_alert(
        self, alert: Alert, update: PriceUpdate
    ) -> Alert | None:
        """Evaluate a new alert against the latest price without a prior tick."""
        if alert.symbol != update.instrument:
            return None
        if not self._condition_is_satisfied(alert, None, update.price):
            return None
        return self._complete_trigger(
            alert, update.price, update.timestamp, recovered=False
        )

    def complete_recovered_trigger(
        self,
        alert_id: str,
        current_price: float,
        triggered_at: datetime,
    ) -> Alert:
        """Complete a recovered trigger through the existing history and manager."""
        alert = self._alert_manager.get_alert(alert_id)
        return self._complete_trigger(
            alert, current_price, triggered_at, recovered=True
        )

    def _complete_trigger(
        self,
        alert: Alert,
        current_price: float,
        triggered_at: datetime,
        *,
        recovered: bool,
    ) -> Alert:
        history_alert = replace(alert, status=AlertStatus.TRIGGERED)
        if recovered:
            newly_recorded = self.alert_history.record_trigger(
                history_alert,
                current_price,
                triggered_at,
                recovered=True,
            )
        else:
            newly_recorded = self.alert_history.record_trigger(
                history_alert, current_price, triggered_at
            )
        triggered_alert = self._alert_manager.mark_triggered(alert.alert_id)
        if newly_recorded:
            if recovered:
                self.notification_queue.enqueue(
                    triggered_alert, current_price, recovered=True
                )
            else:
                self.notification_queue.enqueue(triggered_alert, current_price)
        return triggered_alert

    @staticmethod
    def _condition_is_satisfied(
        alert: Alert, previous_price: float | None, current_price: float
    ) -> bool:
        """Return whether an alert condition is satisfied by this update."""
        if alert.condition is AlertCondition.ABOVE:
            return current_price >= alert.target_price
        if alert.condition is AlertCondition.BELOW:
            return current_price <= alert.target_price
        if previous_price is None:
            return False
        if alert.condition is AlertCondition.CROSS_ABOVE:
            return (
                previous_price <= alert.target_price
                and current_price > alert.target_price
            )
        if alert.condition is AlertCondition.CROSS_BELOW:
            return (
                previous_price >= alert.target_price
                and current_price < alert.target_price
            )
        return False
