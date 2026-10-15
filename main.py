"""Interactive terminal interface for the Market Alert Engine."""

import sys
from getpass import getpass
from pathlib import Path

from data.base_feed import PriceFeed, PriceUpdate
from data.connection_manager import (
    Connection,
    ConnectionManager,
    ConnectionManagerError,
)
from data.credential_store import (
    CredentialStore,
    CredentialStoreError,
    WindowsCredentialStore,
)
from data.instrument_store import InstrumentStore, InstrumentStoreError
from data.mt5_feed import DEFAULT_MT5_SYMBOL, MT5FeedError, MT5PriceFeed
from data.simulated_feed import DEFAULT_INSTRUMENTS, SimulatedPriceFeed
from data.upstox_feed import UpstoxPriceFeed
from data.upstox_instrument_resolver import (
    UpstoxInstrumentResolver,
    UpstoxInstrumentSearchError,
)
from engine.alert_engine import AlertEngine
from engine.alert_manager import AlertManager
from history.alert_history import AlertHistory
from models.alert import AlertCondition
from models.instrument import Instrument
from notifications.notification_handler import NotificationHandler
from notifications.notification_queue import NotificationQueue

SIMULATION_UPDATES_PER_INSTRUMENT = 2


def add_alert(
    manager: AlertManager, extra_instruments: set[str] | None = None
) -> None:
    """Collect alert details and register one valid configured-instrument alert."""
    entered_symbol = input("Instrument: ").strip()
    extra_instruments = extra_instruments or set()
    if entered_symbol in extra_instruments:
        symbol = entered_symbol
    elif entered_symbol.upper() in DEFAULT_INSTRUMENTS:
        symbol = entered_symbol.upper()
    elif entered_symbol.upper() == DEFAULT_MT5_SYMBOL.upper():
        symbol = DEFAULT_MT5_SYMBOL
    else:
        print("Error: enter a configured simulated instrument or XAUUSDm.")
        return

    condition_text = input(
        "Condition (ABOVE, BELOW, CROSS_ABOVE, CROSS_BELOW): "
    ).strip().upper()
    try:
        condition = AlertCondition(condition_text)
    except ValueError:
        print("Error: unsupported alert condition.")
        return

    target_text = input("Target price: ").strip()
    try:
        target_price = float(target_text)
        alert = manager.create_alert(symbol, target_price, condition)
    except ValueError as error:
        print(f"Error: invalid target price ({error}).")
        return

    print(
        f"Alert created:\n{alert.alert_id}\n"
        f"{alert.symbol} {alert.condition.value} {alert.target_price:.5f}"
    )


def show_active_alerts(manager: AlertManager) -> None:
    """Display active alerts from the existing manager."""
    alerts = manager.list_active_alerts()
    if not alerts:
        print("No active alerts.")
        return

    print("Active alerts:")
    for alert in alerts:
        print(
            f"{alert.alert_id} | {alert.symbol} | "
            f"{alert.condition.value} | {alert.target_price:.5f} | "
            f"{alert.status.value}"
        )


def cancel_alert(manager: AlertManager) -> None:
    """Cancel an active alert through the existing manager."""
    alert_id = input("Alert ID: ").strip().upper()
    if not alert_id:
        print("Error: alert ID must not be empty.")
        return

    try:
        alert = manager.cancel_alert(alert_id)
    except KeyError:
        print(f"Error: alert {alert_id} was not found.")
    except ValueError as error:
        print(f"Error: {error}.")
    else:
        print(f"Cancelled {alert.alert_id}.")


def show_alert_history(alert_history: AlertHistory) -> None:
    """Display history records in their stored trigger order."""
    records = alert_history.get_all()
    if not records:
        print("Alert history is empty.")
        return

    print("Alert history (trigger order):")
    for record in records:
        print(
            f"{record.alert_id} | {record.symbol} | "
            f"{record.condition.value} | target={record.target_price:.5f} | "
            f"current={record.current_price:.5f} | "
            f"{record.triggered_at.isoformat()}"
        )


