"""Entry point: ``python -m kanjire``."""
from __future__ import annotations

import sys

from kanjire.data import db
from kanjire.paths import DB_PATH


def _check_database() -> bool:
    if not DB_PATH.exists():
        print("No vocabulary database found.")
        print("Build it first with:\n    python scripts/build_jlpt_dataset.py")
        return False
    try:
        con = db.connect(read_only=True)
        try:
            decks = db.list_decks(con)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not open database: {exc}")
        return False
    if not decks:
        print("The database is empty. Run:\n    python scripts/build_jlpt_dataset.py")
        return False
    return True


def main() -> int:
    # A frozen GUI app has no console, so record any crash to a file the player
    # can share (see kanjire.crashlog). Installed before anything can fail.
    from kanjire import crashlog
    crashlog.install()

    if not _check_database():
        return 1
    # Import late so a missing DB gives a clean message before pyglet loads.
    from kanjire.ui.app import GameApp

    try:
        GameApp().run()
    except BaseException as exc:  # noqa: BLE001 - record, then re-raise
        if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            crashlog.record(type(exc), exc, exc.__traceback__)
            print(f"KanjiRe crashed; details written to {crashlog.crash_log_path()}",
                  file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
