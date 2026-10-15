"""Queue tests use fake handlers; no real sound or toast is emitted."""

import pytest

from engine.alert_manager import AlertManager
from models.alert import AlertCondition
from notifications.notification_handler import NotificationHandler
from notifications.notification_queue import Notification, NotificationQueue


class RecordingHandler:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def handle(self, notification: Notification) -> None:
        self.notifications.append(notification)


def triggered_alerts(count: int):
    manager = AlertManager()
    alerts = [
        manager.create_alert(f"SYM{index}", index + 1, AlertCondition.ABOVE)
        for index in range(count)
    ]
    for alert in alerts:
        manager.mark_triggered(alert.alert_id)
    return alerts


def test_enqueue_dequeue_and_process_are_fifo() -> None:
    alerts = triggered_alerts(3)
    queue = NotificationQueue()
    for index, alert in enumerate(alerts):
        queue.enqueue(alert, 10 + index)

    assert queue.size() == 3
    assert [queue.dequeue().alert for _ in range(3)] == alerts
    assert queue.is_empty()


def test_empty_queue_behavior() -> None:
    queue = NotificationQueue()
    handler = RecordingHandler()

    assert queue.size() == 0
    assert queue.is_empty()
    assert queue.process_next(handler) is None
    assert queue.process_all(handler) == []
    assert handler.notifications == []
    with pytest.raises(IndexError):
        queue.dequeue()


def test_process_all_delivers_every_simultaneous_notification_once() -> None:
    alerts = triggered_alerts(5)
    queue = NotificationQueue()
    for alert in alerts:
        queue.enqueue(alert, 25)
    handler = RecordingHandler()

    processed = queue.process_all(handler)

    assert [item.alert for item in processed] == alerts
    assert handler.notifications == processed
    assert len({item.alert.alert_id for item in processed}) == 5
    assert queue.is_empty()


def test_handler_channel_failures_do_not_stop_queue_processing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alerts = triggered_alerts(3)
    queue = NotificationQueue()
    for alert in alerts:
        queue.enqueue(alert, 10)

    class BrokenChannelsHandler(NotificationHandler):
        @staticmethod
        def _play_sound(notification: Notification) -> None:
            raise OSError(
                f"{notification.alert.alert_id} sound unavailable"
            )

        @staticmethod
        def _show_desktop_notification(notification: Notification) -> None:
            raise RuntimeError(
                f"{notification.alert.alert_id} toast unavailable"
            )

    processed = queue.process_all(BrokenChannelsHandler())
    errors = capsys.readouterr().err

    assert [item.alert for item in processed] == alerts
    assert queue.is_empty()
    assert errors.count("sound failed") == 3
    assert errors.count("desktop notification failed") == 3


def test_non_triggered_alert_cannot_be_queued() -> None:
    manager = AlertManager()
    alert = manager.create_alert("EURUSD", 1.09, AlertCondition.ABOVE)
    queue = NotificationQueue()

    with pytest.raises(ValueError, match="triggered"):
        queue.enqueue(alert, 1.1)


def test_notification_message_includes_alert_and_trigger_prices() -> None:
    alert = triggered_alerts(1)[0]
    notification = Notification(alert, 12.345)

    assert "ALERT-001" in notification.message
    assert "SYM0" in notification.message
    assert "ABOVE" in notification.message
    assert "1.00" in notification.message
    assert "12.35" in notification.message
