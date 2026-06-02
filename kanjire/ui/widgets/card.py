"""The on-screen card: one face of one word, with neon-arcade styling.

The card's animatable state (``offset_y``, ``scale``, ``glow``, ``alpha``,
``flash``, ``shake``) is driven by tweens in the scene; :meth:`apply` re-maps
those plus the card's logical state onto the underlying pyglet primitives each
frame.

Familiarization mode passes a ``font_name`` and a ``direction`` per card so the
same word can be re-dealt looking different every pass.
"""
from __future__ import annotations

import re

from pyglet import shapes
from pyglet.text import Label

from kanjire.game.engine import Card
from kanjire.i18n import get_locale
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT


def _face_badge(face: str) -> str:
    """Tiny label in the corner of a card. The meaning face shows the active
    locale code so the player always knows which language the text is in."""
    if face == "meaning":
        return get_locale().upper()
    return theme.FACE_LABELS.get(face, "")

_PAD = 12
#: Smallest font the meaning face will shrink to so a single un-wrappable word
#: still fits a narrow card (e.g. a 24-word board at the default window size).
_MEANING_MIN_FS = 9


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def short_meaning(s: str, limit: int = 40) -> str:
    """Trim a dictionary gloss down to something that fits a card."""
    s = s.split(";")[0]
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—,")
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or "…"


#: Decorative bundled fonts (Reggae One, Yuji Boku, Hachi Maru Pop, …) have
#: noticeably wider advance widths and larger ascender/descender than gothic
#: fonts, so the horizontal/vertical metrics need a generous safety margin.
_H_WIDTH_FACTOR = 0.82           # 1em wide × this factor
_V_LINE_FACTOR = 1.30            # extra vertical breathing room between glyphs
#: Effective "extra char" of margin added to the vertical denominator so the
#: top of the first glyph and the bottom of the last never clip the card edge.
_V_SAFETY = 0.30


def _font_size_horizontal(face: str, text: str, w: float, h: float) -> int:
    if face == "kanji":
        target = h * 0.38
        max_by_width = (w - 2 * _PAD) / max(1, len(text)) * _H_WIDTH_FACTOR
        return int(max(16, min(target, max_by_width)))
    if face == "reading":
        target = h * 0.21
        max_by_width = (w - 2 * _PAD) / max(1, len(text)) * _H_WIDTH_FACTOR
        return int(max(13, min(target, max_by_width)))
    # meaning - English, may wrap.
    length = len(text)
    if length <= 10:
        base = h * 0.19
    elif length <= 20:
        base = h * 0.15
    elif length <= 30:
        base = h * 0.125
    else:
        base = h * 0.105
    return int(max(11, min(base, 17)))


def _font_size_vertical(text: str, w: float, h: float) -> int:
    """Size for a stacked-tategaki column.

    Total column occupies ``font_size + (n-1) * line_h``; we add ``_V_SAFETY``
    extra "char-worths" of padding so decorative fonts (taller ascenders /
    descenders) never spill above or below the card.
    """
    n = max(1, len(text))
    denom = 1.0 + (n - 1) * _V_LINE_FACTOR + _V_SAFETY
    by_height = (h - 2 * _PAD) / denom
    by_width = (w - 2 * _PAD) * _H_WIDTH_FACTOR
    return int(max(13, min(by_height, by_width, h * 0.38)))


