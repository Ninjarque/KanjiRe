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
    if not _check_database():
        return 1
    # Import late so a missing DB gives a clean message before pyglet loads.
    from kanjire.ui.app import GameApp

    GameApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
