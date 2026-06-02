"""Grid layout helper for arranging cards in the play area."""
from __future__ import annotations

import math


def choose_grid(
    n: int, area_w: float, area_h: float, gap: float = 16.0
) -> tuple[int, int, float, float]:
    """Choose (cols, rows, cell_w, cell_h) maximising card area.

    Prefers card aspect ratios that look like cards (taller-ish), but always
    returns *something* usable even for awkward counts.
    """
    if n <= 0:
        return 1, 1, area_w, area_h

    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell_w = (area_w - (cols + 1) * gap) / cols
        cell_h = (area_h - (rows + 1) * gap) / rows
        if cell_w <= 0 or cell_h <= 0:
            continue
        aspect = cell_w / cell_h
        # Penalise extreme aspect ratios; favour ~0.8..1.6 (portrait-ish cards).
        penalty = 1.0
        if aspect > 1.7:
            penalty = 1.7 / aspect
        elif aspect < 0.6:
            penalty = aspect / 0.6
        score = cell_w * cell_h * penalty
        if best is None or score > best[0]:
            best = (score, cols, rows, cell_w, cell_h)

    if best is None:  # degenerate fallback
        return n, 1, area_w / n, area_h
    _, cols, rows, cell_w, cell_h = best
    return cols, rows, cell_w, cell_h


def slot_center(
    index: int,
    cols: int,
    rows: int,
    cell_w: float,
    cell_h: float,
    area_x: float,
    area_y: float,
    area_w: float,
    area_h: float,
    gap: float = 16.0,
    *,
    count: int | None = None,
) -> tuple[float, float]:
    """Centre (x, y) of the *index*-th cell. Rows fill from the top.

    The last (possibly partial) row is horizontally centred for a tidy look.
    """
    row = index // cols
    col = index % cols

    # Centre a short final row.
    if count is not None:
        in_this_row = min(cols, count - row * cols)
    else:
        in_this_row = cols
    row_width = in_this_row * cell_w + (in_this_row - 1) * gap
    row_x0 = area_x + (area_w - row_width) / 2

    cx = row_x0 + col * (cell_w + gap) + cell_w / 2
    # y from top
    top = area_y + area_h
    cy = top - gap - row * (cell_h + gap) - cell_h / 2
    return cx, cy
