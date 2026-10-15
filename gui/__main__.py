"""Entry point for `python -m gui`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.app import main
from gui.windows_startup import set_windows_autostart

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Alert Engine desktop app.")
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument(
        "--enable-autostart",
        action="store_true",
        help="Start the app hidden in the tray when this Windows user logs in.",
    )
    startup.add_argument(
        "--disable-autostart",
        action="store_true",
        help="Remove the app from this Windows user's login startup.",
    )
    parser.add_argument(
        "--start-hidden",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.enable_autostart or args.disable_autostart:
        set_windows_autostart(args.enable_autostart)
        setting = "enabled" if args.enable_autostart else "disabled"
        print(f"Market Alert Engine autostart {setting}.")
    else:
        main(start_hidden=args.start_hidden)
