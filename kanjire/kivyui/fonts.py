"""Register the bundled Japanese fonts with Kivy.

Unlike the pyglet side there is no system-font discovery: Android ships no
usable Japanese UI face we can rely on by name, so the bundled SIL-OFL fonts
are the fonts, everywhere, on every platform. That also means the app looks
identical on Windows, Linux and Android.

``UI_FONT`` is the interface face (Zen Maru Gothic: full kana/kanji/Latin,
a real text face). The decorative faces exist for Familiarize-style variety.
"""
from __future__ import annotations

from kivy.core.text import LabelBase

from kanjire.paths import PACKAGE_DIR

#: (registered-name, filename) — same set the pyglet UI bundles.
BUNDLED: list[tuple[str, str]] = [
    ("DotGothic16",     "DotGothic16-Regular.ttf"),
    ("Klee One",        "KleeOne-Regular.ttf"),
    ("Yuji Boku",       "YujiBoku-Regular.ttf"),
    ("Hachi Maru Pop",  "HachiMaruPop-Regular.ttf"),
    ("Reggae One",      "ReggaeOne-Regular.ttf"),
    ("Zen Maru Gothic", "ZenMaruGothic-Regular.ttf"),
]

UI_FONT = "Zen Maru Gothic"

_FONTS_DIR = PACKAGE_DIR / "fonts"
_registered: list[str] = []


def register() -> list[str]:
    """Idempotently register every bundled face. Returns available names."""
    if _registered:
        return _registered
    for family, fname in BUNDLED:
        path = _FONTS_DIR / fname
        if not path.exists():
            continue
        try:
            LabelBase.register(name=family, fn_regular=str(path))
        except Exception:
            continue
        _registered.append(family)
    return _registered


#: Every registered Japanese face (for font-variety modes).
def jp_fonts() -> list[str]:
    return list(_registered)