def start_simulation(
    feed: SimulatedPriceFeed,
    engine: AlertEngine,
    notification_queue: NotificationQueue,
    notification_handler: NotificationHandler,
) -> None:
    """Forward a bounded run of feed updates through existing components."""
    print("\nStarting simulation...")
    try:
        for instrument in DEFAULT_INSTRUMENTS:
            for update in feed.stream(
                instrument, count=SIMULATION_UPDATES_PER_INSTRUMENT
            ):
                print(f"{update.instrument} {update.price:.5f}")
                triggered = engine.evaluate(update)
                for alert in triggered:
                    print(f"  -> {alert.alert_id} TRIGGERED")

                while not notification_queue.is_empty():
                    notification = notification_queue.process_next(
                        notification_handler
                    )
                    if notification is not None:
                        print(f"Processed {notification.alert.alert_id}")
    except KeyboardInterrupt:
        print("\nSimulation stopped by user.")
    else:
        print("Simulation complete.")

    print(f"Notification queue remaining: {notification_queue.size()}")


def start_live_feed(
    feed: PriceFeed,
    engine: AlertEngine,
    notification_queue: NotificationQueue,
    notification_handler: NotificationHandler,
) -> None:
    """Forward live MT5 updates through the existing engine and queue."""
    print(f"\nStarting live feed for {DEFAULT_MT5_SYMBOL}...")
    try:
        for update in feed.stream(DEFAULT_MT5_SYMBOL):
            print(f"{update.instrument} {update.price:.5f}")
            triggered = engine.evaluate(update)
            for alert in triggered:
                print(f"  -> {alert.alert_id} TRIGGERED")

            while not notification_queue.is_empty():
                notification = notification_queue.process_next(
                    notification_handler
                )
                if notification is not None:
                    print(f"Processed {notification.alert.alert_id}")
    except KeyboardInterrupt:
        print("\nLive feed stopped by user.")
    except MT5FeedError as error:
        print(f"Live feed error: {error}")

    print(f"Notification queue remaining: {notification_queue.size()}")


def _process_live_update(
    update: PriceUpdate,
    engine: AlertEngine,
    notification_queue: NotificationQueue,
    notification_handler: NotificationHandler,
) -> None:
    """Forward one normalized provider update through existing alert components."""
    print(f"{update.instrument} {update.price:.5f}")
    triggered = engine.evaluate(update)
    for alert in triggered:
        print(f"  -> {alert.alert_id} TRIGGERED")

    while not notification_queue.is_empty():
        notification = notification_queue.process_next(notification_handler)
        if notification is not None:
            print(f"Processed {notification.alert.alert_id}")


class _EphemeralCredentialStore(CredentialStore):
    """Hold one in-memory credential for a single instrument search."""

    def __init__(self, credential: str) -> None:
        self._credential = credential

    def save(self, provider: str, credential_id: str, credential: str) -> None:
        raise CredentialStoreError("Ephemeral search credentials cannot be saved")

    def retrieve(self, provider: str, credential_id: str) -> str:
        if not self._credential:
            raise CredentialStoreError("No stored credential was found")
        return self._credential

    def delete(self, provider: str, credential_id: str) -> None:
        self._credential = ""


def select_saved_instruments(
    instrument_store: InstrumentStore,
) -> list[Instrument] | None:
    """List saved instruments and return the user's numbered multi-selection."""
    saved = instrument_store.list_instruments()
    if not saved:
        print(
            "No saved instruments. Use Manage saved instruments to search "
            "and save one first."
        )
        return None
    for index, instrument in enumerate(saved, start=1):
        print(
            f"{index}. {instrument.trading_symbol} | {instrument.name} | "
            f"{instrument.instrument_key} | {instrument.exchange} | "
            f"{instrument.segment} | {instrument.instrument_type}"
        )
    selection = input(
        "Select saved instrument numbers (e.g. 1,2,4): "
    ).strip()
    selected: list[Instrument] = []
    seen: set[int] = set()
    try:
        for part in selection.split(","):
            text = part.strip()
            if not text:
                continue
            index = int(text)
            if index < 1 or index > len(saved):
                raise IndexError
            if index in seen:
                continue
            seen.add(index)
            selected.append(saved[index - 1])
    except (ValueError, IndexError):
        print("Error: choose a listed saved instrument number.")
        return None
    if not selected:
        print("Error: choose a listed saved instrument number.")
        return None
    return selected


def select_saved_instrument(instrument_store: InstrumentStore) -> Instrument | None:
    """Return the first selected saved instrument, if any."""
    selected = select_saved_instruments(instrument_store)
    if not selected:
        return None
    return selected[0]


