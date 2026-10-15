"""Market Watch: live LTP display from queued PriceUpdate events."""

from __future__ import annotations

import math
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from data.base_feed import PriceUpdate
from gui.session import GuiSession
from gui.theme import (
    FONT_HEADING,
    FONT_SECTION,
    FONT_UI,
    LIVE,
    NAVY,
    RECESS,
    TEXT_MUTED,
    WHITE,
    WORKSPACE,
)
from gui.ui_queue import PriceQueued, UiMessage
from gui.widgets import AppCard, StatusBadge, StatusCard

_COLUMNS = (
    ("instrument", "Instrument", 220),
    ("ltp", "LTP", 120),
    ("exchange", "Exchange", 90),
    ("last_update", "Last Update", 120),
    ("status", "Status", 90),
)


class MarketWatchView:
    """Display-only live prices applied on the Tk thread from UiQueue messages."""

    def __init__(self, parent: tk.Widget, session: GuiSession) -> None:
        self._session = session
        self._has_data = False
        self._last_update_text = "—"
        self._frame = tk.Frame(parent, bg=WORKSPACE)

        heading = tk.Frame(self._frame, bg=WORKSPACE)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading,
            text="Market Watch",
            bg=WORKSPACE,
            fg=NAVY,
            font=FONT_HEADING,
        ).pack(side="left")
        self._live = StatusBadge(heading, "IDLE", kind="idle")
        self._live.pack(side="right")

        table_card = AppCard(self._frame, title="", padding=10)
        table_card.pack(fill="both", expand=True)
        self._empty_wrap = tk.Frame(table_card.body, bg=RECESS)
        self._empty_wrap.pack(fill="both", expand=True)
        empty_content = tk.Frame(self._empty_wrap, bg=RECESS)
        empty_content.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            empty_content,
            text="MARKET FEED",
            bg=RECESS,
            fg=TEXT_MUTED,
            font=FONT_SECTION,
        ).pack(pady=(0, 4))
        self._empty = tk.Label(
            empty_content,
            text="NO ACTIVE MARKET DATA",
            bg=RECESS,
            fg=NAVY,
            font=FONT_SECTION,
        )
        self._empty.pack()
        tk.Label(
            empty_content,
            text="Live prices will appear here when available.",
            bg=RECESS,
            fg=TEXT_MUTED,
            font=FONT_UI,
        ).pack(pady=(4, 0))

        self._tree = ttk.Treeview(
            table_card.body,
            columns=[column[0] for column in _COLUMNS],
            show="headings",
            height=12,
            selectmode="browse",
            style="Watch.Treeview",
        )
        for name, heading_text, width in _COLUMNS:
            anchor = "w" if name == "instrument" else "center"
            self._tree.heading(name, text=heading_text, anchor=anchor)
            self._tree.column(name, width=width, stretch=True, anchor=anchor)
        self._tree.tag_configure("odd", background="#fffaf5")
        self._tree.tag_configure("even", background="#ffffff")

        cards = tk.Frame(self._frame, bg=WORKSPACE)
        cards.pack(fill="x", pady=(10, 0))
        self._feed_card = StatusCard(cards, "MARKET FEED", icon="feed")
        self._feed_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._feed_card.value.configure(text="Waiting for live market data.")
        self._count_card = StatusCard(cards, "CONNECTED INSTRUMENTS", icon="count")
        self._count_card.pack(side="left", fill="both", expand=True)
        self._count_card.value.configure(text="0\nLast Update: —")

    @property
    def frame(self) -> tk.Frame:
        return self._frame

    def handle_ui_message(self, message: UiMessage) -> None:
        if not isinstance(message, PriceQueued):
            return
        self.apply_price_update(message.update)

    def apply_price_update(self, update: PriceUpdate) -> None:
        """Create or update one row from a PriceUpdate. Safe only on the Tk thread."""
        parsed = self._parse_update(update)
        if parsed is None:
            return
        key, display, ltp, exchange, last_update = parsed
        self._show_table()
        values = (display, ltp, exchange, last_update, "LIVE")
        if self._tree.exists(key):
            self._tree.item(key, values=values)
        else:
            count = len(self._tree.get_children())
            tag = "odd" if count % 2 else "even"
            self._tree.insert("", "end", iid=key, values=values, tags=(tag,))
        self._last_update_text = last_update
        self._refresh_cards()

    def _exchange_for(self, key: str) -> str:
        for instrument in self._session.list_saved_instruments():
            if instrument.instrument_key == key:
                return instrument.exchange or "—"
        return "—"

    def _parse_update(
        self, update: PriceUpdate
    ) -> tuple[str, str, str, str, str] | None:
        try:
            key = update.instrument.strip()
            if not key:
                return None
            price = float(update.price)
            if not math.isfinite(price):
                return None
            timestamp = update.timestamp
            if not isinstance(timestamp, datetime):
                return None
            display = self._session.get_instrument_display_name(key)
            last_update = timestamp.strftime("%H:%M:%S")
            return key, display, f"{price:.2f}", self._exchange_for(key), last_update
        except (AttributeError, OverflowError, TypeError, ValueError):
            return None

    def _refresh_cards(self) -> None:
        count = len(self._tree.get_children())
        self._count_card.value.configure(
            text=f"{count}\nLast Update: {self._last_update_text}"
        )
        self._feed_card.value.configure(text="Receiving live market data...")

    def _show_table(self) -> None:
        if self._has_data:
            return
        self._empty_wrap.pack_forget()
        self._tree.pack(fill="both", expand=True)
        self._live.set_state("LIVE", "live")
        self._has_data = True
