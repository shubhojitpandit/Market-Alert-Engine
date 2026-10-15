"""Custom presentation components for the Market Alert Engine desktop shell."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from gui.theme import (
    DANGER,
    HEADER_BOT,
    HEADER_TOP,
    LIVE,
    NAV_FACE,
    NAVY,
    ORANGE,
    ORANGE_HOT,
    PANEL,
    SHADOW,
    SKY,
    TEXT,
    TEXT_MUTED,
    WHITE,
    FONT_BADGE,
    FONT_NAV,
    FONT_SECTION,
    FONT_TITLE,
    FONT_UI,
    FONT_UI_BOLD,
)


def rounded_rect(
    canvas: tk.Canvas,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    radius: int,
    **kwargs,
) -> int:
    """Draw a rounded rectangle as a smoothed polygon."""
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _icon_watch(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    canvas.create_line(
        x, y + size - 2,
        x + 4, y + size // 2,
        x + 8, y + size - 5,
        x + size, y + 2,
        fill=color, width=2, capstyle="round", joinstyle="round",
    )
    canvas.create_oval(x + size - 3, y, x + size + 1, y + 4, fill=color, outline=color)


def _icon_instruments(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    canvas.create_rectangle(x, y, x + size, y + size, outline=color, width=2)
    canvas.create_line(x + 3, y + 4, x + size - 3, y + 4, fill=color, width=2)
    canvas.create_line(x + 3, y + 8, x + size - 3, y + 8, fill=color, width=2)
    canvas.create_line(x + 3, y + 12, x + size - 5, y + 12, fill=color, width=2)


def _icon_connections(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    canvas.create_oval(x, y + 3, x + 6, y + 9, outline=color, width=2)
    canvas.create_oval(x + size - 6, y + 5, x + size, y + 11, outline=color, width=2)
    canvas.create_line(x + 6, y + 6, x + size - 6, y + 8, fill=color, width=2)


def _icon_alerts(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    canvas.create_polygon(
        x + size // 2, y,
        x + size, y + size - 3,
        x, y + size - 3,
        outline=color, fill="", width=2,
    )
    canvas.create_rectangle(
        x + size // 2 - 1, y + size - 2, x + size // 2 + 1, y + size, fill=color, outline=color
    )


def _icon_history(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    canvas.create_oval(x, y, x + size, y + size, outline=color, width=2)
    canvas.create_line(x + size // 2, y + 3, x + size // 2, y + size // 2, fill=color, width=2)
    canvas.create_line(x + size // 2, y + size // 2, x + size - 3, y + size // 2 + 2, fill=color, width=2)


def _icon_feed(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 16) -> None:
    canvas.create_line(x, y + size - 2, x + 5, y + 6, x + 9, y + 11, x + size, y + 2, fill=color, width=2)
    canvas.create_oval(x + size - 3, y, x + size + 1, y + 4, fill=color, outline=color)


def _icon_count(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 16) -> None:
    canvas.create_rectangle(x, y + 4, x + 10, y + size, outline=color, width=2)
    canvas.create_rectangle(x + 6, y, x + size, y + size - 4, outline=color, width=2)


ICONS: dict[str, Callable[..., None]] = {
    "watch": _icon_watch,
    "instruments": _icon_instruments,
    "connections": _icon_connections,
    "alerts": _icon_alerts,
    "history": _icon_history,
    "feed": _icon_feed,
    "count": _icon_count,
}


class TactileButton(tk.Frame):
    """Raised desktop button painted on a Canvas."""

    mae_button = True

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None] | None = None,
        *,
        variant: str = "secondary",
        width: int = 0,
        height: int = 32,
    ) -> None:
        super().__init__(parent, bg=WHITE)
        try:
            parent_bg = parent.cget("bg")
            if parent_bg:
                super().configure(bg=parent_bg)
        except tk.TclError:
            pass
        self._text = text
        self._command = command
        self._variant = variant
        self._disabled = False
        self._hover = False
        self._pressed = False
        self._height = height
        self._min_width = width
        self.canvas = tk.Canvas(self, height=height, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self._redraw()

    def cget(self, key: str):  # type: ignore[override]
        if key == "text":
            return self._text
        return super().cget(key)

    def configure(self, cnf=None, **kw):  # type: ignore[override]
        if cnf is None:
            cnf = {}
        merged = {**cnf, **kw}
        if "text" in merged:
            self._text = str(merged.pop("text"))
        if "command" in merged:
            self._command = merged.pop("command")
        if merged:
            super().configure(**merged)
        self._redraw()

    config = configure

    def invoke(self) -> None:
        if not self._disabled and self._command is not None:
            self._command()

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event) -> None:
        if self._disabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _event) -> None:
        if self._disabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        if was_pressed and self._command is not None:
            self._command()

    def _palette(self) -> tuple[str, str, str, str]:
        if self._variant == "primary":
            fill = ORANGE_HOT if self._hover else ORANGE
            if self._pressed:
                fill = "#e6a35d"
            return fill, NAVY, "#fff4e5", "#cf8a43"
        if self._variant == "danger":
            fill = "#f7e4e1" if self._hover else WHITE
            if self._pressed:
                fill = "#f0d0cb"
            return fill, DANGER, "#ffffff", "#c48a84"
        fill = "#fff7ed" if self._hover else WHITE
        if self._pressed:
            fill = "#f8ead9"
        return fill, NAVY, "#ffffff", "#dfc29f"

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        text_w = max(len(self._text) * 8, 48)
        width = max(self._min_width or (text_w + 28), int(canvas.winfo_width() or 0), text_w + 28)
        height = self._height
        try:
            face = super().cget("bg")
        except tk.TclError:
            face = WHITE
        canvas.configure(width=width, height=height, bg=face)
        fill, fg, light, dark = self._palette()
        rounded_rect(canvas, 1, 2, width - 1, height - 1, 8, fill=SHADOW, outline="")
        rounded_rect(canvas, 1, 1, width - 2, height - 2, 8, fill=fill, outline=dark)
        canvas.create_line(8, 3, width - 8, 3, fill=light)
        canvas.create_text(width // 2, height // 2, text=self._text, fill=fg, font=FONT_UI_BOLD)


class NavButton(tk.Frame):
    """Desktop toolbar module control with a Canvas-drawn icon."""

    mae_button = True

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        icon: str,
        width: int = 128,
        height: int = 40,
    ) -> None:
        super().__init__(parent, bg=NAV_FACE)
        self._text = text
        self._command = command
        self._icon = icon
        self._active = False
        self._hover = False
        self._pressed = False
        self._width = width
        self._height = height
        self.canvas = tk.Canvas(
            self, width=width, height=height, highlightthickness=0, bd=0, bg=NAV_FACE, cursor="hand2"
        )
        self.canvas.pack()
        self.canvas.bind("<Enter>", lambda _e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(False))
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def cget(self, key: str):  # type: ignore[override]
        if key == "text":
            return self._text
        return super().cget(key)

    def invoke(self) -> None:
        self._command()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._redraw()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        if not hover:
            self._pressed = False
        self._redraw()

    def _on_press(self, _event) -> None:
        self._pressed = True
        self._redraw()

    def _on_release(self, _event) -> None:
        self._pressed = False
        self._redraw()
        self._command()

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        w, h = self._width, self._height
        if self._active:
            fill, fg, outline = "#ffe4bd", NAVY, ORANGE
        elif self._pressed:
            fill, fg, outline = "#f8ead9", NAVY, "#dfc29f"
        elif self._hover:
            fill, fg, outline = WHITE, NAVY, ORANGE
        else:
            fill, fg, outline = NAV_FACE, NAVY, "#e9d2b7"
        if self._active or self._hover or self._pressed:
            rounded_rect(canvas, 2, 4, w - 2, h - 2, 6, fill=SHADOW, outline="")
        rounded_rect(canvas, 1, 2, w - 3, h - 3, 6, fill=fill, outline=outline)
        if self._active:
            rounded_rect(
                canvas,
                2,
                8,
                6,
                h - 8,
                2,
                fill=ORANGE,
                outline=ORANGE,
            )
        painter = ICONS.get(self._icon)
        if painter is not None:
            painter(canvas, 13, h // 2 - 7, fg, 14)
        canvas.create_text(35, h // 2, text=self._text, fill=fg, font=FONT_NAV, anchor="w")


class StatusBadge(tk.Canvas):
    """Pill badge for LIVE / connected states."""

    def __init__(self, parent: tk.Misc, text: str = "IDLE", *, kind: str = "idle") -> None:
        super().__init__(parent, width=92, height=28, highlightthickness=0, bd=0)
        self._text = text
        self._kind = kind
        try:
            self.configure(bg=parent.cget("bg"))
        except tk.TclError:
            self.configure(bg=SKY)
        self._redraw()

    def set_state(self, text: str, kind: str) -> None:
        self._text = text
        self._kind = kind
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        if self._kind == "live":
            fill, fg = LIVE, WHITE
        elif self._kind == "warn":
            fill, fg = ORANGE, WHITE
        elif self._kind == "error":
            fill, fg = DANGER, WHITE
        else:
            fill, fg = "#e7ddd1", NAVY
        rounded_rect(self, 2, 3, 90, 26, 10, fill=fill, outline="")
        self.create_oval(12, 11, 18, 17, fill=WHITE, outline=WHITE)
        self.create_text(52, 14, text=self._text, fill=fg, font=FONT_BADGE)


class AppCard(tk.Frame):
    """White rounded content panel with optional title."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str = "",
        *,
        padding: int = 10,
    ) -> None:
        super().__init__(parent, bg=SKY)
        try:
            self.configure(bg=parent.cget("bg"))
        except tk.TclError:
            pass
        self._title = title
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self._canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.header = tk.Frame(self, bg=WHITE)
        if title:
            tk.Label(
                self.header,
                text=title,
                bg=WHITE,
                fg=NAVY,
                font=FONT_SECTION,
                anchor="w",
            ).pack(side="left", padx=4)
            self.header.pack(fill="x", padx=14, pady=(12, 0))
        self.body = tk.Frame(self, bg=WHITE)
        self.body.pack(
            fill="both",
            expand=True,
            padx=padding,
            pady=(8 if title else padding, padding),
        )
        self.bind("<Configure>", self._redraw)
        self._redraw()

    def _redraw(self, _event: object = None) -> None:
        canvas = self._canvas
        try:
            canvas.configure(bg=self.master.cget("bg"))
        except tk.TclError:
            canvas.configure(bg=SKY)
        canvas.delete("all")
        w = max(int(self.winfo_width()), 40)
        h = max(int(self.winfo_height()), 40)
        rounded_rect(canvas, 3, 5, w - 2, h - 2, 9, fill=SHADOW, outline="")
        rounded_rect(canvas, 1, 1, w - 4, h - 5, 9, fill=WHITE, outline="#ead2b5")
        canvas.create_line(16, 3, w - 16, 3, fill="#ffffff")


