"""Japanese fonts: register bundled .ttfs and expose the variety as ``JP_FONTS``.

The repo ships a curated set of free (SIL OFL) Japanese fonts under
``kanjire/fonts/``. They are registered with pyglet at import time so labels can
reference them by family name. System fonts that exist on the player's machine
are added on top so familiarization mode can mix everything.

Two hard-won rules live here (both caused real bugs on Linux):

1. **Never gate a bundled font on ``have_font()``.** That query goes through
   fontconfig on Linux and can report False for a font we just registered - and
   we then dropped *every* bundled font, left ``JP_FONT`` as None, and pyglet
   fell back to a Latin-only system face: the whole UI rendered in a fallback
   font with 漢字 as tofu boxes. If ``add_file`` didn't raise, the font is
   usable; trust it.
2. **Pick the UI font from an explicit preference list**, never "whatever
   registered first" - the bundled list starts with DotGothic16, a decorative
   *pixel* face meant only for Familiarize mode, which then became the entire
   interface font on any machine without Yu Gothic UI.

Set ``KANJIRE_FONT_DEBUG=1`` to print what got resolved.
"""
from __future__ import annotations

import os
import sys

import pyglet

from kanjire.paths import PACKAGE_DIR

# --------------------------------------------------------------------------- #
# Bundled fonts shipped in kanjire/fonts/
# --------------------------------------------------------------------------- #
#: ``(family-name-as-pyglet-sees-it, .ttf-filename)`` for the bundled fonts.
#: Order matters only for Familiarize's random rotation - the UI font is
#: chosen from _UI_PREFERRED below.
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
    # Windows
    "Yu Gothic UI", "Meiryo", "BIZ UDGothic", "MS Gothic",
    "Yu Mincho", "MS Mincho",
    # Linux
    "Noto Sans CJK JP", "Noto Sans JP", "Noto Serif CJK JP", "Noto Serif JP",
    "Source Han Sans JP", "IPAPGothic", "IPAGothic", "TakaoPGothic",
    "VL PGothic", "Droid Sans Japanese",
    # macOS
    "Hiragino Sans", "Hiragino Kaku Gothic ProN",
]

#: The UI font, most-preferred first. Real text faces only - a decorative
#: bundled font must never become the interface font. "Zen Maru Gothic" and
#: "Klee One" are bundled, so the tail of this list always resolves.
_UI_PREFERRED = [
    "Yu Gothic UI", "Meiryo",                        # Windows
    "Noto Sans CJK JP", "Noto Sans JP",              # Linux (best coverage)
    "Source Han Sans JP", "IPAPGothic", "IPAGothic",
    "TakaoPGothic", "VL PGothic",
    "Hiragino Sans",                                 # macOS
    "Zen Maru Gothic", "Klee One",                   # bundled: always there
]

_FONTS_DIR = PACKAGE_DIR / "fonts"


def _have(name: str) -> bool:
    """Is this a font the OS already knows about? (system fonts only)."""
    try:
        return bool(pyglet.font.have_font(name))
    except Exception:
        return False


def _register_bundled() -> list[str]:
    """Load each bundled .ttf. A family counts as available when ``add_file``
    succeeds - we deliberately do NOT ask ``have_font()`` afterwards (see the
    module docstring: it lies on Linux and used to nuke every bundled font)."""
    available: list[str] = []
    for family, fname in BUNDLED:
        path = _FONTS_DIR / fname
        if not path.exists():
            continue
        try:
            pyglet.font.add_file(str(path))
        except Exception:
            continue
        available.append(family)
    _alias_bold_faces()
    return available


def _alias_bold_faces() -> None:
    """Make ``bold=True`` resolve to our regular face instead of DejaVu Sans.

    pyglet's FreeType backend (Linux/BSD) keys added faces on
    ``(name, bold, italic)``. We ship Regular weights only, so a bold lookup
    misses the store, silently falls through to fontconfig, and returns
    **DejaVu Sans** - which has no kanji. That is why every *bold* Japanese
    label (the "KanjiRe 漢字" title, card text) rendered as tofu boxes on
    Linux while the regular ones were fine. Windows never showed it because
    GDI synthesises bold from the regular face.

    Registering each regular face under the bold/italic keys as well gives us
    real glyphs (unemboldened, which is far better than empty boxes).
    """
    try:
        from pyglet.font import freetype
    except Exception:      # not the FreeType backend (Windows/macOS): nothing to do
        return
    store = getattr(freetype.FreeTypeFont, "_memory_faces", None)
    faces = getattr(store, "_dict", None)
    if not isinstance(faces, dict):
        return
    for (name, bold, italic), face in list(faces.items()):
        if bold or italic:
            continue
        for key in ((name, True, False), (name, False, True), (name, True, True)):
            faces.setdefault(key, face)


_bundled_ok = _register_bundled()
_system_ok = [f for f in _SYSTEM_CANDIDATES if _have(f)]

#: Every Japanese font we can use, bundled first then system fonts.
JP_FONTS: list[str] = _bundled_ok + _system_ok

#: A single safe default for HUD/UI text (no randomness, full kana/kanji).
JP_FONT: str | None = next(
    (f for f in _UI_PREFERRED if f in _bundled_ok or _have(f)),
    JP_FONTS[0] if JP_FONTS else None,
)

if os.environ.get("KANJIRE_FONT_DEBUG"):
    print(f"[fonts] dir={_FONTS_DIR} exists={_FONTS_DIR.exists()}\n"
          f"[fonts] bundled={_bundled_ok}\n"
          f"[fonts] system={_system_ok}\n"
          f"[fonts] JP_FONT={JP_FONT!r}", file=sys.stderr)
