"""A framed surface used to group related controls into a titled card.

Purely presentational: it draws a bordered background with an optional header
label. Controls are positioned on top of it by the owning scene; the panel does
not own them. Read ``theme`` colours live, so a palette switch + scene rebuild
picks up new colours automatically.
"""
from __future__ import annotations

from pyglet import shapes
from pyglet.text import Label

from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT


class Panel:
    def __init__(
        self,
        batch,
        bg_group,
        text_group,
        *,
        title: str = "",
        accent: theme.Color | None = None,
    ) -> None:
        self.title = title
        self.x = self.y = 0.0
        self.w = self.h = 10.0
        # A subtle raised surface sitting between BG and PANEL so the controls
        # (which use PANEL) still stand out against it.
        self._bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2,
            color=theme.lerp(theme.BG, theme.PANEL, 0.5),
            border_color=accent or theme.PANEL_HI,
            batch=batch, group=bg_group,
        )
        self._base_header_font = 12
        self._header: Label | None = None
        if title:
            self._header = Label(
                title, font_name=JP_FONT, font_size=self._base_header_font, bold=True,
                color=theme.with_alpha(theme.MUTED, 255),
                anchor_x="left", anchor_y="center",
                batch=batch, group=text_group,
            )

    def set_rect(self, x: float, y: float, w: float, h: float) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self._bg.x, self._bg.y = x, y
        self._bg.width, self._bg.height = w, h
        if self._header is not None:
            self._header.x = x + 18
            self._header.y = y + h - 18

    def set_scale(self, s: float) -> None:
        if self._header is not None:
            self._header.font_size = max(8, round(self._base_header_font * s))

    def set_visible(self, visible: bool) -> None:
        self._bg.visible = visible
        if self._header is not None:
            self._header.opacity = 255 if visible else 0

    def delete(self) -> None:
        self._bg.delete()
        if self._header is not None:
            self._header.delete()
