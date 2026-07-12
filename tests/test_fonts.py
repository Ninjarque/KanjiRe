"""Font guards for the two Linux rendering bugs (both invisible on Windows).

1. The UI font must be a real *text* face with kana/kanji. It used to be
   "whatever registered first" - which is DotGothic16, a decorative pixel
   font - and on any machine without Yu Gothic UI that became the whole
   interface. Worse, ``have_font()`` (fontconfig) reported False for our
   added files on Linux, so every bundled font was dropped, ``JP_FONT``
   became None, and pyglet fell back to a Latin-only face: 漢字 rendered as
   tofu boxes.

2. Every glyph used in a UI string must actually exist in the bundled fonts.
   ▶ ⚡ ✓ ↻ are NOT in them, and shipped as empty boxes on Linux.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from kanjire.paths import PACKAGE_DIR

FONTS_DIR = PACKAGE_DIR / "fonts"

#: Ranges we don't check per-glyph (any Japanese font covers them, and the
#: bundled ones are verified for kana+kanji separately below).
_JP = ((0x3000, 0x30FF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))


def _string_constants(path):
    """Every string literal in a module except docstrings - i.e. the text that
    can actually reach a Label. Comments and docstrings are prose (they say
    things like "yōon" or "kanji↔reading") and must not trip the guard."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr):
                    docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.value


def _ui_symbols() -> dict[str, set[str]]:
    """Non-ASCII, non-Japanese chars in renderable strings -> files using them.

    Scans every module, not just i18n: the streak ❄, the Journey ▶ and the
    dictation 🔊 are built in scene code and were tofu on Linux precisely
    because nothing checked them.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for text in _string_constants(path):
            for ch in text:
                cp = ord(ch)
                if cp < 0x00A0 or any(lo <= cp <= hi for lo, hi in _JP):
                    continue
                out.setdefault(ch, set()).add(path.name)
    return out


def _cmaps():
    fontTools = pytest.importorskip("fontTools",
                                    reason="fontTools not installed")
    from fontTools.ttLib import TTFont
    out = {}
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        f = TTFont(str(path), fontNumber=0, lazy=True)
        cps = set()
        for t in f["cmap"].tables:
            cps |= set(t.cmap.keys())
        out[path.name] = cps
        f.close()
    return out


def test_ui_font_is_a_real_text_face_not_the_pixel_font():
    from kanjire.ui import fonts
    assert fonts.JP_FONT, "JP_FONT is None -> pyglet falls back to a font with no kanji"
    assert fonts.JP_FONT != "DotGothic16", (
        "the decorative pixel font became the UI font; _UI_PREFERRED must win"
    )
    # The tail of _UI_PREFERRED is bundled, so resolution can never fail.
    assert fonts.JP_FONT in fonts._UI_PREFERRED


def test_bundled_fonts_are_registered_without_asking_have_font():
    from kanjire.ui import fonts
    # add_file succeeded for every shipped ttf -> all families available.
    assert len(fonts._bundled_ok) == len(list(FONTS_DIR.glob("*.ttf"))), (
        f"bundled fonts were dropped: {fonts._bundled_ok}"
    )
    for family in ("Zen Maru Gothic", "Klee One", "DotGothic16"):
        assert family in fonts.JP_FONTS


def test_every_ui_symbol_exists_in_every_bundled_font():
    cmaps = _cmaps()
    assert cmaps, "no bundled fonts found"
    syms = _ui_symbols()
    missing: dict[str, list[str]] = {}
    for ch in sorted(syms):
        absent = [name for name, cps in cmaps.items() if ord(ch) not in cps]
        if absent:
            missing[f"{ch!r} U+{ord(ch):04X} used in {sorted(syms[ch])}"] = absent
    assert not missing, (
        "UI strings use glyphs the bundled fonts don't have (they render as "
        f"tofu boxes on Linux): {missing}"
    )


def test_bold_resolves_to_a_japanese_face_not_a_latin_fallback():
    """The bug behind the tofu title: pyglet's FreeType backend keys added
    faces on (name, bold, italic). We ship Regular only, so ``bold=True``
    missed the store, fell through to fontconfig and returned DejaVu Sans -
    no kanji. Windows hid this because GDI synthesises bold."""
    import pyglet

    from kanjire.ui import fonts

    if pyglet.font._font_class.__name__ != "FreeTypeFont":
        pytest.skip("bold aliasing only applies to the FreeType backend")
    for family in fonts._bundled_ok:
        for bold in (False, True):
            got = pyglet.font.load(family, 16, bold=bold)
            assert got.name == family, (
                f"load({family!r}, bold={bold}) fell back to {got.name!r} - "
                "Japanese text in that style will render as tofu boxes"
            )


def test_bundled_fonts_cover_kana_and_kanji():
    cmaps = _cmaps()
    for name, cps in cmaps.items():
        for ch in "漢字あアの":
            assert ord(ch) in cps, f"{name} lacks {ch}"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP/ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
