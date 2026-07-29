"""A tiny trace log for the Reading Room, written next to crash.log.

Exists for one reason: sentences appeared to be counted as read without the
Next button being pressed, and that could not be reproduced off-device. The
only way to settle it is to have the app say what it did, on the machine
where it happens.

Every read that gets logged — and every sentence change that does *not* —
appends one line here with the reason and the caller, so the log answers
"what moved the counter?" directly instead of by guesswork. Cheap enough
to leave on: one short line per sentence, capped at MAX_LINES.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime

MAX_LINES = 400
#: Set KANJIRE_NO_READLOG=1 to silence it entirely.
_ENV_OFF = "KANJIRE_NO_READLOG"


def path():
    from kanjire.paths import user_dir
    return user_dir() / "reading.log"


def _caller(skip: int = 2) -> str:
    """Two frames of the call site, minus this module's own two frames.

    skip=2 drops _caller and note; anything more eats the real caller and
    leaves whatever framework called it, which identifies nothing.
    """
    frames = traceback.extract_stack()[:-skip]
    return " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}"
                       for f in reversed(frames[-2:]))


def note(event: str, **fields) -> None:
    """Append one line. Never raises — diagnostics must not break reading."""
    if os.environ.get(_ENV_OFF):
        return
    try:
        bits = " ".join(f"{k}={v}" for k, v in fields.items())
        line = (f"{datetime.now().isoformat(timespec='seconds')} "
                f"{event:<14} {bits} @ {_caller()}\n")
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)
        _trim(p)
    except Exception:       # noqa: BLE001 — a log must never break the app
        pass


def _trim(p) -> None:
    """Keep the file bounded without rewriting it on every single line."""
    try:
        if p.stat().st_size < 96 * 1024:
            return
        lines = p.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:       # noqa: BLE001
        pass


def read_tail(n: int = 60) -> list[str]:
    """The last *n* lines, for showing in Settings or pasting into a report."""
    try:
        return path().read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:       # noqa: BLE001
        return []