def manage_upstox_connections(
    connection_manager: ConnectionManager,
    *,
    credential_store: CredentialStore | None = None,
    instrument_store: InstrumentStore | None = None,
) -> None:
    """Manage saved Upstox connections without echoing entered credentials."""
    while True:
        print(
            "\nUPSTOX CONNECTIONS\n"
            "1. List connections\n"
            "2. Create connection\n"
            "3. Connect\n"
            "4. Disconnect\n"
            "5. Delete connection\n"
            "6. Back\n"
            "7. Manage saved instruments"
        )
        choice = input("Choose an option: ").strip()
        if choice == "6":
            return
        if choice == "7":
            if credential_store is None or instrument_store is None:
                print("Error: saved instrument management is unavailable.")
                continue
            manage_saved_upstox_instruments(
                connection_manager, credential_store, instrument_store
            )
            continue
        if choice == "1":
            connections = connection_manager.list_connections()
            if not connections:
                print("No saved Upstox connections.")
            for connection in connections:
                status = connection_manager.get_status(
                    connection.connection_id
                )
                print(
                    f"{connection.connection_id} | {connection.name} | "
                    f"{connection.provider} | "
                    f"{', '.join(connection.subscribed_instruments())} | "
                    f"{status.state}"
                )
                if status.error:
                    print(f"  Status: {status.error}")
            continue
        if choice == "2":
            name = input("Connection name: ").strip()
            if instrument_store is None:
                print("Error: saved instrument management is unavailable.")
                continue
            selected_instruments = select_saved_instruments(instrument_store)
            if selected_instruments is None:
                continue
            selected_keys = [
                instrument.instrument_key for instrument in selected_instruments
            ]
            if not sys.stdin.isatty():
                print(
                    "Error: use an interactive terminal to enter credentials safely."
                )
                continue
            token = getpass("Upstox access token (input hidden): ")
            try:
                connection = connection_manager.create_connection(
                    name=name,
                    provider="upstox",
                    instrument=selected_keys[0],
                    credential=token,
                    instruments=selected_keys,
                )
            except (ConnectionManagerError, ValueError) as error:
                print(f"Error: {error}.")
            else:
                print(
                    f"Saved connection {connection.connection_id} "
                    f"({connection.name}) for "
                    f"{', '.join(selected_keys)}."
                )
            finally:
                del token
            continue
        if choice in {"3", "4", "5"}:
            connection_id = input("Connection ID: ").strip()
            try:
                if choice == "3":
                    listed_connections = connection_manager.list_connections()
                    listed_connection_ids = [
                        connection.connection_id
                        for connection in listed_connections
                    ]
                    print(f"Connect diagnostic input: {connection_id!r}")
                    print(f"Connect diagnostic input length: {len(connection_id)}")
                    for listed_id in listed_connection_ids:
                        print(
                            "Connect diagnostic listed ID: "
                            f"{listed_id!r}; length: {len(listed_id)}"
                        )
                    print(
                        "Connect diagnostic ID is listed: "
                        f"{connection_id in set(listed_connection_ids)}"
                    )
                    print(f"Connect diagnostic working directory: {Path.cwd()}")
                    print(
                        "Connect diagnostic registry path: "
                        f"{connection_manager.default_registry_path().resolve()}"
                    )
                    connection_manager.connect(connection_id)
                    print("Connection started.")
                elif choice == "4":
                    connection_manager.disconnect(connection_id)
                    print("Disconnected. Saved credentials were preserved.")
                else:
                    connection_manager.delete_connection(connection_id)
                    print("Connection and saved credential deleted.")
            except ConnectionManagerError as error:
                print(f"Error: {error}.")
            continue
        print("Error: choose an option from 1 to 7.")


