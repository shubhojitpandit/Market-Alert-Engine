"""Modern retro desktop visual language for the Market Alert Engine shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from data.connection_manager import ConnectionStatus

SKY = "#f8ead9"
SKY_DEEP = "#e6a35d"
WORKSPACE = "#ffffff"
HEADER_TOP = "#d9822b"
HEADER_BOT = "#e99b48"
NAV_FACE = "#fff4e8"
PANEL = "#ffffff"
PANEL_EDGE = "#e9c49a"
RECESS = "#fff8f0"
SURFACE = "#fff0df"
CONTROL = "#fffaf5"
ORANGE = "#f2b36d"
ORANGE_HOT = "#f6c78d"
ORANGE_DIM = "#d98a3f"
AMBER = "#d48a1c"
AMBER_DIM = "#b47312"
NAVY = "#34302c"
TEXT = "#292622"
TEXT_DIM = "#34302c"
TEXT_MUTED = "#75695e"
LIVE = "#21a85a"
DOWN = "#d23a32"
DANGER = "#d23a32"
SELECT = "#d98a3f"
ACCENT = ORANGE
SHADOW = "#e6c8a5"
WHITE = "#ffffff"
BG = SKY
HEADER = HEADER_BOT
NAV = NAV_FACE
FRAME_FACE = WORKSPACE
RAIL = NAV_FACE
TITLE_TOP = HEADER_TOP
TITLE_BOTTOM = HEADER_BOT
TASK_TOP = NAV_FACE
TASK_BOTTOM = NAV_FACE
TASK_HEADER = HEADER_TOP
TEAL = ORANGE
TEAL_DIM = ORANGE_DIM


def _ui_font(size: int = 9, *, bold: bool = False) -> tuple[str, int, str] | tuple[str, int]:
    family = "Calibri"
    if bold:
        return (family, size, "bold")
    return (family, size)


FONT_TITLE = ("Calibri", 14, "bold")
FONT_SECTION = ("Calibri", 10, "bold")
FONT_UI = ("Calibri", 9)
FONT_UI_BOLD = ("Calibri", 9, "bold")
FONT_META = ("Calibri", 8)
FONT_BADGE = ("Calibri", 8, "bold")
FONT_MONO = ("Calibri", 11)
FONT_MONO_LG = ("Calibri", 12, "bold")
FONT_NAV = ("Calibri", 9, "bold")
FONT_HEADING = ("Calibri", 16, "bold")

MODULES = (
    ("watch", "MARKET WATCH", "Market Watch"),
    ("instruments", "INSTRUMENTS", "Instruments"),
    ("connections", "CONNECTIONS", "Connections"),
    ("alerts", "ALERTS", "Alerts"),
    ("history", "HISTORY", "History"),
)


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Configure ttk widgets that still host real data (tables, entries)."""
    global FONT_TITLE, FONT_SECTION, FONT_UI, FONT_UI_BOLD, FONT_META, FONT_NAV, FONT_HEADING, FONT_BADGE
    FONT_TITLE = _ui_font(14, bold=True)
    FONT_SECTION = _ui_font(10, bold=True)
    FONT_UI = _ui_font(9)
    FONT_UI_BOLD = _ui_font(9, bold=True)
    FONT_META = _ui_font(8)
    FONT_NAV = _ui_font(9, bold=True)
    FONT_HEADING = _ui_font(16, bold=True)
    FONT_BADGE = _ui_font(8, bold=True)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=SKY)
    for font_name in tkfont.names(root):
        tkfont.nametofont(font_name, root=root).configure(family="Calibri")
    style.configure(".", background=PANEL, foreground=TEXT, font=FONT_UI)
    style.configure("TFrame", background=PANEL)
    style.configure("App.TFrame", background=WORKSPACE)
    style.configure("Workspace.TFrame", background=WORKSPACE)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Recess.TFrame", background=RECESS)
    style.configure("TLabel", background=PANEL, foreground=TEXT, font=FONT_UI)
    style.configure("Title.TLabel", background=PANEL, foreground=NAVY, font=FONT_HEADING)
    style.configure("Meta.TLabel", background=PANEL, foreground=TEXT_MUTED, font=FONT_META)
    style.configure("Section.TLabel", background=PANEL, foreground=NAVY, font=FONT_SECTION)
    style.configure("Empty.TLabel", background=RECESS, foreground=TEXT_MUTED, font=FONT_SECTION)
    style.configure("Status.TLabel", background=PANEL, foreground=TEXT_MUTED, font=FONT_META)
    style.configure("StatusLive.TLabel", background=SURFACE, foreground=LIVE, font=FONT_UI_BOLD)
    style.configure("StatusIdle.TLabel", background=SURFACE, foreground=TEXT_MUTED, font=FONT_UI_BOLD)
    style.configure("StatusWarn.TLabel", background=SURFACE, foreground=AMBER_DIM, font=FONT_UI_BOLD)
    style.configure("StatusError.TLabel", background=SURFACE, foreground=DOWN, font=FONT_UI_BOLD)
    style.configure(
        "TEntry",
        fieldbackground=WHITE,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor="#d9b58d",
        lightcolor="#fff8ef",
        darkcolor="#c49a6d",
        padding=5,
        font=FONT_UI,
    )
    style.configure(
        "TCombobox",
        fieldbackground=WHITE,
        background=WHITE,
        foreground=TEXT,
        arrowcolor=NAVY,
        bordercolor="#d9b58d",
        padding=4,
        font=FONT_UI,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", WHITE), ("focus", WHITE)],
        foreground=[("readonly", TEXT)],
    )
    style.configure(
        "Treeview",
        background=WHITE,
        fieldbackground=WHITE,
        foreground=TEXT,
        bordercolor="#ead2b5",
        lightcolor=WHITE,
        darkcolor="#ead2b5",
        rowheight=26,
        font=FONT_UI,
    )
    style.configure(
        "Treeview.Heading",
        background="#fff0df",
        foreground=NAVY,
        font=FONT_SECTION,
        relief="raised",
        bordercolor="#e5c49e",
    )
    style.map(
        "Treeview",
        background=[("selected", SELECT)],
        foreground=[("selected", "#ffffff")],
    )
    style.map("Treeview.Heading", background=[("active", "#ffe4c4")])
    style.configure(
        "Watch.Treeview",
        background=WHITE,
        fieldbackground=WHITE,
        foreground=TEXT,
        rowheight=25,
        font=FONT_MONO,
    )
    style.configure(
        "Watch.Treeview.Heading",
        background="#fff0df",
        foreground=NAVY,
        font=FONT_SECTION,
    )
    style.configure(
        "Vertical.TScrollbar",
        background="#fff0df",
        troughcolor=RECESS,
        bordercolor="#e5c49e",
    )
    return style


