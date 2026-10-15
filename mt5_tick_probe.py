"""One-shot probe for retrieving a live XAUUSDm tick from MetaTrader 5."""

from datetime import datetime, timezone

SYMBOL = "XAUUSDm"


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ModuleNotFoundError as error:
        if error.name != "MetaTrader5":
            raise
        print(
            "The MetaTrader5 Python package is not installed. "
            "Install it with: python -m pip install MetaTrader5"
        )
        return 1

    initialized = False
    try:
        if not mt5.initialize():
            print(f"Could not connect to the MT5 terminal: {mt5.last_error()}")
            return 1
        initialized = True
        print("Connected to the MT5 terminal.")

        if not mt5.symbol_select(SYMBOL, True):
            print(f"Could not select {SYMBOL}: {mt5.last_error()}")
            return 1

        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            print(f"Could not retrieve a tick for {SYMBOL}: {mt5.last_error()}")
            return 1

        timestamp = datetime.fromtimestamp(tick.time, timezone.utc)
        print(f"Latest tick for {SYMBOL}:")
        print(f"  Bid:       {tick.bid}")
        print(f"  Ask:       {tick.ask}")
        print(f"  Last:      {tick.last}")
        print(f"  Timestamp: {timestamp.isoformat()} (UTC)")
        return 0
    finally:
        if initialized:
            mt5.shutdown()
            print("MT5 connection closed.")


if __name__ == "__main__":
    raise SystemExit(main())
