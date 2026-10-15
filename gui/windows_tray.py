"""Windows system-tray icon integration using the native Shell API."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes


_WM_APP = 0x8000
_TRAY_CALLBACK = _WM_APP + 1
_WM_NULL = 0x0000
_WM_LBUTTONUP = 0x0202
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONUP = 0x0205
_NIM_ADD = 0x00000000
_NIM_DELETE = 0x00000002
_NIF_MESSAGE = 0x00000001
_NIF_ICON = 0x00000002
_NIF_TIP = 0x00000004
_IDI_APPLICATION = 32512
_MF_STRING = 0x00000000
_TPM_RETURNCMD = 0x0100
_TPM_NONOTIFY = 0x0080
_OPEN_COMMAND = 1
_EXIT_COMMAND = 2

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WindowsTrayIcon:
    """Own one Shell tray icon and dispatch its Open/Exit actions."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        if os.name != "nt":
            raise OSError("The Windows system tray is available only on Windows.")
        self._on_open = on_open
        self._on_exit = on_exit
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._hwnd: int | None = None
        self._icon_added = False
        self._instance: int | None = None
        self._class_name = f"MarketAlertEngineTray-{os.getpid()}"
        self._window_proc = _WNDPROC(self._window_proc_callback)
        self._register_window()
        self._add_icon()

    def _register_window(self) -> None:
        class WndClass(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        get_module_handle = self._kernel32.GetModuleHandleW
        get_module_handle.argtypes = [wintypes.LPCWSTR]
        get_module_handle.restype = wintypes.HINSTANCE
        instance = get_module_handle(None)
        self._instance = instance
        window_class = WndClass()
        window_class.lpfnWndProc = self._window_proc
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name

        register_class = self._user32.RegisterClassW
        register_class.argtypes = [ctypes.POINTER(WndClass)]
        register_class.restype = wintypes.ATOM
        if not register_class(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())

        create_window = self._user32.CreateWindowExW
        create_window.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        create_window.restype = wintypes.HWND
        self._hwnd = create_window(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.DestroyMenu.argtypes = [wintypes.HMENU]
        self._user32.DestroyMenu.restype = wintypes.BOOL
        self._user32.UnregisterClassW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.HINSTANCE,
        ]
        self._user32.UnregisterClassW.restype = wintypes.BOOL

    def _add_icon(self) -> None:
        load_icon = self._user32.LoadIconW
        load_icon.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        load_icon.restype = wintypes.HICON
        icon_handle = load_icon(
            None,
            ctypes.cast(ctypes.c_void_p(_IDI_APPLICATION), wintypes.LPCWSTR),
        )
        data = _NotifyIconData()
        data.cbSize = ctypes.sizeof(_NotifyIconData)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        data.uCallbackMessage = _TRAY_CALLBACK
        data.hIcon = icon_handle
        data.szTip = "Market Alert Engine"

        notify_icon = self._shell32.Shell_NotifyIconW
        notify_icon.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]
        notify_icon.restype = wintypes.BOOL
        if not notify_icon(_NIM_ADD, ctypes.byref(data)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._icon_added = True

    def _window_proc_callback(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == _TRAY_CALLBACK:
            if lparam in (_WM_LBUTTONUP, _WM_LBUTTONDBLCLK):
                self._on_open()
            elif lparam == _WM_RBUTTONUP:
                self._show_menu()
            return 0
        def_window_proc = self._user32.DefWindowProcW
        def_window_proc.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        def_window_proc.restype = ctypes.c_ssize_t
        return def_window_proc(hwnd, message, wparam, lparam)

    def _show_menu(self) -> None:
        create_menu = self._user32.CreatePopupMenu
        create_menu.restype = wintypes.HMENU
        menu = create_menu()
        if not menu:
            raise ctypes.WinError(ctypes.get_last_error())
        append_menu = self._user32.AppendMenuW
        append_menu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        ]
        append_menu.restype = wintypes.BOOL
        if not append_menu(menu, _MF_STRING, _OPEN_COMMAND, "Open"):
            self._user32.DestroyMenu(menu)
            raise ctypes.WinError(ctypes.get_last_error())
        if not append_menu(menu, _MF_STRING, _EXIT_COMMAND, "Exit"):
            self._user32.DestroyMenu(menu)
            raise ctypes.WinError(ctypes.get_last_error())
        point = _Point()
        get_cursor_pos = self._user32.GetCursorPos
        get_cursor_pos.argtypes = [ctypes.POINTER(_Point)]
        get_cursor_pos.restype = wintypes.BOOL
        if not get_cursor_pos(ctypes.byref(point)):
            self._user32.DestroyMenu(menu)
            raise ctypes.WinError(ctypes.get_last_error())
        set_foreground = self._user32.SetForegroundWindow
        set_foreground.argtypes = [wintypes.HWND]
        set_foreground.restype = wintypes.BOOL
        set_foreground(self._hwnd)
        track_menu = self._user32.TrackPopupMenu
        track_menu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPVOID,
        ]
        track_menu.restype = wintypes.UINT
        command = track_menu(
            menu,
            _TPM_RETURNCMD | _TPM_NONOTIFY,
            point.x,
            point.y,
            0,
            self._hwnd,
            None,
        )
        if command == _OPEN_COMMAND:
            self._on_open()
        elif command == _EXIT_COMMAND:
            self._on_exit()
        post_message = self._user32.PostMessageW
        post_message.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        post_message.restype = wintypes.BOOL
        post_message(self._hwnd, _WM_NULL, 0, 0)
        self._user32.DestroyMenu(menu)

    def close(self) -> None:
        """Remove the icon and destroy its hidden callback window."""
        if self._hwnd is None:
            return
        if self._icon_added:
            data = _NotifyIconData()
            data.cbSize = ctypes.sizeof(_NotifyIconData)
            data.hWnd = self._hwnd
            data.uID = 1
            self._shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(data))
            self._icon_added = False
        self._user32.DestroyWindow(self._hwnd)
        self._hwnd = None
        self._user32.UnregisterClassW(self._class_name, self._instance)
        self._instance = None
