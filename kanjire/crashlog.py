"""Write unhandled crashes to a file, so a player can share it without having
to relaunch from a terminal.

A frozen GUI app has no console: when it dies, the traceback vanishes with it.
This records it to ``<user dir>/crash.log`` (the same folder as the save file
and update.log), newest crash appended, with version / platform / timestamp
context so a report is actionable.
"""
from __future__ import annotations

import platform
import sys
import time
import traceback


def crash_log_path():
    from kanjire.paths import USER_DIR
    return USER_DIR / "crash.log"


def record(exc_type, exc_value, exc_tb) -> None:
    """Append one crash to the log. Never raises."""
    try:
        from kanjire import __version__
    except Exception:  # noqa: BLE001
        __version__ = "?"
    try:
        path = crash_log_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("=" * 70 + "\n")
            fh.write(f"KanjiRe {__version__} crash at "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            fh.write(f"platform: {platform.platform()}  python: "
                     f"{platform.python_version()}\n\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
            fh.write("\n")
    except Exception:  # noqa: BLE001 - logging a crash must never crash
        pass


def install() -> None:
    """Route uncaught exceptions through :func:`record` before the default
    handler. Safe to call more than once."""
    prev = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            record(exc_type, exc_value, exc_tb)
        prev(exc_type, exc_value, exc_tb)

    sys.excepthook = hook
