"""Alerts tab: create and cancel alerts through GuiSession / AlertManager."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from models.alert import AlertCondition
from gui.session import GuiSession
from gui.theme import FONT_HEADING, NAVY, WORKSPACE
from gui.ui_queue import AlertActionQueued, UiMessage
from gui.widgets import AppCard, TactileButton
from notifications.sound_settings import SoundSettingsError

_COLUMNS = (
    ("alert_id", "Alert ID", 100),
    ("instrument", "Instrument", 160),
    ("condition", "Condition", 120),
    ("threshold", "Threshold", 120),
    ("status", "Status", 100),
)
_CONDITIONS = (
    AlertCondition.ABOVE.value,
    AlertCondition.BELOW.value,
    AlertCondition.CROSS_ABOVE.value,
    AlertCondition.CROSS_BELOW.value,
)


class AlertsView:
    """Create and list alerts using existing AlertManager state."""

    def __init__(
        self,
        parent: tk.Widget,
        session: GuiSession,
        *,
        confirm: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._session = session
        self._confirm = confirm or self._confirm_dialog
        self._instrument_keys: list[str] = []
        self._frame = tk.Frame(parent, bg=WORKSPACE)
        heading = tk.Frame(self._frame, bg=WORKSPACE)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading, text="Alerts", bg=WORKSPACE, fg=NAVY, font=FONT_HEADING
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
        create = ttk.LabelFrame(self._body, text="CREATE ALERT")
        create.pack(fill="x", pady=(4, 10))

        row = ttk.Frame(create)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Instrument").pack(side="left")
        self._instrument = ttk.Combobox(row, state="readonly", width=36)
        self._instrument.pack(side="left", padx=(4, 12))
        ttk.Label(row, text="Condition").pack(side="left")
        self._condition = ttk.Combobox(
            row, state="readonly", width=14, values=_CONDITIONS
        )
        self._condition.set(AlertCondition.ABOVE.value)
        self._condition.pack(side="left", padx=(4, 12))
        ttk.Label(row, text="Threshold").pack(side="left")
        self._threshold = ttk.Entry(row, width=14)
        self._threshold.pack(side="left", padx=(4, 12))
        TactileButton(row, "Create", self.create_alert, variant="primary").pack(side="left")

        sound_box = ttk.LabelFrame(self._body, text="ALERT SOUND")
        sound_box.pack(fill="x", pady=(0, 8))
        sound_row = ttk.Frame(sound_box)
        sound_row.pack(fill="x", padx=8, pady=6)
        ttk.Label(sound_row, text="Sound").pack(side="left")
        self._sound = ttk.Combobox(
            sound_row, state="readonly", width=26
        )
        self._sound.pack(side="left", padx=(4, 8))
        self._sound.bind("<<ComboboxSelected>>", self._select_sound)
        TactileButton(sound_row, "+ Add Sound", self._add_sound).pack(
            side="left", padx=(0, 6)
        )
        TactileButton(sound_row, "Test Sound", self._test_sound).pack(
            side="left", padx=(0, 14)
        )
        ttk.Label(sound_row, text="Playback").pack(side="left")
        self._playback = ttk.Combobox(
            sound_row, state="readonly", width=8, values=("Once", "Twice")
        )
        self._playback.pack(side="left", padx=(4, 0))
        self._playback.bind("<<ComboboxSelected>>", self._select_playback)
        self._sound_ids: dict[str, str] = {}

        buttons = tk.Frame(self._body, bg="#ffffff")
        buttons.pack(fill="x", pady=(0, 8))
        TactileButton(
            buttons, "Cancel Alert", self.cancel_selected, variant="danger"
        ).pack(side="left", padx=(0, 8))
        TactileButton(buttons, "Refresh", self.refresh).pack(side="left")

        active = ttk.LabelFrame(self._body, text="ACTIVE ALERTS")
        active.pack(fill="both", expand=True, pady=(0, 8))
        self._tree = ttk.Treeview(
            active,
            columns=[column[0] for column in _COLUMNS],
            show="headings",
            height=12,
            selectmode="browse",
        )
        for name, heading, width in _COLUMNS:
            self._tree.heading(
                name, text=heading, anchor="center" if name == "alert_id" else "w"
            )
            if name in {"condition", "threshold", "status"}:
                self._tree.column(name, width=width, stretch=True, anchor="center")
            elif name == "alert_id":
                self._tree.column(name, width=width, stretch=True, anchor="center")
            else:
                self._tree.column(name, width=width, stretch=True)
        self._tree.pack(fill="both", expand=True, padx=6, pady=6)

        self._status = ttk.Label(self._body, text="", style="Status.TLabel")
        self._status.pack(anchor="w", pady=(0, 4))
        self._refresh_sound_controls()

    def _confirm_dialog(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, parent=self._frame.winfo_toplevel())

    def handle_ui_message(self, message: UiMessage) -> None:
        if not isinstance(message, AlertActionQueued):
            return
        self.refresh()
        self._set_status(message.message)

    def _refresh_sound_controls(self) -> None:
        try:
            choices = self._session.list_alert_sounds()
            selected = self._session.selected_alert_sound()
            labels: list[str] = []
            self._sound_ids = {}
            selected_label = ""
            selected_missing = False
            for choice in choices:
                label = choice.name
                missing = choice.path is not None and not choice.path.is_file()
                if missing:
                    label = f"{label} (missing)"
                base_label = label
                suffix = 2
                while label in self._sound_ids:
                    label = f"{base_label} ({suffix})"
                    suffix += 1
                labels.append(label)
                self._sound_ids[label] = choice.sound_id
                if choice.sound_id == selected:
                    selected_label = label
                    selected_missing = missing
            self._sound["values"] = labels
            if labels:
                self._sound.set(selected_label or labels[0])
            self._playback.set(self._session.alert_sound_playback())
            if selected_missing:
                self._set_status(
                    "Selected sound file is missing. Import it again or choose another."
                )
        except SoundSettingsError:
            self._set_status("Could not load alert sound settings.")

    def _select_sound(self, _event: object = None) -> None:
        sound_id = self._sound_ids.get(self._sound.get())
        if sound_id is None:
            return
        try:
            self._session.select_alert_sound(sound_id)
        except SoundSettingsError as error:
            self._set_status(str(error))
            self._refresh_sound_controls()

    def _select_playback(self, _event: object = None) -> None:
        try:
            self._session.set_alert_sound_playback(self._playback.get())
        except (SoundSettingsError, ValueError):
            self._set_status("Could not save alert sound playback setting.")
            self._refresh_sound_controls()

    def _add_sound(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._frame.winfo_toplevel(),
            title="Add Alert Sound",
            filetypes=(
                ("Audio files", "*.wav *.mp3"),
                ("WAV audio", "*.wav"),
                ("MP3 audio", "*.mp3"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        try:
            self._session.import_alert_sound(Path(selected))
        except SoundSettingsError as error:
            self._set_status(str(error))
            return
        self._refresh_sound_controls()
        self._set_status("Alert sound added.")

    def _test_sound(self) -> None:
        try:
            self._session.test_alert_sound()
        except SoundSettingsError as error:
            self._set_status(str(error))
        except Exception:
            self._set_status("Could not play the selected alert sound.")

    def refresh_instruments(self) -> None:
        options = self._session.available_alert_instruments()
        previous = self._instrument.current()
        self._instrument_keys = [key for key, _name in options]
        self._instrument["values"] = [name for _key, name in options]
        if not options:
            self._instrument.set("")
            return
        if 0 <= previous < len(options):
            self._instrument.current(previous)
        elif not self._instrument.get():
            self._instrument.current(0)

    def refresh(self) -> None:
        self.refresh_instruments()
        selected = self._tree.selection()
        self._tree.delete(*self._tree.get_children())
        for alert in self._session.list_active_alerts():
            self._tree.insert(
                "",
                "end",
                iid=alert.alert_id,
                values=(
                    alert.alert_id,
                    self._session.get_instrument_label(alert.symbol),
                    alert.condition.value,
                    f"{alert.target_price:.5f}",
                    alert.status.value,
                ),
            )
        remaining = [item for item in selected if self._tree.exists(item)]
        if remaining:
            self._tree.selection_set(remaining)

    def create_alert(self) -> None:
        index = self._instrument.current()
        if index < 0 or index >= len(self._instrument_keys):
            self._set_status("Select an instrument.")
            return
        symbol = self._instrument_keys[index]
        try:
            self._session.start_create_alert(
                symbol, self._threshold.get(), self._condition.get()
            )
        except ValueError as error:
            self._set_status(str(error))
            return
        self._threshold.delete(0, "end")
        self._set_status("Creating alert...")

    def cancel_selected(self) -> None:
        selected = self._tree.selection()
        if not selected:
            self._set_status("Select an alert to cancel.")
            return
        alert_id = selected[0]
        if not self._confirm(
            "Cancel alert?",
            f"Cancel {alert_id}? Alert history will not be deleted.",
        ):
            self._set_status("Cancel cancelled.")
            return
        try:
            self._session.start_cancel_alert(alert_id)
        except ValueError as error:
            self._set_status(str(error))
            return
        self._set_status("Cancelling alert...")

    def _set_status(self, text: str) -> None:
        self._status.configure(text=text)