# --------------------------------------------------------------------------- #
# Text rendering: horizontal Label or stacked-vertical chars
# --------------------------------------------------------------------------- #
class CardText:
    """Renders a card's text either horizontally (one Label) or vertically
    (one Label per character, stacked top-to-bottom Japanese-style).

    Per-frame updates (position, shake, alpha) only touch ``x``/``y``/``color``
    so they don't trigger an expensive Label re-layout.
    """

    def __init__(self, text, face, font_name, direction, batch, group):
        self.text = text
        self.face = face
        self.font_name = font_name
        # Meaning is English; vertical doesn't help recognition, so force h.
        self.direction = "horizontal" if face == "meaning" else direction
        self._batch = batch
        self._group = group
        self._labels: list[Label] = []
        self._rel_y: list[float] = []
        self._base_x = 0.0
        self._base_y = 0.0
        self._color = theme.TEXT
        self._alpha = 1.0
        self._build()

    # -- construction ------------------------------------------------- #
    def _mk(self, text: str, **kw) -> Label:
        return Label(
            text, font_name=self.font_name, font_size=14,
            color=theme.with_alpha(self._color, 255),
            anchor_x="center", anchor_y="center",
            batch=self._batch, group=self._group, **kw,
        )

    def _build(self) -> None:
        for L in self._labels:
            L.delete()
        self._labels.clear()
        self._rel_y.clear()
        if self.direction == "vertical":
            for ch in self.text:
                self._labels.append(self._mk(ch))
                self._rel_y.append(0.0)
        else:
            kw = {}
            if self.face == "meaning":
                kw.update(multiline=True, align="center", width=10)
            self._labels.append(self._mk(self.text, **kw))
            self._rel_y.append(0.0)

    # -- geometry (re-layout: only when slot changes) ----------------- #
    def set_geometry(self, cx: float, cy: float, w: float, h: float) -> None:
        self._base_x = cx
        self._base_y = cy
        if self.direction == "vertical":
            n = len(self.text)
            font_size = _font_size_vertical(self.text, w, h)
            line_h = font_size * _V_LINE_FACTOR
            total = (n - 1) * line_h
            for i, L in enumerate(self._labels):
                L.font_size = font_size
                self._rel_y[i] = total / 2 - i * line_h
                L.x = cx
                L.y = cy + self._rel_y[i]
        else:
            L = self._labels[0]
            fs = _font_size_horizontal(self.face, self.text, w, h)
            if self.face == "meaning":
                avail = max(10, int(w - 2 * _PAD))
                L.width = avail
                L.font_size = fs
                # A long single word can't wrap, so the laid-out content may be
                # wider than the card. Shrink the font until the widest line
                # fits, measuring the real layout (font-agnostic) rather than
                # guessing from character counts.
                guard = 0
                while (fs > _MEANING_MIN_FS and L.content_width > avail
                       and guard < 20):
                    fs -= 1
                    L.font_size = fs
                    guard += 1
            else:
                L.font_size = fs
            self._rel_y[0] = 0.0
            L.x = cx
            L.y = cy

    # -- per-frame (cheap) -------------------------------------------- #
    def apply(self, offset_y: float, shake: float, alpha: float) -> None:
        self._alpha = max(0.0, min(1.0, alpha))
        a = int(255 * self._alpha)
        bx = self._base_x + shake
        by = self._base_y + offset_y
        col = (self._color[0], self._color[1], self._color[2], a)
        for i, L in enumerate(self._labels):
            L.x = bx
            L.y = by + self._rel_y[i]
            L.color = col

    def set_color(self, color: theme.Color) -> None:
        self._color = color

    def delete(self) -> None:
        for L in self._labels:
            L.delete()
        self._labels.clear()


