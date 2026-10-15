"""Tkinter instrument-console shell for the Market Alert Engine."""

from __future__ import annotations

import sys
from queue import Empty, SimpleQueue
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from data.connection_manager import ConnectionManagerError
from gui.alerts_view import AlertsView
from gui.connections_view import ConnectionsView
from gui.history_view import HistoryView
from gui.instruments_view import InstrumentsView
from gui.market_watch import MarketWatchView
from gui.session import GuiSession
from gui.theme import (
    LIVE,
    MODULES,
    NAV_FACE,
    NAVY,
    ORANGE,
    SURFACE,
    TEXT_MUTED,
    WHITE,
    WORKSPACE,
    apply_theme,
    style_window,
    system_status,
)
from gui.ui_queue import PriceQueued
from gui.widgets import HeaderBar, NavButton, TactileButton, rounded_rect
from gui.windows_tray import WindowsTrayIcon
from gui.windows_startup import is_windows_autostart_enabled, set_windows_autostart

_QUEUE_POLL_MS = 100


class MarketAlertApp:
    """Tk shell that owns one GuiSession and drains its UI queue on the UI thread."""

    def __init__(
        self,
        session: GuiSession | None = None,
        *,
        start_hidden: bool = False,
    ) -> None:
        self.session = session or GuiSession()
        self._running = True
        self._shutting_down = False
        self._after_id: str | None = None
        self._tray_actions: SimpleQueue[str] = SimpleQueue()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.title("Market Alert Engine")
        self.root.geometry("1160x720")
        self.root.minsize(960, 600)
        self._window_interaction: tuple[str, int, int, int, int, int, int] | None = None
        apply_theme(self.root)
        style_window(self.root)
        self.root.configure(bg=WORKSPACE)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<ButtonPress-1>", self._start_window_interaction, add="+")
        self.root.bind("<B1-Motion>", self._move_or_resize_window, add="+")
        self.root.bind("<ButtonRelease-1>", self._stop_window_interaction, add="+")
        self._market_watch_view: MarketWatchView | None = None
        self._instruments_view: InstrumentsView | None = None
        self._connections_view: ConnectionsView | None = None
        self._alerts_view: AlertsView | None = None
        self._history_view: HistoryView | None = None
        self._modules: dict[str, tk.Frame] = {}
        self._nav_buttons: dict[str, NavButton] = {}
        self._active_module = "watch"
        self._autostart_enabled = is_windows_autostart_enabled()
        self._autostart_switch: tk.Canvas | None = None
        self._status_scope: tk.Label | None = None
        self._status_value: tk.Label | None = None
        self._status_system: tk.Label | None = None
        self._build_shell()
        self.root.update_idletasks()
        active_module = self._modules[self._active_module]
        module_width = max(frame.winfo_reqwidth() for frame in self._modules.values())
        module_height = max(frame.winfo_reqheight() for frame in self._modules.values())
        minimum_width = max(
            960,
            self.root.winfo_reqwidth()
            + module_width
            - active_module.winfo_reqwidth(),
        )
        minimum_height = max(
            600,
            self.root.winfo_reqheight()
            + module_height
            - active_module.winfo_reqheight(),
        )
        self.root.minsize(minimum_width, minimum_height)
        self._tray: WindowsTrayIcon | None = None
        if sys.platform == "win32":
            self._tray = WindowsTrayIcon(
                lambda: self._tray_actions.put("open"),
                lambda: self._tray_actions.put("exit"),
            )
        if start_hidden and self._tray is not None:
            self.root.withdraw()
        self.session.start_startup_recovery()
        self._schedule_queue_drain()

    @property
    def module_names(self) -> list[str]:
        """Return module titles in navigation order (test-compatible labels)."""
        return [title for _key, _nav, title in MODULES]

    def _build_shell(self) -> None:
        HeaderBar(
            self.root,
            on_minimize=self._minimize_to_tray,
            on_toggle_maximize=self._toggle_maximize,
            on_close=self._on_close,
        ).pack(fill="x")

        statusbar = tk.Frame(self.root, bg=SURFACE, height=30, bd=1, relief="raised")
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)
        tk.Frame(statusbar, bg="#e9c49a", height=1).pack(fill="x")
        self._status_scope = tk.Label(
            statusbar,
            text="SYSTEM",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=("Calibri", 9, "bold"),
        )
        self._status_scope.pack(side="left", padx=(12, 4))
        tk.Label(statusbar, text="•", bg=SURFACE, fg=TEXT_MUTED).pack(side="left")
        self._status_value = tk.Label(
            statusbar,
            text="STANDBY",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=("Calibri", 9, "bold"),
        )
        self._status_value.pack(side="left", padx=4)
        self._status_system = tk.Label(
            statusbar,
            text="SYSTEM  •  RUNNING",
            bg=SURFACE,
            fg=NAVY,
            font=("Calibri", 9, "bold"),
        )
        self._status_system.pack(side="right", padx=12)

        body = tk.Frame(self.root, bg=WORKSPACE)
        body.pack(fill="both", expand=True)
        nav = tk.Frame(body, bg=NAV_FACE, width=190, bd=1, relief="raised")
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        brand = tk.Frame(nav, bg="#fff0df", height=78)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        logo = tk.Canvas(
            brand, width=30, height=30, bg="#fff0df", highlightthickness=0
        )
        logo.pack(side="left", padx=(12, 8), pady=10)
        logo.create_rectangle(2, 3, 27, 27, fill="#e99b48", outline="#bd7830", width=2)
        logo.create_line(5, 22, 11, 14, 16, 19, 24, 8, fill=WHITE, width=2)
        tk.Label(
            brand,
            text="Market Alert\nEngine",
            bg="#fff0df",
            fg=NAVY,
            font=("Calibri", 10, "bold"),
            justify="left",
            anchor="w",
        ).pack(side="left", fill="both", expand=True, pady=8)
        tk.Frame(nav, bg="#e9c49a", height=1).pack(fill="x", padx=8)
        navigation = tk.Frame(nav, bg=NAV_FACE)
        navigation.pack(fill="x", padx=7, pady=(8, 0))
        for key, _nav_label, title in MODULES:
            button = NavButton(
                navigation,
                title,
                lambda module=key: self.show_module(module),
                icon=key,
                width=172,
                height=42,
            )
            button.pack(fill="x", pady=3)
            self._nav_buttons[key] = button
        autostart = tk.Frame(navigation, bg=NAV_FACE)
        autostart.pack(fill="x", padx=13, pady=(9, 0))
        tk.Label(
            autostart,
            text="AUTOSTART",
            bg=NAV_FACE,
            fg=TEXT_MUTED,
            font=("Calibri", 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        self._autostart_switch = tk.Canvas(
            autostart,
            width=68,
            height=25,
            bg=NAV_FACE,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            takefocus=True,
        )
        self._autostart_switch.pack(anchor="w", pady=(3, 0))
        self._autostart_switch.bind("<Button-1>", self._toggle_autostart)
        self._autostart_switch.bind("<Return>", self._toggle_autostart)
        self._autostart_switch.bind("<space>", self._toggle_autostart)
        self._autostart_switch.bind(
            "<Configure>", lambda _event: self._draw_autostart_switch()
        )
        self._draw_autostart_switch()
        tk.Frame(nav, bg="#e9c49a", width=1).pack(side="right", fill="y")

        main = tk.Frame(body, bg=WORKSPACE)
        main.pack(side="left", fill="both", expand=True)
        toolbar = tk.Frame(main, bg=WORKSPACE, height=42)
        toolbar.pack(fill="x", padx=14, pady=(8, 0))
        toolbar.pack_propagate(False)
        tk.Label(
            toolbar,
            text="MARKET DATA WORKSPACE",
            bg=WORKSPACE,
            fg=NAVY,
            font=("Calibri", 9, "bold"),
        ).pack(side="left", padx=4)
        TactileButton(
            toolbar, "Refresh", self._toolbar_refresh, variant="secondary", height=30
        ).pack(side="right", pady=4)

        workspace = tk.Frame(main, bg=WORKSPACE)
        workspace.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self._workspace = workspace

        watch = tk.Frame(workspace, bg=WORKSPACE)
        self._market_watch_view = MarketWatchView(watch, self.session)
        self._market_watch_view.frame.pack(fill="both", expand=True)
        self._modules["watch"] = watch

        instruments = tk.Frame(workspace, bg=WORKSPACE)
        self._instruments_view = InstrumentsView(
            instruments,
            self.session,
            on_saved_instruments_changed=self._refresh_instrument_consumers,
        )
        self._instruments_view.frame.pack(fill="both", expand=True)
        self._modules["instruments"] = instruments

        connections = tk.Frame(workspace, bg=WORKSPACE)
        self._connections_view = ConnectionsView(connections, self.session)
        self._connections_view.frame.pack(fill="both", expand=True)
        self._modules["connections"] = connections

        alerts = tk.Frame(workspace, bg=WORKSPACE)
        self._alerts_view = AlertsView(alerts, self.session)
        self._alerts_view.frame.pack(fill="both", expand=True)
        self._modules["alerts"] = alerts

        history = tk.Frame(workspace, bg=WORKSPACE)
        self._history_view = HistoryView(history, self.session)
        self._history_view.frame.pack(fill="both", expand=True)
        self._modules["history"] = history

        self.show_module("watch")
        self._refresh_system_status()

    def _draw_autostart_switch(self) -> None:
        if self._autostart_switch is None:
            return
        canvas = self._autostart_switch
        canvas.delete("all")
        enabled = self._autostart_enabled
        rounded_rect(
            canvas,
            2,
            4,
            37,
            21,
            8,
            fill=ORANGE if enabled else "#d8c9b8",
            outline="#cf8a43" if enabled else "#c7b39c",
        )
        knob_left = 21 if enabled else 6
        canvas.create_oval(
            knob_left,
            6,
            knob_left + 13,
            19,
            fill=WHITE,
            outline="#e9d2b7",
        )
        canvas.create_text(
            53,
            13,
            text="ON" if enabled else "OFF",
            fill=NAVY if enabled else TEXT_MUTED,
            font=("Calibri", 8, "bold"),
        )

    def _toggle_autostart(self, _event: tk.Event[tk.Misc] | None = None) -> str:
        enabled = not self._autostart_enabled
        try:
            set_windows_autostart(enabled)
        except OSError:
            messagebox.showerror(
                "Autostart",
                "Could not change the Windows startup setting. Please try again.",
                parent=self.root,
            )
            return "break"
        self._autostart_enabled = enabled
        self._draw_autostart_switch()
        return "break"

    def _toolbar_refresh(self) -> None:
        """Refresh currently visible module using existing view refresh methods."""
        if self._active_module == "instruments" and self._instruments_view is not None:
            self._instruments_view.refresh_saved()
        elif self._active_module == "connections" and self._connections_view is not None:
            self._connections_view.refresh()
        elif self._active_module == "alerts" and self._alerts_view is not None:
            self._alerts_view.refresh()
        elif self._active_module == "history" and self._history_view is not None:
            self._history_view.refresh()
        self._refresh_instrument_consumers()
        self._refresh_system_status()

    def show_module(self, module: str) -> None:
        """Show one module workspace and mark its navigation control active."""
        if module not in self._modules:
            return
        self._active_module = module
        for key, frame in self._modules.items():
            if key == module:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for key, button in self._nav_buttons.items():
            button.set_active(key == module)
        self._on_tab_changed()

    def _toggle_maximize(self) -> None:
        try:
            self.root.state(
                "normal" if self.root.state() == "zoomed" else "zoomed"
            )
        except tk.TclError:
            pass

    def _start_window_interaction(self, event: tk.Event[tk.Misc]) -> None:
        if self.root.state() == "zoomed":
            return

        x = self.root.winfo_rootx()
        y = self.root.winfo_rooty()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        pointer_x = event.x_root
        pointer_y = event.y_root
        local_x = pointer_x - x
        local_y = pointer_y - y
        edge_size = 8

        horizontal = "left" if local_x < edge_size else (
            "right" if local_x >= width - edge_size else ""
        )
        vertical = "top" if local_y < edge_size else (
            "bottom" if local_y >= height - edge_size else ""
        )
        mode = vertical + horizontal
        if not mode and 6 <= local_y < 44:
            first_control = width - 82
            on_control = any(
                first_control + index * 25 <= local_x <= first_control + index * 25 + 22
                and 8 <= local_y <= 34
                for index in range(3)
            )
            if not on_control:
                mode = "move"

        if mode:
            self._window_interaction = (
                mode,
                pointer_x,
                pointer_y,
                x,
                y,
                width,
                height,
            )
            if mode != "move":
                self.root.grab_set()

    def _move_or_resize_window(self, event: tk.Event[tk.Misc]) -> None:
        interaction = self._window_interaction
        if interaction is None:
            return

        mode, start_x, start_y, origin_x, origin_y, origin_width, origin_height = interaction
        delta_x = event.x_root - start_x
        delta_y = event.y_root - start_y
        min_width, min_height = self.root.minsize()
        x = origin_x
        y = origin_y
        width = origin_width
        height = origin_height

        if mode == "move":
            x += delta_x
            y += delta_y
        else:
            if "left" in mode:
                width = max(min_width, origin_width - delta_x)
                x = origin_x + origin_width - width
            elif "right" in mode:
                width = max(min_width, origin_width + delta_x)
            if "top" in mode:
                height = max(min_height, origin_height - delta_y)
                y = origin_y + origin_height - height
            elif "bottom" in mode:
                height = max(min_height, origin_height + delta_y)

        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _stop_window_interaction(self, _event: tk.Event[tk.Misc]) -> None:
        if (
            self._window_interaction is not None
            and self._window_interaction[0] != "move"
            and self.root.grab_current() == self.root
        ):
            self.root.grab_release()
        self._window_interaction = None

    def _refresh_instrument_consumers(self) -> None:
        if self._alerts_view is not None:
            self._alerts_view.refresh_instruments()
        if self._connections_view is not None:
            self._connections_view.refresh()

    def _on_tab_changed(self, _event: object = None) -> None:
        if self._instruments_view is not None:
            self._instruments_view.refresh_saved()
        self._refresh_instrument_consumers()
        self._refresh_system_status()

    def _refresh_system_status(self) -> None:
        if self._status_scope is None or self._status_value is None:
            return
        scope, label, style = system_status(self.session)
        marker, _, text = label.partition(" ")
        display = text.strip() or label
        self._status_scope.configure(text=scope)
        color = TEXT_MUTED
        kind = "idle"
        if style == "StatusLive.TLabel":
            color = LIVE
            kind = "live"
        elif style == "StatusWarn.TLabel":
            color = ORANGE
            kind = "warn"
        elif style == "StatusError.TLabel":
            color = "#d23a32"
        self._status_value.configure(text=display, fg=color)
        if self._status_system is not None:
            self._status_system.configure(
                text=f"SYSTEM  •  {'ACTIVE' if kind == 'live' else 'RUNNING'}"
            )

    def _schedule_queue_drain(self) -> None:
        if not self._running:
            return
        try:
            while True:
                try:
                    action = self._tray_actions.get_nowait()
                except Empty:
                    break
                if action == "open":
                    self._show_window()
                elif action == "exit":
                    self._shutdown()
                    return
            for message in self.session.ui_queue.drain():
                try:
                    self._dispatch_ui_message(message)
                except tk.TclError:
                    self._running = False
                    return
                except Exception:
                    continue
            if self._connections_view is not None:
                try:
                    self._connections_view.refresh_statuses()
                except tk.TclError:
                    self._running = False
                    return
                except Exception:
                    pass
            try:
                self._refresh_system_status()
            except Exception:
                pass
        except tk.TclError:
            self._running = False
            return
        except Exception:
            pass
        finally:
            if self._running:
                try:
                    self._after_id = self.root.after(
                        _QUEUE_POLL_MS, self._schedule_queue_drain
                    )
                except tk.TclError:
                    self._running = False

    def _dispatch_ui_message(self, message: object) -> None:
        if isinstance(message, PriceQueued):
            if self._market_watch_view is not None:
                self._market_watch_view.handle_ui_message(message)
            return
        if self._instruments_view is not None:
            self._instruments_view.handle_ui_message(message)
        if self._connections_view is not None:
            self._connections_view.handle_ui_message(message)
        if self._alerts_view is not None:
            self._alerts_view.handle_ui_message(message)
        if self._history_view is not None:
            self._history_view.handle_ui_message(message)

    def _minimize_to_tray(self) -> None:
        if self._tray is not None:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _on_close(self) -> None:
        if self._tray is not None:
            self.root.withdraw()
            return
        self._shutdown()

    def _show_window(self) -> None:
        if not self._running:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._running = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._tray is not None:
            try:
                self._tray.close()
            except OSError as error:
                print(f"Could not remove the system-tray icon: {error}")
            self._tray = None
        try:
            self.session.close(block=False)
        except ConnectionManagerError:
            pass
        except TypeError:
            self.session.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        """Enter the Tk mainloop until the window is closed."""
        self.root.mainloop()


def main(*, start_hidden: bool = False) -> None:
    """Create the shell and start the Tk mainloop."""
    MarketAlertApp(start_hidden=start_hidden).run()
