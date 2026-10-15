"""Data model for a managed price alert."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math


class AlertCondition(str, Enum):
    """Supported alert condition types."""

    ABOVE = "ABOVE"
    BELOW = "BELOW"
    CROSS_ABOVE = "CROSS_ABOVE"
    CROSS_BELOW = "CROSS_BELOW"


class AlertStatus(str, Enum):
    """Lifecycle states for an alert."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    CANCELLED = "CANCELLED"


@dataclass
class Alert:
    """Represent one alert and its current lifecycle state."""

    alert_id: str
    symbol: str
    target_price: float
    condition: AlertCondition
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    status: AlertStatus = AlertStatus.CREATED

    def __post_init__(self) -> None:
        if not isinstance(self.alert_id, str) or not self.alert_id.strip():
            raise ValueError("alert_id must not be empty")

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        self.symbol = self.symbol.strip()

        if isinstance(self.target_price, bool) or not isinstance(
            self.target_price, (int, float)
        ):
            raise ValueError("target_price must be a positive finite number")
        try:
            price = float(self.target_price)
        except OverflowError as error:
            raise ValueError(
                "target_price must be a positive finite number"
            ) from error
        if not math.isfinite(price) or price <= 0:
            raise ValueError("target_price must be a positive finite number")
        self.target_price = price

        if not isinstance(self.condition, AlertCondition):
            raise ValueError("condition must be a supported AlertCondition")
        if not isinstance(self.status, AlertStatus):
            raise ValueError("status must be a supported AlertStatus")
