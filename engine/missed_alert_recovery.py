"""Conservatively evaluate historical candles for missed alert triggers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from data.historical_candle import HistoricalCandle
from models.alert import Alert, AlertCondition


@dataclass(frozen=True)
class RecoveredTrigger:
    """Candle-derived values for a confirmed recovered trigger."""

    current_price: float
    triggered_at: datetime


class MissedAlertRecovery:
    """Find only triggers that OHLC candle data can establish confidently."""

    @staticmethod
    def find_trigger(
        alert: Alert,
        candles: list[HistoricalCandle],
        recovery_start: datetime,
        recovery_end: datetime,
        interval_seconds: int,
    ) -> RecoveredTrigger | None:
        if interval_seconds <= 0:
            raise ValueError("candle interval must be positive")
        boundary = max(
            MissedAlertRecovery._utc(recovery_start),
            MissedAlertRecovery._utc(alert.created_at),
        )
        end = MissedAlertRecovery._utc(recovery_end)
        first_full_candle = MissedAlertRecovery._ceil_to_interval(
            boundary, interval_seconds
        )
        for candle in sorted(candles, key=lambda item: item.timestamp):
            candle_start = MissedAlertRecovery._utc(candle.timestamp)
            if candle_start >= end:
                break
            if candle_start < first_full_candle:
                continue

            if alert.condition is AlertCondition.ABOVE:
                if candle.high >= alert.target_price:
                    return RecoveredTrigger(candle.high, candle_start)
            elif alert.condition is AlertCondition.BELOW:
                if candle.low <= alert.target_price:
                    return RecoveredTrigger(candle.low, candle_start)
            elif alert.condition is AlertCondition.CROSS_ABOVE:
                if (
                    candle.low <= alert.target_price
                    and candle.close > alert.target_price
                ):
                    return RecoveredTrigger(candle.close, candle_start)
            elif alert.condition is AlertCondition.CROSS_BELOW:
                if (
                    candle.high >= alert.target_price
                    and candle.close < alert.target_price
                ):
                    return RecoveredTrigger(candle.close, candle_start)
        return None

    @staticmethod
    def _ceil_to_interval(value: datetime, interval_seconds: int) -> datetime:
        timestamp = value.timestamp()
        interval_index = int(timestamp // interval_seconds)
        if interval_index * interval_seconds < timestamp:
            interval_index += 1
        return datetime.fromtimestamp(
            interval_index * interval_seconds, tz=timezone.utc
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
