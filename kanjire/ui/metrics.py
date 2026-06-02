"""Resolution-aware UI scaling.

Scenes call :func:`scale_for(width, height)` in their ``on_resize`` and multiply
their base font sizes, button heights, margins and gaps by the result, so the
layout shrinks to fit small screens (e.g. 1600x900) and grows on large ones
(e.g. 2560x1440) without overlapping or looking tiny.

The reference is the historical design size (1180x1020), so ``scale_for`` returns
exactly ``1.0`` there and the look at the default window is unchanged.
"""
from __future__ import annotations

#: The size the UI was originally laid out at; scale is 1.0 here.
REF_W, REF_H = 1180, 1020
#: Clamp so text never collapses to unreadable nor balloons absurdly.
MIN_SCALE, MAX_SCALE = 0.80, 1.50


def scale_for(width: float, height: float) -> float:
    """UI scale for a window of *width* x *height*.

    Uses the smaller of the width/height ratios so content never overflows the
    short axis (a wide-but-short window scales by its height)."""
    raw = min(width / REF_W, height / REF_H)
    return max(MIN_SCALE, min(MAX_SCALE, raw))