def style_window(window: tk.Misc) -> None:
    """Apply the sky desktop face color to a Tk window or dialog."""
    window.configure(bg=WORKSPACE)


def style_listbox(listbox: tk.Listbox) -> None:
    listbox.configure(
        bg=WHITE,
        fg=TEXT,
        selectbackground=SELECT,
        selectforeground=WHITE,
        highlightthickness=1,
        highlightbackground=PANEL_EDGE,
        highlightcolor=ORANGE,
        bd=2,
        relief="sunken",
        font=FONT_UI,
        activestyle="none",
    )


def format_connection_status(status: ConnectionStatus) -> str:
    """Return a compact instrument-style status label."""
    if status.state == "connected":
        return "● CONNECTED"
    if status.state == "connecting":
        return "◌ CONNECTING"
    if status.state == "error":
        detail = (status.error or "ERROR").strip()
        return f"● {detail}" if detail else "● ERROR"
    return "● DISCONNECTED"


def system_status(session: object) -> tuple[str, str, str]:
    """Return (scope, label, style) from live session/connection state."""
    states: list[str] = []
    try:
        connections = session.list_connections()  # type: ignore[attr-defined]
    except Exception:
        connections = []
    for connection in connections:
        try:
            status = session.get_connection_status(connection.connection_id)  # type: ignore[attr-defined]
            states.append(status.state)
        except Exception:
            continue
    if "connected" in states:
        return "MARKET DATA", "● CONNECTED", "StatusLive.TLabel"
    if "connecting" in states:
        return "MARKET DATA", "◌ CONNECTING", "StatusWarn.TLabel"
    if "error" in states:
        return "MARKET DATA", "● ERROR", "StatusError.TLabel"
    if states:
        return "MARKET DATA", "● DISCONNECTED", "StatusIdle.TLabel"
    return "SYSTEM", "● STANDBY", "StatusIdle.TLabel"