class StatusCard(AppCard):
    """Compact information card used under Market Watch."""

    def __init__(self, parent: tk.Misc, title: str, *, icon: str) -> None:
        super().__init__(parent, title="", padding=12)
        row = tk.Frame(self.body, bg=WHITE)
        row.pack(fill="both", expand=True)
        icon_canvas = tk.Canvas(row, width=28, height=28, bg=WHITE, highlightthickness=0)
        icon_canvas.pack(side="left", padx=(0, 10))
        painter = ICONS.get(icon)
        if painter is not None:
            painter(icon_canvas, 4, 6, ORANGE, 16)
        text = tk.Frame(row, bg=WHITE)
        text.pack(side="left", fill="both", expand=True)
        tk.Label(text, text=title, bg=WHITE, fg=NAVY, font=FONT_SECTION, anchor="w").pack(fill="x")
        self.value = tk.Label(text, text="", bg=WHITE, fg=TEXT, font=FONT_UI, anchor="w", justify="left")
        self.value.pack(fill="x")


class HeaderBar(tk.Frame):
    """Blue desktop title strip with icon and native-style window controls."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_minimize: Callable[[], None],
        on_toggle_maximize: Callable[[], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(parent, height=44)
        self.pack_propagate(False)
        self._window_actions = (on_minimize, on_toggle_maximize, on_close)
        self.canvas = tk.Canvas(self, height=44, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<ButtonRelease-1>", self._on_click)

    def _redraw(self, _event: object = None) -> None:
        canvas = self.canvas
        canvas.delete("all")
        w = max(int(canvas.winfo_width()), 200)
        h = 44
        steps = h
        for index in range(steps):
            ratio = index / max(steps - 1, 1)
            color = _mix(HEADER_TOP, HEADER_BOT, ratio)
            canvas.create_line(0, index, w, index, fill=color)
        canvas.create_rectangle(0, 0, 5, h, fill=ORANGE, outline=ORANGE)
        canvas.create_rectangle(12, 10, 36, 34, fill="#d9822b", outline="#fff0df", width=2)
        canvas.create_line(15, 29, 20, 20, 24, 25, 33, 14, fill=WHITE, width=2)
        canvas.create_text(46, 22, text="Market Alert Engine", fill=WHITE, font=FONT_TITLE, anchor="w")
        for index, action in enumerate(("minimize", "maximize", "close")):
            left = w - 82 + index * 25
            right = left + 22
            fill = "#fff8ef" if action != "close" else "#f4d6d3"
            outline = "#bd7830" if action != "close" else "#9e352e"
            canvas.create_rectangle(
                left, 9, right, 33, fill=fill, outline=outline, width=1
            )
            center_x = (left + right) // 2
            if action == "minimize":
                canvas.create_line(center_x - 4, 25, center_x + 4, 25, fill=NAVY, width=2)
            elif action == "maximize":
                canvas.create_rectangle(
                    center_x - 4, 14, center_x + 4, 23, outline=NAVY, width=1
                )
            else:
                canvas.create_line(
                    center_x - 4, 15, center_x + 4, 23, fill="#8c2420", width=2
                )
                canvas.create_line(
                    center_x + 4, 15, center_x - 4, 23, fill="#8c2420", width=2
                )
        canvas.create_line(0, h - 1, w, h - 1, fill="#bd7830")

    def _on_click(self, event: tk.Event[tk.Canvas]) -> None:
        width = int(self.canvas.winfo_width())
        first_control = width - 82
        index = (event.x - first_control) // 25
        if event.y < 8 or event.y > 34 or not 0 <= index < len(self._window_actions):
            return
        control_left = first_control + index * 25
        if control_left <= event.x <= control_left + 22:
            self._window_actions[index]()


def attach_dialog_header(dialog: tk.Misc, title: str) -> tk.Canvas:
    """Blue structural header for modal dialogs."""
    canvas = tk.Canvas(dialog, height=40, highlightthickness=0, bd=0)
    canvas.pack(fill="x")

    def paint(_event: object = None) -> None:
        canvas.delete("all")
        w = max(int(canvas.winfo_width()), 120)
        for index in range(40):
            canvas.create_line(0, index, w, index, fill=_mix(HEADER_TOP, HEADER_BOT, index / 39))
        canvas.create_rectangle(0, 0, 4, 40, fill=ORANGE, outline=ORANGE)
        canvas.create_text(16, 20, text=title, fill=WHITE, font=FONT_SECTION, anchor="w")

    canvas.bind("<Configure>", paint)
    paint()
    return canvas


def _mix(start: str, end: str, ratio: float) -> str:
    def parts(color: str) -> tuple[int, int, int]:
        value = color.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    r1, g1, b1 = parts(start)
    r2, g2, b2 = parts(end)
    red = int(r1 + (r2 - r1) * ratio)
    green = int(g1 + (g2 - g1) * ratio)
    blue = int(b1 + (b2 - b1) * ratio)
    return f"#{red:02x}{green:02x}{blue:02x}"
