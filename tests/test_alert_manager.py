"""Component tests for alert storage and lifecycle management."""

import pytest

from engine.alert_manager import AlertManager
from models.alert import AlertCondition, AlertStatus


def test_registers_alerts_with_sequential_ids_and_active_status() -> None:
    manager = AlertManager()
    first = manager.create_alert("EURUSD", 1.09, AlertCondition.ABOVE)
    second = manager.create_alert("BTCUSD", 65000, AlertCondition.BELOW)

    assert first.alert_id == "ALERT-001"
    assert second.alert_id == "ALERT-002"
    assert first.status is AlertStatus.ACTIVE
    assert second.status is AlertStatus.ACTIVE


@pytest.mark.parametrize("condition", list(AlertCondition))
def test_all_supported_conditions_can_be_registered(
    condition: AlertCondition,
) -> None:
    alert = AlertManager().create_alert("EURUSD", 1.09, condition)

    assert alert.condition is condition


def test_active_and_all_alert_views_preserve_registration_order() -> None:
    manager = AlertManager()
    first = manager.create_alert("EURUSD", 1.09, AlertCondition.ABOVE)
    second = manager.create_alert("BTCUSD", 65000, AlertCondition.BELOW)
    third = manager.create_alert("AAPL", 200, AlertCondition.CROSS_ABOVE)

    manager.cancel_alert(second.alert_id)
    manager.mark_triggered(third.alert_id)

    assert manager.list_active_alerts() == [first]
    assert manager.list_alerts() == [first, second, third]
    assert manager.get_alert(first.alert_id) is first


def test_cancel_and_trigger_update_status_and_leave_active_collection() -> None:
    manager = AlertManager()
    cancelled = manager.create_alert("EURUSD", 1.09, AlertCondition.ABOVE)
    triggered = manager.create_alert("BTCUSD", 65000, AlertCondition.BELOW)

    manager.cancel_alert(cancelled.alert_id)
    manager.mark_triggered(triggered.alert_id)

    assert cancelled.status is AlertStatus.CANCELLED
    assert triggered.status is AlertStatus.TRIGGERED
    assert manager.list_active_alerts() == []


@pytest.mark.parametrize(
    ("symbol", "price", "condition"),
    [
        ("", 1, AlertCondition.ABOVE),
        ("   ", 1, AlertCondition.ABOVE),
        ("EURUSD", 0, AlertCondition.ABOVE),
        ("EURUSD", -1, AlertCondition.ABOVE),
        ("EURUSD", float("nan"), AlertCondition.ABOVE),
        ("EURUSD", float("inf"), AlertCondition.ABOVE),
        ("EURUSD", True, AlertCondition.ABOVE),
        ("EURUSD", 1, "ABOVE"),
    ],
)
def test_invalid_alert_data_is_rejected(
    symbol: str, price: float, condition: AlertCondition | str
) -> None:
    with pytest.raises(ValueError):
        AlertManager().create_alert(symbol, price, condition)  # type: ignore[arg-type]


def test_invalid_registration_does_not_consume_an_alert_id() -> None:
    manager = AlertManager()

    with pytest.raises(ValueError):
        manager.create_alert("EURUSD", 0, AlertCondition.ABOVE)

    assert (
        manager.create_alert("EURUSD", 1, AlertCondition.ABOVE).alert_id
        == "ALERT-001"
    )


@pytest.mark.parametrize("transition", ["cancel", "trigger"])
def test_non_active_alert_cannot_be_transitioned_again(
    transition: str,
) -> None:
    manager = AlertManager()
    alert = manager.create_alert("EURUSD", 1, AlertCondition.ABOVE)
    manager.cancel_alert(alert.alert_id)
    change = manager.cancel_alert if transition == "cancel" else manager.mark_triggered

    with pytest.raises(ValueError):
        change(alert.alert_id)


def test_triggered_alert_cannot_be_cancelled_and_cancelled_cannot_be_triggered() -> None:
    manager = AlertManager()
    triggered = manager.create_alert("EURUSD", 1, AlertCondition.ABOVE)
    cancelled = manager.create_alert("BTCUSD", 1, AlertCondition.BELOW)
    manager.mark_triggered(triggered.alert_id)
    manager.cancel_alert(cancelled.alert_id)

    with pytest.raises(ValueError):
        manager.cancel_alert(triggered.alert_id)
    with pytest.raises(ValueError):
        manager.mark_triggered(cancelled.alert_id)
