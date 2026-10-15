"""Instruments tab: search, save, and remove via GuiSession."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from data.connection_manager import ConnectionManagerError
from data.credential_store import CredentialStoreError
from gui.dialogs import place_modal_dialog
from gui.session import GuiSession
from gui.theme import FONT_HEADING, NAVY, WORKSPACE, style_window
from gui.widgets import AppCard, TactileButton, attach_dialog_header
from gui.ui_queue import SearchErrorQueued, SearchResultQueued, UiMessage
from models.instrument import Instrument

_RESULT_COLUMNS = (
    ("symbol", "Trading Symbol", 120),
    ("name", "Name", 220),
    ("market", "Market", 110),
    ("exchange", "Exchange", 90),
    ("segment", "Segment", 90),
    ("type", "Instrument Type", 120),
)
_SAVED_COLUMNS = (
    ("symbol", "Trading Symbol", 120),
    ("name", "Name", 220),
    ("exchange", "Exchange", 90),
    ("segment", "Segment", 90),
)


class InstrumentsView:
    """Instrument search and saved-instrument management widgets."""

    def __init__(
        self,
        parent: tk.Widget,
        session: GuiSession,
        *,
        ask_token: Callable[[], str | None] | None = None,
        confirm: Callable[[str, str], bool] | None = None,
        on_saved_instruments_changed: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._ask_token = ask_token or self._prompt_token
        self._confirm = confirm or self._confirm_dialog
        self._on_saved_changed = on_saved_instruments_changed
        self._result_instruments: dict[str, Instrument] = {}
        self._saved_instruments: dict[str, Instrument] = {}
        self._frame = tk.Frame(parent, bg=WORKSPACE)
        heading = tk.Frame(self._frame, bg=WORKSPACE)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading, text="Instruments", bg=WORKSPACE, fg=NAVY, font=FONT_HEADING
        ).pack(side="left")
        card = AppCard(self._frame, title="", padding=10)
        card.pack(fill="both", expand=True)
        self._body = card.body
        self._build()
        self.refresh_saved()

    @property
    def frame(self) -> tk.Frame:
        return self._frame

    def _build(self) -> None:
        search_row = ttk.Frame(self._body)
        search_row.pack(fill="x", pady=(4, 8))
        ttk.Label(search_row, text="Query", style="Meta.TLabel").pack(side="left")
        self._query = ttk.Entry(search_row)
        self._query.pack(side="left", fill="x", expand=True, padx=8)
        self._query.bind("<Return>", lambda _event: self.start_search())
        self._search_button = TactileButton(
            search_row, "Search", self.start_search, variant="primary"
        )
        self._search_button.pack(side="left")
        TactileButton(
            search_row, "API Key", self.open_credentials_dialog
        ).pack(side="left", padx=(8, 0))

        results_box = ttk.LabelFrame(self._body, text="SEARCH RESULTS")
        results_box.pack(fill="both", expand=True, pady=(4, 6))
        self._results = self._make_tree(results_box, _RESULT_COLUMNS, height=8)
        self._results.pack(fill="both", expand=True, padx=6, pady=6)
        TactileButton(
            results_box, "Save selected", self.save_selected, variant="primary"
        ).pack(anchor="w", padx=6, pady=(0, 8))

        saved_box = ttk.LabelFrame(self._body, text="SAVED INSTRUMENTS")
        saved_box.pack(fill="both", expand=True, pady=(4, 6))
        self._saved = self._make_tree(saved_box, _SAVED_COLUMNS, height=8)
        for name in ("symbol", "name", "exchange", "segment"):
            anchor = "w" if name in ("symbol", "name") else "center"
            self._saved.heading(name, anchor=anchor)
            self._saved.column(name, anchor=anchor)
        self._saved.pack(fill="both", expand=True, padx=6, pady=6)
        TactileButton(
            saved_box, "Remove selected", self.remove_selected, variant="danger"
        ).pack(anchor="w", padx=6, pady=(0, 8))

        self._status = ttk.Label(self._body, text="", style="Status.TLabel")
        self._status.pack(anchor="w", pady=(0, 4))

    @staticmethod
    def _make_tree(
        parent: tk.Widget,
        columns: tuple[tuple[str, str, int], ...],
        *,
        height: int,
    ) -> ttk.Treeview:
        names = [column[0] for column in columns]
        tree = ttk.Treeview(
            parent, columns=names, show="headings", height=height, selectmode="browse"
        )
        for name, heading, width in columns:
            tree.heading(name, text=heading)
            tree.column(name, width=width, stretch=True)
        return tree

    def _prompt_token(self) -> str | None:
        value = simpledialog.askstring(
            "API Key",
            "Enter API Key (input hidden):",
            parent=self._frame.winfo_toplevel(),
            show="*",
        )
        if value is None:
            return None
        token = value.strip()
        return token or None

    def _confirm_dialog(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, parent=self._frame.winfo_toplevel())

    def start_search(self) -> None:
        raw_query = self._query.get()
        try:
            self._session.validate_search_query(raw_query)
        except ValueError as error:
            self._set_status(str(error))
            return

        try:
            self._session.start_instrument_search(raw_query)
        except ValueError as error:
            self._set_status(str(error))
            return
        self._set_status("Searching...")

    def update_credential(self) -> None:
        """Open the market-data credential-management dialog."""
        self.open_credentials_dialog()

    def open_credentials_dialog(self) -> None:
        parent = self._frame.winfo_toplevel()
        dialog = tk.Toplevel(parent)
        dialog.title("API Key")
        style_window(dialog)
        attach_dialog_header(dialog, "API Key")
        notebook = ttk.Notebook(dialog)
        indian_tab = ttk.Frame(notebook, padding=12)
        crypto_tab = ttk.Frame(notebook, padding=12)
        forex_tab = ttk.Frame(notebook, padding=12)
        notebook.add(indian_tab, text="INDIAN MARKET")
        notebook.add(crypto_tab, text="CRYPTO")
        notebook.add(forex_tab, text="FOREX")
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        ttk.Label(indian_tab, text="API Key:").pack(anchor="w")
        token_entry = ttk.Entry(indian_tab, show="*", width=40)
        token_entry.pack(fill="x", pady=(0, 8))
        ttk.Label(
            indian_tab,
            text="Connect saved feeds here or manage individual feeds in Connections.",
            wraplength=400,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(crypto_tab, text="API Key (optional):").pack(anchor="w")
        delta_key_entry = ttk.Entry(crypto_tab, show="*", width=40)
        delta_key_entry.pack(fill="x", pady=(0, 8))
        ttk.Label(crypto_tab, text="API Secret (optional):").pack(anchor="w")
        delta_secret_entry = ttk.Entry(crypto_tab, show="*", width=40)
        delta_secret_entry.pack(fill="x", pady=(0, 8))
        ttk.Label(
            crypto_tab,
            text=(
                "CRYPTO public market data does not require credentials. "
                "Connect saved feeds here or manage individual feeds in Connections."
            ),
            wraplength=400,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(forex_tab, text="API Key:").pack(anchor="w")
        forex_key_entry = ttk.Entry(forex_tab, show="*", width=40)
        forex_key_entry.pack(fill="x", pady=(0, 8))
        ttk.Label(forex_tab, text="API Secret:").pack(anchor="w")
        forex_secret_entry = ttk.Entry(forex_tab, show="*", width=40)
        forex_secret_entry.pack(fill="x", pady=(0, 8))

        dialog_status = ttk.Label(dialog, text="")
        dialog_status.pack(anchor="w", padx=12, pady=(8, 0))

        def close_dialog() -> None:
            token_entry.delete(0, "end")
            delta_key_entry.delete(0, "end")
            delta_secret_entry.delete(0, "end")
            forex_key_entry.delete(0, "end")
            forex_secret_entry.delete(0, "end")
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        def submit_update() -> None:
            credential = token_entry.get()
            try:
                self._session.save_upstox_credential(credential)
            except (ValueError, CredentialStoreError, ConnectionManagerError) as error:
                dialog_status.configure(
                    text=str(error) or "Could not save the credential."
                )
                return
            credential = None
            close_dialog()
            self._set_status("INDIAN MARKET API Key saved.")

        def submit_delta_update() -> None:
            api_key = delta_key_entry.get()
            api_secret = delta_secret_entry.get()
            try:
                self._session.save_delta_credentials(api_key, api_secret)
            except (ValueError, CredentialStoreError, ConnectionManagerError) as error:
                dialog_status.configure(
                    text=str(error) or "Could not save the credentials."
                )
                return
            api_key = ""
            api_secret = ""
            close_dialog()
            self._set_status("CRYPTO API credentials saved.")

        def submit_forex_update() -> None:
            api_key = forex_key_entry.get()
            api_secret = forex_secret_entry.get()
            try:
                self._session.save_forex_credentials(api_key, api_secret)
            except (ValueError, CredentialStoreError, ConnectionManagerError) as error:
                dialog_status.configure(
                    text=str(error) or "Could not save the credentials."
                )
                return
            api_key = ""
            api_secret = ""
            close_dialog()
            self._set_status("FOREX API credentials saved.")

        def submit_forex_remove() -> None:
            try:
                self._session.remove_forex_credentials()
            except (CredentialStoreError, ConnectionManagerError) as error:
                dialog_status.configure(
                    text=str(error) or "Could not remove the credentials."
                )
                return
            close_dialog()
            self._set_status("FOREX API credentials removed.")

        def submit_disconnect() -> None:
            if not self._confirm_account_disconnect(parent, "INDIAN MARKET"):
                try:
                    dialog.grab_set()
                except tk.TclError:
                    pass
                self._set_status("INDIAN MARKET disconnect cancelled.")
                return
            self._session.disconnect_upstox_account()
            close_dialog()
            self._set_status("INDIAN MARKET disconnected.")

        def submit_delta_disconnect() -> None:
            if not self._confirm_account_disconnect(parent, "CRYPTO"):
                try:
                    dialog.grab_set()
                except tk.TclError:
                    pass
                self._set_status("CRYPTO disconnect cancelled.")
                return
            self._session.disconnect_delta_account()
            close_dialog()
            self._set_status("CRYPTO disconnected.")

        indian_buttons = tk.Frame(indian_tab, bg=dialog.cget("bg"))
        indian_buttons.pack(fill="x", pady=(8, 0))
        TactileButton(
            indian_buttons,
            "Connect saved feeds",
            lambda: self._connect_saved_connections(
                "upstox", "INDIAN MARKET", dialog_status
            ),
            variant="primary",
        ).pack(side="left", padx=(0, 8))
        TactileButton(indian_buttons, "Save API Key", submit_update, variant="primary").pack(
            side="left", padx=(0, 8)
        )
        TactileButton(indian_buttons, "Disconnect", submit_disconnect, variant="danger").pack(
            side="left", padx=(0, 8)
        )
        crypto_buttons = tk.Frame(crypto_tab, bg=dialog.cget("bg"))
        crypto_buttons.pack(fill="x", pady=(8, 0))
        TactileButton(
            crypto_buttons,
            "Connect saved feeds",
            lambda: self._connect_saved_connections(
                "delta", "CRYPTO", dialog_status
            ),
            variant="primary",
        ).pack(side="left", padx=(0, 8))
        TactileButton(
            crypto_buttons,
            "Save API Credentials",
            submit_delta_update,
            variant="primary",
        ).pack(side="left", padx=(0, 8))
        TactileButton(
            crypto_buttons,
            "Disconnect",
            submit_delta_disconnect,
            variant="danger",
        ).pack(side="left")
        forex_buttons = tk.Frame(forex_tab, bg=dialog.cget("bg"))
        forex_buttons.pack(fill="x", pady=(8, 0))
        TactileButton(
            forex_buttons,
            "Save / Update",
            submit_forex_update,
            variant="primary",
        ).pack(side="left", padx=(0, 8))
        TactileButton(
            forex_buttons,
            "Remove Credentials",
            submit_forex_remove,
            variant="danger",
        ).pack(side="left")
        buttons = tk.Frame(dialog, bg=dialog.cget("bg"))
        buttons.pack(fill="x", padx=12, pady=12)
        TactileButton(buttons, "Close", close_dialog).pack(side="left")
        place_modal_dialog(dialog, parent)
        token_entry.focus_set()
        self._dialog = dialog

    def _connect_saved_connections(
        self, provider: str, market: str, status: ttk.Label
    ) -> None:
        connections = [
            connection
            for connection in self._session.list_connections()
            if connection.provider == provider
        ]
        if not connections:
            status.configure(
                text=f"No saved {market} feeds. Create one in Connections."
            )
            return
        started = sum(
            self._session.connect_connection(connection.connection_id) > 0
            for connection in connections
        )
        status.configure(text=f"Connecting {started} {market} feed(s)...")

    def _confirm_account_disconnect(
        self, parent: tk.Misc, market: str = "INDIAN MARKET"
    ) -> bool:
        if self._confirm is not self._confirm_dialog:
            return self._confirm(
                f"Disconnect {market}?",
                f"This will disconnect all active {market} feeds and remove the "
                "stored API credentials. Saved instruments, connections and "
                "alerts will not be deleted.",
            )
        confirm = tk.Toplevel(parent)
        confirm.title(f"Disconnect {market}?")
        style_window(confirm)
        attach_dialog_header(confirm, f"Disconnect {market}?")
        ttk.Label(
            confirm,
            text=(
                f"This will disconnect all active {market} feeds\n"
                "and remove the stored API credentials.\n\n"
                "Saved instruments, connections and alerts\n"
                "will not be deleted."
            ),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(16, 8))
        decision = {"ok": False}

        def accept() -> None:
            decision["ok"] = True
            confirm.destroy()

        def cancel() -> None:
            confirm.destroy()

        buttons = tk.Frame(confirm, bg=confirm.cget("bg"))
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        TactileButton(buttons, "Disconnect", accept, variant="danger").pack(
            side="left", padx=(0, 8)
        )
        TactileButton(buttons, "Cancel", cancel).pack(side="left")
        place_modal_dialog(confirm, parent)
        parent.wait_window(confirm)
        return decision["ok"]

    def handle_ui_message(self, message: UiMessage) -> None:
        current = self._session.current_search_request_id()
        if isinstance(message, SearchResultQueued):
            if message.request_id != current:
                return
            self._show_results(list(message.results))
            if message.results:
                status = f"{len(message.results)} result(s)."
            else:
                status = "No matching instruments."
            if message.warning:
                status = f"{status} {message.warning}"
            self._set_status(status)
            return
        if isinstance(message, SearchErrorQueued):
            if message.request_id != current:
                return
            text = message.message
            lowered = text.lower()
            if "401" in lowered or "unauthor" in lowered or "auth" in lowered:
                text = (
                    f"{text} Use API Key to replace the "
                    "stored credential."
                )
            self._set_status(text)

    def save_selected(self) -> None:
        selected = self._results.selection()
        if not selected:
            self._set_status("Select a search result to save.")
            return
        instrument = self._result_instruments.get(selected[0])
        if instrument is None:
            self._set_status("Select a search result to save.")
            return
        saved = self._session.save_instrument(instrument)
        self.refresh_saved()
        self._notify_saved_changed()
        if saved is instrument:
            self._set_status(f"Saved {saved.trading_symbol}.")
        else:
            self._set_status(f"Already saved {saved.trading_symbol}.")

    def remove_selected(self) -> None:
        selected = self._saved.selection()
        if not selected:
            self._set_status("Select a saved instrument to remove.")
            return
        instrument = self._saved_instruments.get(selected[0])
        if instrument is None:
            self._set_status("Select a saved instrument to remove.")
            return
        warning = self._session.instrument_removal_warning(instrument.instrument_key)
        if warning is not None:
            if not self._confirm("Remove saved instrument?", warning):
                self._set_status("Removal cancelled.")
                return
        self._session.remove_instrument(instrument.instrument_key)
        self.refresh_saved()
        self._notify_saved_changed()
        self._set_status(f"Removed {instrument.trading_symbol}.")

    def refresh_saved(self) -> None:
        self._saved_instruments = {
            instrument.instrument_key: instrument
            for instrument in self._session.list_saved_instruments()
        }
        self._fill_tree(
            self._saved,
            list(self._saved_instruments.values()),
            include_type=False,
        )

    def _show_results(self, results: list[Instrument]) -> None:
        self._result_instruments = {
            instrument.instrument_key: instrument for instrument in results
        }
        self._fill_tree(self._results, results, include_type=True)

    def _fill_tree(
        self,
        tree: ttk.Treeview,
        instruments: list[Instrument],
        *,
        include_type: bool,
    ) -> None:
        tree.delete(*tree.get_children())
        for instrument in instruments:
            values = [
                instrument.trading_symbol,
                instrument.name,
            ]
            if include_type:
                values.append(self._market_category(instrument))
            values.extend((instrument.exchange, instrument.segment))
            if include_type:
                values.append(instrument.instrument_type)
            tree.insert("", "end", iid=instrument.instrument_key, values=values)

    @staticmethod
    def _market_category(instrument: Instrument) -> str:
        if instrument.provider == "delta":
            return "CRYPTO"
        if instrument.provider == "forex":
            return "FOREX"
        return "INDIAN MARKET"

    def _notify_saved_changed(self) -> None:
        if self._on_saved_changed is not None:
            self._on_saved_changed()

    def _set_status(self, text: str) -> None:
        self._status.configure(text=text)
