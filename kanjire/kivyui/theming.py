"""Bridge between the shared palette module and Kivy's 0..1 RGBA colours.

``kanjire.ui.theme`` stays the single source of truth (0..255 int tuples,
live-swapped by ``apply_palette``). Kivy widgets call :func:`rgba` at draw
time so a palette change only needs the screens rebuilt, same convention as
the pyglet UI.
"""
from __future__ import annotations

from kanjire.ui import theme

__all__ = ["theme", "rgba"]


def rgba(c, a: float = 1.0) -> tuple[float, float, float, float]:
    """0..255 int colour tuple -> Kivy 0..1 RGBA."""
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, a)
