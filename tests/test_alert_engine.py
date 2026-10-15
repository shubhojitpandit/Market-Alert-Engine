"""Component and integration tests for alert evaluation."""

from datetime import datetime, timezone

import pytest

from data.base_feed import PriceUpdate
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from history.alert_history import AlertHistory
from models.alert import Alert, AlertCondition, AlertStatus
from notifications.notification_queue import NotificationQueue


TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def update(instrument: str, price: float) -> PriceUpdate:
    return PriceUpdate(instrument, price, TIMESTAMP)


@pytest.mark.parametrize(
    ("price", "should_trigger"),
    [(9.99, False), (10.0, True), (10.01, True)],
)
def test_above_uses_inclusive_boundary(
    price: float, should_trigger: bool
) -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.ABOVE)
    engine = AlertEngine(manager)

    triggered = engine.evaluate(update("X", price))

    assert (alert in triggered) is should_trigger


@pytest.mark.parametrize(
    ("price", "should_trigger"),
    [(10.01, False), (10.0, True), (9.99, True)],
)
def test_below_uses_inclusive_boundary(
    price: float, should_trigger: bool
) -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.BELOW)
    engine = AlertEngine(manager)

    triggered = engine.evaluate(update("X", price))

    assert (alert in triggered) is should_trigger


@pytest.mark.parametrize(
    ("prices", "should_trigger"),
    [
        ([9, 9.5], False),
        ([9, 10], False),
        ([10, 10.01], True),
        ([11, 11.5], False),
    ],
)
def test_cross_above_boundaries(
    prices: list[float], should_trigger: bool
) -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.CROSS_ABOVE)
    engine = AlertEngine(manager)

    engine.evaluate(update("X", prices[0]))
    triggered = engine.evaluate(update("X", prices[1]))

    assert (alert in triggered) is should_trigger


@pytest.mark.parametrize(
    ("prices", "should_trigger"),
    [
        ([11, 10.5], False),
        ([11, 10], False),
        ([10, 9.99], True),
        ([9, 8.5], False),
    ],
)
def test_cross_below_boundaries(
    prices: list[float], should_trigger: bool
) -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.CROSS_BELOW)
    engine = AlertEngine(manager)

    engine.evaluate(update("X", prices[0]))
    triggered = engine.evaluate(update("X", prices[1]))

    assert (alert in triggered) is should_trigger


def test_first_update_does_not_trigger_crossing_alerts() -> None:
    manager = AlertManager()
    above = manager.create_alert("X", 10, AlertCondition.CROSS_ABOVE)
    below = manager.create_alert("X", 10, AlertCondition.CROSS_BELOW)
    engine = AlertEngine(manager)

    assert engine.evaluate(update("X", 11)) == []
    assert above.status is AlertStatus.ACTIVE
    assert below.status is AlertStatus.ACTIVE


def test_multiple_alerts_trigger_independently_in_registration_order() -> None:
    manager = AlertManager()
    above = manager.create_alert("X", 8, AlertCondition.ABOVE)
    cross = manager.create_alert("X", 9, AlertCondition.CROSS_ABOVE)
    higher = manager.create_alert("X", 12, AlertCondition.ABOVE)
    engine = AlertEngine(manager)

    engine.evaluate(update("X", 7))
    triggered = engine.evaluate(update("X", 10))

    assert triggered == [above, cross]
    assert higher.status is AlertStatus.ACTIVE


def test_instrument_isolation_and_previous_prices_are_independent() -> None:
    manager = AlertManager()
    first = manager.create_alert("A", 10, AlertCondition.CROSS_ABOVE)
    second = manager.create_alert("B", 10, AlertCondition.CROSS_ABOVE)
    engine = AlertEngine(manager)

    assert engine.evaluate(update("A", 9)) == []
    assert engine.evaluate(update("B", 11)) == []
    assert engine.evaluate(update("A", 11)) == [first]
    assert engine.evaluate(update("B", 11.5)) == []
    assert second.status is AlertStatus.ACTIVE


def test_cancelled_alert_never_triggers() -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.ABOVE)
    manager.cancel_alert(alert.alert_id)
    engine = AlertEngine(manager)

    assert engine.evaluate(update("X", 11)) == []
    assert alert.status is AlertStatus.CANCELLED


def test_triggered_alert_does_not_trigger_again() -> None:
    manager = AlertManager()
    alert = manager.create_alert("X", 10, AlertCondition.ABOVE)
    engine = AlertEngine(manager)

    assert engine.evaluate(update("X", 10)) == [alert]
    assert engine.evaluate(update("X", 11)) == []
    assert alert.status is AlertStatus.TRIGGERED
    assert engine.notification_queue.size() == 1


def test_actual_price_order_and_history_notification_integration() -> None:
    manager = AlertManager()
    queue = NotificationQueue()
    history = AlertHistory()
    engine = AlertEngine(manager, queue, history)
    first = manager.create_alert("X", 10, AlertCondition.ABOVE)
    second = manager.create_alert("X", 9, AlertCondition.ABOVE)
    price_update = update("X", 11)

    assert engine.evaluate(price_update) == [first, second]

    notification_items = [queue.dequeue(), queue.dequeue()]
    records = history.get_all()
    assert [item.alert for item in notification_items] == [first, second]
    assert [item.current_price for item in notification_items] == [11, 11]
    assert [record.alert_id for record in records] == [
        first.alert_id,
        second.alert_id,
    ]
    assert [record.current_price for record in records] == [11, 11]
    assert [record.triggered_at for record in records] == [
        price_update.timestamp,
        price_update.timestamp,
    ]


def test_history_failure_does_not_block_notifications(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingHistory(AlertHistory):
        def record_trigger(
            self, alert: Alert, current_price: float, triggered_at: datetime
        ) -> bool:
            raise RuntimeError(
                f"history unavailable for {alert.alert_id} at "
                f"{current_price} on {triggered_at.isoformat()}"
            )

    manager = AlertManager()
    queue = NotificationQueue()
    alert = manager.create_alert("X", 10, AlertCondition.ABOVE)
    engine = AlertEngine(manager, queue, FailingHistory())

    assert engine.evaluate(update("X", 11)) == [alert]
    assert alert.status is AlertStatus.TRIGGERED
    assert queue.size() == 1
    assert "history recording failed" in capsys.readouterr().err
