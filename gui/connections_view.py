"""Connections tab: create, connect, disconnect, and delete via GuiSession."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from gui.dialogs import place_modal_dialog
from gui.session import GuiSession
from gui.theme import (
    FONT_HEADING,
    NAVY,
    WORKSPACE,
    format_connection_status,
    style_listbox,
    style_window,
)
from gui.widgets import AppCard, TactileButton, attach_dialog_header
from gui.ui_queue import ConnectionActionQueued, UiMessage

_COLUMNS = (
    ("name", "Name", 180),
    ("provider", "Provider", 90),
    ("instruments", "Instruments", 280),
    ("status", "Status", 220),
)


class ConnectionsView:
    """Connection list and lifecycle controls."""

    def __init__(
        self,
        parent: tk.Widget,
        session: GuiSession,
        *,
        confirm: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._session = session
        self._confirm = confirm or self._confirm_dialog
        self._frame = tk.Frame(parent, bg=WORKSPACE)
        heading = tk.Frame(self._frame, bg=WORKSPACE)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading, text="Connections", bg=WORKSPACE, fg=NAVY, font=FONT_HEADING
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
        listing = ttk.LabelFrame(self._body, text="CONNECTIONS")
        listing.pack(fill="both", expand=True, pady=(4, 8))
        self._tree = ttk.Treeview(
            listing,
            columns=[column[0] for column in _COLUMNS],
            show="headings",
            height=12,
            selectmode="browse",
        )
        for name, heading, width in _COLUMNS:
            anchor = "center" if name in ("provider", "status") else "w"
            self._tree.heading(name, text=heading, anchor=anchor)
            self._tree.column(name, width=width, stretch=True, anchor=anchor)
        self._tree.pack(fill="both", expand=True, padx=6, pady=6)

        buttons = tk.Frame(self._body, bg="#ffffff")
        self._buttons = buttons
        buttons.pack(fill="x", pady=(0, 8))
        TactileButton(buttons, "Create Connection", self.open_create, variant="primary").pack(
            side="left", padx=(0, 8)
        )
        TactileButton(buttons, "Connect", self.connect_selected, variant="primary").pack(
            side="left", padx=(0, 8)
        )
        TactileButton(buttons, "Disconnect", self.disconnect_selected).pack(
            side="left", padx=(0, 8)
        )
        TactileButton(
            buttons, "Delete Connection", self.delete_selected, variant="danger"
        ).pack(side="left")

        self._status = ttk.Label(self._body, text="", style="Status.TLabel")
        self._status.pack(anchor="w", pady=(0, 4))

    def _confirm_dialog(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, parent=self._frame.winfo_toplevel())

    def handle_ui_message(self, message: UiMessage) -> None:
        if not isinstance(message, ConnectionActionQueued):
            return
        self.refresh()
        self._set_status(message.message)

    def refresh(self) -> None:
        selected = self._tree.selection()
        self._tree.delete(*self._tree.get_children())
        for connection in self._session.list_connections():
            status = self._session.get_connection_status(connection.connection_id)
            provider = {
                "upstox": "INDIAN MARKET",
                "delta": "CRYPTO",
            }.get(connection.provider, connection.provider)
            self._tree.insert(
                "",
                "end",
                iid=connection.connection_id,
                values=(
                    connection.name,
                    provider,
                    self._session.connection_instrument_labels(connection),
                    format_connection_status(status),
                ),
            )
        remaining = [item for item in selected if self._tree.exists(item)]
        if remaining:
            self._tree.selection_set(remaining)

    def refresh_statuses(self) -> None:
        for connection in self._session.list_connections():
            if not self._tree.exists(connection.connection_id):
                self.refresh()
                return
            status = self._session.get_connection_status(connection.connection_id)
            self._tree.set(
                connection.connection_id,
                "status",
                format_connection_status(status),
            )
        listed = set(self._tree.get_children())
        actual = {
            connection.connection_id
            for connection in self._session.list_connections()
        }
        if listed != actual:
            self.refresh()

    def selected_connection_id(self) -> str | None:
        selected = self._tree.selection()
        if not selected:
            return None
        return selected[0]

    def open_create(self) -> None:
        saved = self._session.list_saved_instruments()
        if not saved:
            self._set_status(
                "No saved instruments. Search and save one first."
            )
            return
        parent = self._frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("Create Connection")
        style_window(dialog)
        attach_dialog_header(dialog, "Create Connection")

        ttk.Label(dialog, text="Connection name:").pack(anchor="w", padx=12, pady=(16, 0))
        name_entry = ttk.Entry(dialog)
        name_entry.pack(fill="x", padx=12, pady=(0, 8))

        ttk.Label(dialog, text="Saved instruments:").pack(anchor="w", padx=12, pady=(8, 0))
        listbox = tk.Listbox(dialog, selectmode="extended", height=8, exportselection=False)
        style_listbox(listbox)
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        for instrument in saved:
            listbox.insert(
                "end",
                instrument.name or instrument.trading_symbol,
            )

        credential_ready = self._session.has_upstox_credential()
        token_entry: ttk.Entry | None = None
        upstox_credentials = ttk.Frame(dialog)
        if not credential_ready:
            ttk.Label(upstox_credentials, text="API Key:").pack(
                anchor="w"
            )
            token_entry = ttk.Entry(upstox_credentials, show="*")
            token_entry.pack(fill="x", pady=(0, 8))
        delta_credentials = ttk.Frame(dialog)
        ttk.Label(delta_credentials, text="API Key (optional):").pack(
            anchor="w"
        )
        api_key_entry = ttk.Entry(delta_credentials, show="*")
        api_key_entry.pack(fill="x", pady=(0, 6))
        ttk.Label(delta_credentials, text="API Secret (optional):").pack(
            anchor="w"
        )
        api_secret_entry = ttk.Entry(delta_credentials, show="*")
        api_secret_entry.pack(fill="x", pady=(0, 8))

        def selected_providers() -> set[str]:
            return {
                saved[index].provider for index in listbox.curselection()
            }

        def refresh_credentials(_event: object = None) -> None:
            providers = selected_providers()
            if "delta" in providers:
                delta_credentials.pack(fill="x", padx=12, pady=(0, 8))
            else:
                delta_credentials.pack_forget()
            if "upstox" in providers and not credential_ready:
                upstox_credentials.pack(fill="x", padx=12, pady=(0, 8))
            else:
                upstox_credentials.pack_forget()

        listbox.bind("<<ListboxSelect>>", refresh_credentials)

        dialog_status = ttk.Label(dialog, text="")
        dialog_status.pack(anchor="w", padx=12)

        def submit() -> None:
            name = name_entry.get()
            selected = [saved[index] for index in listbox.curselection()]
            if not selected:
                dialog_status.configure(text="Select saved instruments.")
                return
            keys = [instrument.instrument_key for instrument in selected]
            token = token_entry.get() if token_entry is not None else ""
            try:
                self._session.create_connections_for_instruments(
                    name,
                    keys,
                    upstox_credential=token,
                    delta_api_key=api_key_entry.get(),
                    delta_api_secret=api_secret_entry.get(),
                )
            except ValueError as error:
                dialog_status.configure(text=str(error))
                return
            if token_entry is not None:
                token_entry.delete(0, "end")
            api_key_entry.delete(0, "end")
            api_secret_entry.delete(0, "end")
            dialog.destroy()
            self._set_status("Creating connection...")

        buttons = tk.Frame(dialog, bg=dialog.cget("bg"))
        buttons.pack(fill="x", padx=12, pady=12)
        TactileButton(buttons, "Create", submit, variant="primary").pack(
            side="left", padx=(0, 8)
        )
        TactileButton(buttons, "Cancel", dialog.destroy).pack(side="left")
        place_modal_dialog(dialog, parent)
        name_entry.focus_set()
        self._dialog = dialog

    def connect_selected(self) -> None:
        connection_id = self.selected_connection_id()
        if connection_id is None:
            self._set_status("Select a connection.")
            return
        self._session.connect_connection(connection_id)
        self._set_status("Connecting...")
        self.refresh_statuses()

    def disconnect_selected(self) -> None:
        connection_id = self.selected_connection_id()
        if connection_id is None:
            self._set_status("Select a connection.")
            return
        self._session.disconnect_connection(connection_id)
        self._set_status("Disconnecting...")

    def delete_selected(self) -> None:
        connection_id = self.selected_connection_id()
        if connection_id is None:
            self._set_status("Select a connection.")
            return
        if not self._confirm(
            "Delete connection?",
            "Delete this connection and its saved credential? "
            "Saved instruments will not be deleted.",
        ):
            self._set_status("Delete cancelled.")
            return
        self._session.start_delete_connection(connection_id)
        self._set_status("Deleting connection...")

    def _set_status(self, text: str) -> None:
        self._status.configure(text=text)
