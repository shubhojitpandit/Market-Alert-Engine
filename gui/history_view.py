"""History tab: read-only AlertHistory display."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import timedelta, timezone

from gui.session import GuiSession
from gui.theme import FONT_HEADING, NAVY, WORKSPACE
from gui.ui_queue import AlertActionQueued, UiMessage
from gui.widgets import AppCard, TactileButton

_COLUMNS = (
    ("alert_id", "Alert ID", 100),
    ("instrument", "Instrument", 160),
    ("condition", "Condition", 120),
    ("threshold", "Threshold", 120),
    ("trigger_price", "Trigger Price", 120),
    ("source", "Source", 100),
    ("triggered_at", "Triggered At", 220),
)


class HistoryView:
    """Read-only table of AlertHistory records."""

    def __init__(self, parent: tk.Widget, session: GuiSession) -> None:
        self._session = session
        self._frame = tk.Frame(parent, bg=WORKSPACE)
        heading = tk.Frame(self._frame, bg=WORKSPACE)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading, text="History", bg=WORKSPACE, fg=NAVY, font=FONT_HEADING
        ).pack(side="left")
        card = AppCard(self._frame, title="", padding=10)
        card.pack(fill="both", expand=True)
        self._body = card.body
        self._build()
        self.refresh()

    @property
    def frame(self) -> tk.Frame:
        return self._frame

    def _build(self) -> None:
        records = ttk.LabelFrame(self._body, text="TRIGGERED ALERTS")
        records.pack(fill="both", expand=True, pady=(4, 8))
        self._tree = ttk.Treeview(
            records,
            columns=[column[0] for column in _COLUMNS],
            show="headings",
            height=16,
            selectmode="browse",
        )
        for name, heading, width in _COLUMNS:
            self._tree.heading(name, text=heading)
            self._tree.column(
                name,
                width=width,
                stretch=True,
                anchor="w" if name == "alert_id" else "center",
            )
        self._tree.pack(fill="both", expand=True, padx=6, pady=6)
        TactileButton(self._body, "Refresh", self.refresh).pack(anchor="w", pady=(0, 8))
        self._status = ttk.Label(self._body, text="", style="Status.TLabel")
        self._status.pack(anchor="w", pady=(0, 4))

    def handle_ui_message(self, message: UiMessage) -> None:
        if isinstance(message, AlertActionQueued):
            self.refresh()

    def refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        records = self._session.list_alert_history()
        for record in reversed(records):
            timestamp = record.triggered_at.astimezone(
                timezone(timedelta(hours=5, minutes=30))
            ).strftime("%Y-%m-%d %H:%M:%S")
            self._tree.insert(
                "",
                "end",
                iid=record.alert_id,
                values=(
                    record.alert_id,
                    self._session.get_instrument_display_name(record.symbol),
                    record.condition.value,
                    f"{record.target_price:.5f}",
                    f"{record.current_price:.5f}",
                    "Recovered" if record.recovered else "Live",
                    timestamp,
                ),
            )
        if records:
            self._status.configure(text=f"{len(records)} record(s).")
        else:
            self._status.configure(text="NO RECORDS")
