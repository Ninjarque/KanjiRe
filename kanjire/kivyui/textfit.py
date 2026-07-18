"""Font-size fitting: the Kivy counterpart of the pyglet card text shrinker.

This runs on the UI thread while a board assembles, so its cost is the
launch stall. The original walked DOWN from `start` one point at a time,
rendering a full text texture per candidate — up to ~20 renders per card,
re-run on every card relayout. Now: one measurement + a proportional jump
for single-line text, a short binary search for wrapped text, and a global
memo so identical (text, box) pairs never measure twice.
"""
from __future__ import annotations

from kivy.core.text import Label as CoreLabel

#: (text, font, wrap, max_w, max_h, start) -> fitted size. Cleared wholesale
#: if it ever grows silly; entries are tiny and repeat constantly (the same
#: words re-fit on every relayout and every next-round deal).
_cache: dict[tuple, float] = {}


def _measure(text: str, font_name: str, size: float,
             wrap_w: float | None) -> tuple[float, float]:
    lbl = CoreLabel(text=text, font_name=font_name, font_size=size)
    if wrap_w is not None:
        lbl.options["text_size"] = (wrap_w, None)
    lbl.refresh()
    return lbl.texture.size if lbl.texture else (0, 0)


def fit_font_size(text: str, max_w: float, max_h: float, *,
                  font_name: str, start: float, floor: float = 9.0,
                  wrap: bool = False) -> float:
    """Largest font size <= *start* at which *text* fits in the box.

    With *wrap* the text may break into multiple lines (meanings); without it
    the text must fit on one line (kanji / readings).
    """
    if not text:
        return start
    key = (text, font_name, bool(wrap), int(max_w), int(max_h), int(start))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    wrap_w = max_w if wrap else None

    w, h = _measure(text, font_name, start, wrap_w)
    if w <= max_w and h <= max_h:
        out = start
    elif not wrap and w and h:
        # One line scales ~linearly with font size: jump straight to the
        # answer, then nudge down a point or two if rounding still spills.
        size = max(floor, start * min(max_w / w, max_h / h))
        w, h = _measure(text, font_name, size, wrap_w)
        guard = 0
        while (w > max_w or h > max_h) and size > floor and guard < 4:
            size -= 1.0
            guard += 1
            w, h = _measure(text, font_name, size, wrap_w)
        out = max(floor, size)
    else:
        # Wrapped text re-flows as it shrinks — nonlinear, so binary search.
        lo, hi, best = floor, start, floor
        for _ in range(6):
            mid = (lo + hi) / 2
            w, h = _measure(text, font_name, mid, wrap_w)
            if w <= max_w and h <= max_h:
                best, lo = mid, mid
            else:
                hi = mid
        out = best

    if len(_cache) > 4096:
        _cache.clear()
    _cache[key] = out
    return out
