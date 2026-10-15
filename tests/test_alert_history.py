"""In-memory alert history component and integration checks."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from data.base_feed import PriceUpdate
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from history.alert_history import AlertHistory
from models.alert import AlertCondition


def test_history_starts_empty_and_recent_empty_is_clean() -> None:
    history = AlertHistory()

    assert history.get_all() == []
    assert history.get_recent() == []
    assert history.get_recent(0) == []


def test_engine_records_trigger_details_and_actual_price() -> None:
    manager = AlertManager()
    history = AlertHistory()
    engine = AlertEngine(manager, alert_history=history)
    alert = manager.create_alert("EURUSD", 1.09, AlertCondition.ABOVE)
    triggered_at = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    engine.evaluate(PriceUpdate("EURUSD", 1.091, triggered_at))
    (record,) = history.get_all()

    assert record.alert_id == alert.alert_id
    assert record.symbol == alert.symbol
    assert record.condition is alert.condition
    assert record.target_price == 1.09
    assert record.current_price == 1.091
    assert record.current_price != record.target_price
    assert record.triggered_at == triggered_at
    assert record.triggered_at.utcoffset() == timedelta(0)


def test_history_preserves_trigger_order_and_returns_recent_records() -> None:
    manager = AlertManager()
    history = AlertHistory()
    engine = AlertEngine(manager, alert_history=history)
    alerts = [
        manager.create_alert("X", target, AlertCondition.ABOVE)
        for target in (1, 2, 3)
    ]
    engine.evaluate(
        PriceUpdate("X", 0, datetime(2026, 1, 1, tzinfo=timezone.utc))
    )
    engine.evaluate(
        PriceUpdate("X", 3, datetime(2026, 1, 2, tzinfo=timezone.utc))
    )

    assert [record.alert_id for record in history.get_all()] == [
        alert.alert_id for alert in alerts
    ]
    assert [record.alert_id for record in history.get_recent(2)] == [
        "ALERT-002",
        "ALERT-003",
    ]
    assert history.get_recent(10) == history.get_all()


def test_duplicate_trigger_record_is_ignored() -> None:
    manager = AlertManager()
    history = AlertHistory()
    alert = manager.create_alert("X", 1, AlertCondition.ABOVE)
    manager.mark_triggered(alert.alert_id)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert history.record_trigger(alert, 2, timestamp)
    assert not history.record_trigger(alert, 3, timestamp)
    assert len(history.get_all()) == 1
    assert history.get_all()[0].current_price == 2


def test_cancelled_alert_cannot_be_recorded() -> None:
    manager = AlertManager()
    history = AlertHistory()
    alert = manager.create_alert("X", 1, AlertCondition.ABOVE)
    manager.cancel_alert(alert.alert_id)

    with pytest.raises(ValueError, match="triggered"):
        history.record_trigger(
            alert, 2, datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
    assert history.get_all() == []


def test_history_record_is_immutable_and_timestamp_normalized_to_utc() -> None:
    manager = AlertManager()
    history = AlertHistory()
    alert = manager.create_alert("X", 1, AlertCondition.ABOVE)
    manager.mark_triggered(alert.alert_id)
    local_timestamp = datetime(
        2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=2))
    )
    history.record_trigger(alert, 1.5, local_timestamp)
    (record,) = history.get_all()

    assert record.triggered_at == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    with pytest.raises(FrozenInstanceError):
        record.current_price = 99  # type: ignore[misc]


def test_negative_recent_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        AlertHistory().get_recent(-1)