# --------------------------------------------------------------------------- #
# The card itself
# --------------------------------------------------------------------------- #
class CardView:
    def __init__(
        self,
        model: Card,
        batch,
        glow_group,
        bg_group,
        text_group,
        *,
        font_name: str | None = None,
        direction: str = "horizontal",
    ) -> None:
        self.model = model
        self.face = model.face
        self.face_color = theme.FACE_COLORS.get(model.face, theme.ACCENT)
        self.display_text = (
            short_meaning(model.text) if model.face == "meaning" else model.text
        )

        self.cx = self.cy = 0.0
        self.bw = self.bh = 10.0

        # animatable state
        self.offset_y = 0.0
        self.scale = 1.0
        self.glow = 0.0
        self.alpha = 1.0
        self.flash = 0.0
        self.shake = 0.0
        self.visible = True

        self._glow = shapes.Rectangle(
            0, 0, 10, 10, color=self.face_color, batch=batch, group=glow_group
        )
        self._glow.opacity = 0
        self._bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=3,
            color=theme.PANEL, border_color=self.face_color,
            batch=batch, group=bg_group,
        )
        self._badge = Label(
            _face_badge(model.face),
            font_name=JP_FONT, font_size=10,
            color=theme.with_alpha(self.face_color, 230),
            anchor_x="left", anchor_y="top", batch=batch, group=text_group,
        )
        # Top-right corner sticker (Survival): 新 (new) / ♥ / ¥ bounty markers.
        self._sticker_text = ""
        self._sticker_color = theme.GOLD
        self._sticker = Label(
            "", font_name=JP_FONT, font_size=14, bold=True,
            color=theme.with_alpha(theme.GOLD, 0),
            anchor_x="right", anchor_y="top", batch=batch, group=text_group,
        )
        self._text = CardText(
            self.display_text, model.face,
            font_name or JP_FONT, direction,
            batch, text_group,
        )

    # ------------------------------------------------------------------ #
    def set_sticker(self, text: str, color: theme.Color | None = None) -> None:
        """Set (or clear, with '') the corner sticker glyph + colour."""
        self._sticker_text = text or ""
        if color is not None:
            self._sticker_color = color
        self._sticker.text = self._sticker_text

    def set_slot(self, cx: float, cy: float, w: float, h: float) -> None:
        self.cx, self.cy, self.bw, self.bh = cx, cy, w, h
        self._text.set_geometry(cx, cy, w, h)
        self.apply()

    def contains(self, px: float, py: float) -> bool:
        return (
            self.cx - self.bw / 2 <= px <= self.cx + self.bw / 2
            and self.cy - self.bh / 2 <= py <= self.cy + self.bh / 2
        )

    # ------------------------------------------------------------------ #
    def apply(self) -> None:
        a = max(0.0, min(1.0, self.alpha))
        if not self.visible or a <= 0.0:
            self._glow.opacity = 0
            self._bg.opacity = 0
            self._text.apply(self.offset_y, self.shake, 0.0)
            self._badge.color = theme.with_alpha(self.face_color, 0)
            self._sticker.color = theme.with_alpha(self._sticker_color, 0)
            return

        s = self.scale
        w, h = self.bw * s, self.bh * s
        x = self.cx - w / 2 + self.shake
        y = self.cy - h / 2 + self.offset_y

        selected = self.model.selected
        face = self.face_color

        # A PANEL-dominated fill (so the card body stays on the same
        # light/dark side as the rest of the UI), with the face colour
        # carried mainly by the border/badge. tint() flips direction on
        # light palettes so selected borders don't wash out.
        fill = theme.lerp(theme.PANEL, face, 0.18)
        border = theme.tint(face, 0.25 if selected else 0.0)
        if self.flash > 0:
            border = theme.lerp(border, theme.DANGER, self.flash)
            fill = theme.lerp(fill, theme.tint(theme.DANGER, 0.2), self.flash * 0.6)

        glow_amt = max(self.glow, self.flash)
        gm = 6 + 26 * glow_amt
        self._glow.x, self._glow.y = x - gm, y - gm
        self._glow.width, self._glow.height = w + 2 * gm, h + 2 * gm
        self._glow.color = theme.DANGER if self.flash > 0.3 else face
        self._glow.opacity = int(150 * glow_amt * a)

        self._bg.x, self._bg.y = x, y
        self._bg.width, self._bg.height = w, h
        self._bg.color = fill
        self._bg.border_color = border
        self._bg.opacity = int(255 * a)

        self._badge.x = x + 8
        self._badge.y = y + h - 6
        # Keep the face-tinted badge where it reads, fall back to a contrasting
        # near-black on bright (light-palette) fills.
        self._badge.color = theme.with_alpha(theme.readable_on(fill, light=face),
                                              int(230 * a))

        self._sticker.x = x + w - 8
        self._sticker.y = y + h - 5
        self._sticker.color = theme.with_alpha(
            self._sticker_color, int(235 * a) if self._sticker_text else 0
        )

        self._text.apply(self.offset_y, self.shake, a)

    def delete(self) -> None:
        self._glow.delete()
        self._bg.delete()
        self._badge.delete()
        self._sticker.delete()
        self._text.delete()
