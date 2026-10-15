"""Account-level Upstox disconnect and centered modal dialogs."""

from __future__ import annotations

import time
import tkinter as tk
from datetime import datetime, timezone

import pytest

from data.base_feed import PriceUpdate
from data.connection_manager import ConnectionManagerError
from gui.ui_queue import SearchResultQueued
from models.alert import AlertCondition
from tests.test_gui_connections import (
    FAKE_TOKEN,
    FakeManagedFeed,
    NIFTY,
    RELIANCE,
    make_session,
    session_owned_strings,
    wait_for_messages,
)
from tests.tk_helpers import (
    isolate_gui_persistent_storage,
    shutdown_app,
    tk_available,
)

TS = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_gui_stores(monkeypatch, tmp_path) -> None:
    isolate_gui_persistent_storage(monkeypatch, tmp_path)


def wait_until(predicate, *, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
    found = [widget]
    for child in widget.winfo_children():
        found.extend(walk_widgets(child))
    return found


def button_by_text(root: tk.Misc, text: str) -> tk.Misc:
    for widget in walk_widgets(root):
        try:
            if getattr(widget, "mae_button", False) and widget.cget("text") == text:
                return widget
        except tk.TclError:
            continue
    raise AssertionError(f"Button {text!r} was not found")


def test_disconnect_upstox_account_keeps_metadata_and_drops_credentials(
    tmp_path, monkeypatch
) -> None:
    class EmptyCryptoResolver:
        def search(self, query: str) -> list:
            del query
            return []

    monkeypatch.setattr(
        "gui.session.DeltaInstrumentResolver", EmptyCryptoResolver
    )
    feed = FakeManagedFeed()
    session = make_session(tmp_path, feeds=[feed])
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    session.save_upstox_credential(FAKE_TOKEN)
    connection = session.create_connection(
        "Watchlist", "upstox", NIFTY.instrument_key, FAKE_TOKEN
    )
    session.connect(connection.connection_id)
    alert = session.create_alert(NIFTY.instrument_key, 22500, AlertCondition.ABOVE)
    session.alert_engine.evaluate(
        PriceUpdate(NIFTY.instrument_key, 22520.45, TS)
    )
    history = session.list_alert_history()
    assert history
    session.disconnect_upstox_account()
    assert wait_until(
        lambda: session.get_connection_status(connection.connection_id).state
        != "connected"
    )
    assert session.has_upstox_credential() is False
    assert session.list_saved_instruments() == [NIFTY, RELIANCE]
    remaining = session.list_connections()
    assert len(remaining) == 1
    assert remaining[0].connection_id == connection.connection_id
    assert session.list_active_alerts() == []
    assert session.list_alert_history() == history
    with pytest.raises((ConnectionManagerError, KeyError, ValueError)):
        session.connect(connection.connection_id)
    assert session.get_connection_status(connection.connection_id).state != (
        "connected"
    )
    request_id = session.start_instrument_search("NIFTY")
    assert request_id > 0
    search_messages = []

    def search_completed() -> bool:
        search_messages.extend(session.ui_queue.drain())
        return any(
            isinstance(message, SearchResultQueued)
            and message.request_id == request_id
            for message in search_messages
        )

    assert wait_until(search_completed)
    search_result = next(
        message
        for message in search_messages
        if isinstance(message, SearchResultQueued)
        and message.request_id == request_id
    )
    assert search_result.warning is not None
    assert "INDIAN MARKET not searched" in search_result.warning
    assert "API Key" in search_result.warning
    assert FAKE_TOKEN not in repr(search_result)
    assert FAKE_TOKEN not in session_owned_strings(session)
    assert all(FAKE_TOKEN not in repr(item) for item in session.ui_queue.drain())
    del alert


def test_disconnect_does_not_delete_active_alerts(tmp_path) -> None:
    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_upstox_credential(FAKE_TOKEN)
    alert = session.create_alert(NIFTY.instrument_key, 23000, AlertCondition.ABOVE)
    session.disconnect_upstox_account()
    assert session.list_active_alerts() == [alert]
    assert session.has_upstox_credential() is False


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_credentials_dialog_update_and_cancel_disconnect(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    app = MarketAlertApp(session)
    try:
        view = app._instruments_view
        assert view is not None
        view.open_credentials_dialog()
        dialog = view._dialog
        assert str(dialog.transient()) == str(app.root)
        assert str(dialog.grab_status()) == "local"
        token_entry = None
        for widget in walk_widgets(dialog):
            if widget.winfo_class() == "TEntry":
                token_entry = widget
                break
        assert token_entry is not None
        token_entry.insert(0, FAKE_TOKEN)
        button_by_text(dialog, "Save API Key").invoke()
        app.root.update_idletasks()
        assert session.has_upstox_credential() is True
        assert FAKE_TOKEN not in view._status.cget("text")
        assert "saved" in view._status.cget("text").lower()

        view._confirm = lambda _title, _message: False
        view.open_credentials_dialog()
        dialog = view._dialog
        button_by_text(dialog, "Disconnect").invoke()
        assert session.has_upstox_credential() is True
        assert "cancelled" in view._status.cget("text").lower()
        button_by_text(dialog, "Close").invoke()
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_credentials_dialog_confirmed_disconnect(tmp_path, monkeypatch) -> None:
    from gui.app import MarketAlertApp

    feed = FakeManagedFeed()
    session = make_session(tmp_path, feeds=[feed])
    session.save_instrument(NIFTY)
    session.save_upstox_credential(FAKE_TOKEN)
    connection = session.create_connection(
        "Watchlist", "upstox", NIFTY.instrument_key, FAKE_TOKEN
    )
    session.connect(connection.connection_id)
    app = MarketAlertApp(session)
    try:
        view = app._instruments_view
        assert view is not None
        view._confirm = lambda _title, _message: True
        view.open_credentials_dialog()
        dialog = view._dialog
        assert "Disconnect" in [
            widget.cget("text")
            for widget in walk_widgets(dialog)
            if getattr(widget, "mae_button", False)
        ]
        button_by_text(dialog, "Disconnect").invoke()
        assert wait_until(
            lambda: session.get_connection_status(connection.connection_id).state
            != "connected"
        )
        assert session.has_upstox_credential() is False
        assert session.list_connections()[0].connection_id == connection.connection_id
        assert session.list_saved_instruments() == [NIFTY]
        assert view._status.cget("text") == "INDIAN MARKET disconnected."
        assert FAKE_TOKEN not in view._status.cget("text")
        class EmptyCryptoResolver:
            def search(self, query: str) -> list:
                del query
                return []

        monkeypatch.setattr(
            "gui.session.DeltaInstrumentResolver", EmptyCryptoResolver
        )
        assert session.has_upstox_credential() is False
        view._query.insert(0, "NIFTY")
        request_id = session.current_search_request_id() + 1
        view.start_search()
        assert session.current_search_request_id() == request_id
        search_messages = []

        def search_completed() -> bool:
            search_messages.extend(session.ui_queue.drain())
            return any(
                isinstance(message, SearchResultQueued)
                and message.request_id == request_id
                for message in search_messages
            )

        assert wait_until(search_completed)
        search_result = next(
            message
            for message in search_messages
            if isinstance(message, SearchResultQueued)
            and message.request_id == request_id
        )
        assert "INDIAN MARKET not searched" in (search_result.warning or "")
        assert "API Key" in (search_result.warning or "")
        assert FAKE_TOKEN not in repr(search_result)
        assert session.has_upstox_credential() is False
    finally:
        shutdown_app(app)


@pytest.mark.skipif(not tk_available(), reason="Tkinter is unavailable")
def test_create_connection_dialog_is_centered_modal_child(tmp_path) -> None:
    from gui.app import MarketAlertApp

    session = make_session(tmp_path)
    session.save_instrument(NIFTY)
    session.save_instrument(RELIANCE)
    app = MarketAlertApp(session)
    try:
        app.root.geometry("960x640+120+90")
        app.root.update_idletasks()
        view = app._connections_view
        assert view is not None
        view.open_create()
        dialog = view._dialog
        dialog.update_idletasks()
        assert str(dialog.transient()) == str(app.root)
        assert str(dialog.grab_status()) == "local"
        parent_x = app.root.winfo_rootx()
        parent_y = app.root.winfo_rooty()
        parent_w = app.root.winfo_width()
        parent_h = app.root.winfo_height()
        dialog_w = dialog.winfo_width()
        dialog_h = dialog.winfo_height()
        expected_x = parent_x + (parent_w - dialog_w) // 2
        expected_y = parent_y + (parent_h - dialog_h) // 2
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        expected_x = max(0, min(expected_x, screen_w - dialog_w))
        expected_y = max(0, min(expected_y, screen_h - dialog_h))
        assert abs(dialog.winfo_rootx() - expected_x) <= 40
        assert abs(dialog.winfo_rooty() - expected_y) <= 40
        screen_center_x = screen_w // 2 - dialog_w // 2
        if abs(parent_x + parent_w // 2 - screen_w // 2) > 80:
            assert abs(dialog.winfo_rootx() - screen_center_x) > 20
        dialog.destroy()
    finally:
        shutdown_app(app)
