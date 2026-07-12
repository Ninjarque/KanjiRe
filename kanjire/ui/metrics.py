"""Resolution-aware UI scaling.

Scenes call :func:`scale_for(width, height)` in their ``on_resize`` and multiply
their base font sizes, button heights, margins and gaps by the result, so the
layout shrinks to fit small screens (e.g. 1600x900) and grows on large ones
(e.g. 2560x1440) without overlapping or looking tiny.

The reference is the historical design size (1180x1020), so ``scale_for`` returns
exactly ``1.0`` there and the look at the default window is unchanged.
"""
from __future__ import annotations

#: The reference layout size. Height is deliberately *below* the historical
#: design height (1020): dividing by 880 makes a 1080p window scale to ~1.23
#: instead of ~1.06, so the UI reads comfortably large on common screens
#: (it used to feel small at 1080p+). Layouts must therefore tolerate ~1.25x
#: their design height at 1080 - verified by the multi-size capture sweep.
REF_W, REF_H = 1180, 880
#: Clamp so text never collapses to unreadable nor balloons absurdly.
#: The floor tracks the minimum window (760x600 -> ~0.59): flooring higher
#: (it used to be 0.80) made tall layouts like the Learn Advanced tab stop
#: shrinking and overlap the footer on short windows. Font sizes have their
#: own per-label minimums, so small scales stay legible.
MIN_SCALE, MAX_SCALE = 0.58, 1.70


def scale_for(width: float, height: float) -> float:
    """UI scale for a window of *width* x *height*.

    Uses the smaller of the width/height ratios so content never overflows the
    short axis (a wide-but-short window scales by its height)."""
    raw = min(width / REF_W, height / REF_H)
    return max(MIN_SCALE, min(MAX_SCALE, raw))
