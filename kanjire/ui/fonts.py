"""Japanese fonts: register bundled .ttfs and expose the variety as ``JP_FONTS``.

The repo ships a curated set of free (SIL OFL) Japanese fonts under
``kanjire/fonts/``. They are registered with pyglet at import time so labels can
reference them by family name. System fonts that exist on the player's machine
are added on top so familiarization mode can mix everything.
"""
from __future__ import annotations

import pyglet

from kanjire.paths import PACKAGE_DIR

# --------------------------------------------------------------------------- #
# Bundled fonts shipped in kanjire/fonts/
# --------------------------------------------------------------------------- #
#: ``(family-name-as-pyglet-sees-it, .ttf-filename)`` for the bundled fonts.
BUNDLED: list[tuple[str, str]] = [
    ("DotGothic16",     "DotGothic16-Regular.ttf"),
    ("Klee One",        "KleeOne-Regular.ttf"),
    ("Yuji Boku",       "YujiBoku-Regular.ttf"),
    ("Hachi Maru Pop",  "HachiMaruPop-Regular.ttf"),
    ("Reggae One",      "ReggaeOne-Regular.ttf"),
    ("Zen Maru Gothic", "ZenMaruGothic-Regular.ttf"),
]

# System fonts we'll *also* use if the OS has them; widens the visual variety.
_SYSTEM_CANDIDATES = [
    "Yu Gothic UI", "Meiryo", "BIZ UDGothic", "MS Gothic",
    "Yu Mincho", "MS Mincho", "Noto Serif JP", "Noto Sans JP",
]

_FONTS_DIR = PACKAGE_DIR / "fonts"


def _register_bundled() -> list[str]:
    """Load each bundled .ttf with pyglet and return the families that worked."""
    available: list[str] = []
    for family, fname in BUNDLED:
        path = _FONTS_DIR / fname
        if not path.exists():
            continue
        try:
            pyglet.font.add_file(str(path))
        except Exception:
            continue
        if _have(family):
            available.append(family)
    return available


def _have(name: str) -> bool:
    try:
        return bool(pyglet.font.have_font(name))
    except Exception:
        return False


_bundled_ok = _register_bundled()
_system_ok = [f for f in _SYSTEM_CANDIDATES if _have(f)]

#: Every Japanese font we can use, bundled first then system fonts.
JP_FONTS: list[str] = _bundled_ok + _system_ok

#: A single safe default used by HUD/UI text where we don't want randomness.
JP_FONT: str | None = (
    "Yu Gothic UI" if _have("Yu Gothic UI") else (JP_FONTS[0] if JP_FONTS else None)
)
