"""Shared Tk dialog placement for modal windows owned by the main app."""

from __future__ import annotations

import tkinter as tk

from gui.theme import style_window


def _center_on_parent(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    parent.update_idletasks()
    dialog.update_idletasks()
    dialog_width = max(dialog.winfo_reqwidth(), dialog.winfo_width())
    dialog_height = max(dialog.winfo_reqheight(), dialog.winfo_height())
    parent_width = max(parent.winfo_width(), 1)
    parent_height = max(parent.winfo_height(), 1)
    x = parent.winfo_rootx() + (parent_width - dialog_width) // 2
    y = parent.winfo_rooty() + (parent_height - dialog_height) // 2
    screen_width = dialog.winfo_screenwidth()
    screen_height = dialog.winfo_screenheight()
    x = max(0, min(x, screen_width - dialog_width))
    y = max(0, min(y, screen_height - dialog_height))
    dialog.geometry(f"+{x}+{y}")


def place_modal_dialog(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    """Center a Toplevel on its parent and make it modal on the UI thread."""
    dialog.transient(parent)
    dialog.resizable(False, False)
    style_window(dialog)
    _center_on_parent(dialog, parent)
    dialog.lift()
    try:
        dialog.wait_visibility()
    except tk.TclError:
        pass
    _center_on_parent(dialog, parent)
    dialog.grab_set()
    dialog.focus_set()
