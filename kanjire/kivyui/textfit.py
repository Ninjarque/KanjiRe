"""Font-size fitting: the Kivy counterpart of the pyglet card text shrinker."""
from __future__ import annotations

from kivy.core.text import Label as CoreLabel


def fit_font_size(text: str, max_w: float, max_h: float, *,
                  font_name: str, start: float, floor: float = 9.0,
                  wrap: bool = False) -> float:
    """Largest font size <= *start* at which *text* fits in the box.

    With *wrap* the text may break into multiple lines (meanings); without it
    the text must fit on one line (kanji / readings).
    """
    if not text:
        return start
    size = start
    while size > floor:
        lbl = CoreLabel(text=text, font_name=font_name, font_size=size)
        if wrap:
            lbl.options["text_size"] = (max_w, None)
        lbl.refresh()
        w, h = lbl.texture.size if lbl.texture else (0, 0)
        if w <= max_w and h <= max_h:
            return size
        size -= 1.0
    return floor