def manage_saved_upstox_instruments(
    connection_manager: ConnectionManager,
    credential_store: CredentialStore,
    instrument_store: InstrumentStore,
) -> None:
    """Search, save, list, and remove Upstox instruments without exposing tokens."""
    while True:
        print(
            "\nSAVED UPSTOX INSTRUMENTS\n"
            "1. Search instruments\n"
            "2. List saved instruments\n"
            "3. Remove saved instrument\n"
            "4. Back"
        )
        choice = input("Choose an option: ").strip()
        if choice == "4":
            return
        if choice == "2":
            saved = instrument_store.list_instruments()
            if not saved:
                print("No saved instruments.")
                continue
            for instrument in saved:
                print(
                    f"{instrument.instrument_key} | {instrument.trading_symbol} | "
                    f"{instrument.name} | {instrument.exchange} | "
                    f"{instrument.segment} | {instrument.instrument_type}"
                )
            continue
        if choice == "3":
            instrument_key = input("Instrument key: ").strip()
            try:
                instrument_store.remove(instrument_key)
            except (InstrumentStoreError, ValueError) as error:
                print(f"Error: {error}.")
            else:
                print("Saved instrument removed.")
            continue
        if choice != "1":
            print("Error: choose an option from 1 to 4.")
            continue

        upstox_connections = [
            connection
            for connection in connection_manager.list_connections()
            if connection.provider == "upstox"
        ]
        query = input("Search query: ").strip()
        search_store: CredentialStore = credential_store
        credential_id = "search"
        ephemeral_token: str | None = None
        if not upstox_connections:
            if not sys.stdin.isatty():
                print(
                    "Error: use an interactive terminal to enter credentials safely."
                )
                continue
            ephemeral_token = getpass(
                "Upstox access token for this search (input hidden): "
            )
            search_store = _EphemeralCredentialStore(ephemeral_token)
        elif len(upstox_connections) == 1:
            credential_id = upstox_connections[0].connection_id
        else:
            credential_id = input("Connection ID for search: ").strip()
        try:
            resolver = UpstoxInstrumentResolver(
                search_store, credential_id
            )
            results = resolver.search(query)
        except (UpstoxInstrumentSearchError, ValueError) as error:
            print(f"Error: {error}.")
            continue
        finally:
            if ephemeral_token is not None:
                del ephemeral_token
        if not results:
            print("No matching instruments.")
            continue
        for index, instrument in enumerate(results, start=1):
            print(
                f"{index}. {instrument.instrument_key} | "
                f"{instrument.trading_symbol} | {instrument.name} | "
                f"{instrument.exchange} | {instrument.segment} | "
                f"{instrument.instrument_type}"
            )
        selection = input("Save result number (0 to skip): ").strip()
        if selection == "0" or selection == "":
            continue
        try:
            selected = results[int(selection) - 1]
        except (ValueError, IndexError):
            print("Error: choose a listed result number.")
            continue
        saved = instrument_store.add(selected)
        if saved is selected:
            print(f"Saved {saved.instrument_key}.")
        else:
            print(f"Already saved {saved.instrument_key}.")


def run_cli() -> None:
    """Create the application components and coordinate menu operations."""
    manager = AlertManager()
    notification_queue = NotificationQueue()
    alert_history = AlertHistory()
    engine = AlertEngine(manager, notification_queue, alert_history)
    notification_handler = NotificationHandler()
    feed = SimulatedPriceFeed(update_interval=0.02, seed=42)
    credential_store = WindowsCredentialStore()
    instrument_store = InstrumentStore()
    connection_manager = ConnectionManager(
        credential_store=credential_store,
        feed_factories={"upstox": UpstoxPriceFeed},
        on_update=lambda update: _process_live_update(
            update, engine, notification_queue, notification_handler
        ),
    )

    actions = {
        "1": lambda: add_alert(
            manager,
            {
                key
                for connection in connection_manager.list_connections()
                for key in connection.subscribed_instruments()
            },
        ),
        "2": lambda: show_active_alerts(manager),
        "3": lambda: cancel_alert(manager),
        "4": lambda: show_alert_history(alert_history),
        "5": lambda: start_simulation(
            feed, engine, notification_queue, notification_handler
        ),
        "7": lambda: start_live_feed(
            MT5PriceFeed(), engine, notification_queue, notification_handler
        ),
        "8": lambda: manage_upstox_connections(
            connection_manager,
            credential_store=credential_store,
            instrument_store=instrument_store,
        ),
    }

    try:
        while True:
            print(
                "\nMARKET ALERT ENGINE\n"
                "1. Add Alert\n"
                "2. View Active Alerts\n"
                "3. Cancel Alert\n"
                "4. View Alert History\n"
                "5. Start Simulation\n"
                "6. Exit\n"
                "7. Start MT5 Live Feed (XAUUSDm)\n"
                "8. Manage Upstox Connections"
            )
            try:
                choice = input("Choose an option: ").strip()
            except EOFError:
                print("\nInput ended. Exiting.")
                return

            if choice == "6":
                print("Goodbye.")
                return
            action = actions.get(choice)
            if action is None:
                print("Error: choose an option from 1 to 8.")
                continue

            try:
                action()
            except EOFError:
                print("\nInput ended. Exiting.")
                return
    finally:
        try:
            connection_manager.close()
        except ConnectionManagerError:
            print(
                "Warning: one or more provider connections did not close cleanly.",
                file=sys.stderr,
            )


def main() -> None:
    run_cli()


if __name__ == "__main__":
    main()
