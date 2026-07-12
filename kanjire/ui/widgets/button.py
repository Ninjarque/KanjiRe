"""A simple clickable button with hover feedback."""
from __future__ import annotations

from collections.abc import Callable

from pyglet import shapes
from pyglet.text import Label

from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT


class Button:
    def __init__(
        self,
        text: str,
        on_click: Callable[[], None],
        batch,
        bg_group,
        text_group,
        *,
        accent: theme.Color | None = None,
        font_size: int = 16,
    ) -> None:
        self.text = text
        self.on_click = on_click
        self._base_font = font_size  # for resolution scaling (set_scale)
        # Resolve the accent at construction time (not as a def-time default,
        # which would freeze the import-time palette colour and ignore later
        # theme switches).
        self.accent = accent if accent is not None else theme.ACCENT
        self.enabled = True
        self.selected = False
        self.visible = True
        self._hover = False
        self.x = self.y = 0.0
        self.w = self.h = 0.0

        self._bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2,
            color=theme.PANEL, border_color=self.accent, batch=batch, group=bg_group,
        )
        self._label = Label(
            text, font_name=JP_FONT, font_size=font_size,
            color=theme.with_alpha(theme.TEXT, 255),
            anchor_x="center", anchor_y="center", batch=batch, group=text_group,
        )

    def set_rect(self, x: float, y: float, w: float, h: float) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self._bg.x, self._bg.y = x, y
        self._bg.width, self._bg.height = w, h
        self._label.x = x + w / 2
        self._label.y = y + h / 2
        self._refresh()

    def set_text(self, text: str) -> None:
        self.text = text
        self._label.text = text

    def set_scale(self, s: float) -> None:
        """Resize the label font from its construction-time base."""
        self._label.font_size = max(8, round(self._base_font * s))

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def set_hover(self, hover: bool) -> None:
        if hover != self._hover:
            self._hover = hover
            self._refresh()

    def set_selected(self, selected: bool) -> None:
        if selected != self.selected:
            self.selected = selected
            self._refresh()

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable AND repaint. Assigning ``.enabled`` directly leaves
        the old colours on screen, which is how guests ended up with settings
        buttons that never showed their state."""
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self._refresh()

    def set_visible(self, visible: bool) -> None:
        """Hide/show without recreating. Hidden buttons don't receive clicks."""
        self.visible = bool(visible)
        self._bg.visible = self.visible
        self.enabled = self.visible
        self._refresh()

    def click(self) -> None:
        if self.enabled:
            self.on_click()

    def _refresh(self) -> None:
        # Colours are chosen via theme.tint / theme.readable_on so the button
        # stays legible under both dark and light palettes (lighten-on-dark vs
        # darken-on-light is handled centrally by the theme module).
        if not self.visible:
            # Every branch below assigns an RGBA colour, and in pyglet setting
            # Label.color overwrites its alpha - so a plain `opacity = 0` here
            # was undone by the very next repaint and hidden buttons kept
            # drawing their text (a ghost "Start game" floated on the guest's
            # lobby). Hiding has to go through the colour too.
            self._label.color = theme.with_alpha(theme.TEXT, 0)
            return
        if not self.enabled:
            # A disabled button must STILL show whether it is the selected
            # one - read-only is not the same as unreadable. (Multiplayer
            # guests see the host's settings this way: they can't click, but
            # they must be able to tell what's picked.)
            if self.selected:
                bg = theme.lerp(theme.PANEL, self.accent, 0.42)
                self._bg.color = bg
                self._bg.border_color = theme.tint(self.accent, 0.25)
                self._label.color = theme.with_alpha(theme.readable_on(bg), 255)
            else:
                bg = theme.tint(theme.PANEL, 0.3)
                self._bg.color = bg
                self._bg.border_color = theme.DIM
                # Contrast against THIS fill, not the panel: a flat DIM label on
                # the lightened disabled fill was almost unreadable. Muted, but
                # still legible - a guest has to be able to read the options the
                # host didn't pick.
                self._label.color = theme.with_alpha(theme.readable_on(bg), 165)
            return
        if self.selected:
            bg = theme.lerp(theme.PANEL, self.accent, 0.55)
            self._bg.color = bg
            self._bg.border_color = theme.tint(self.accent, 0.3)
            self._label.color = theme.with_alpha(theme.readable_on(bg), 255)
        elif self._hover:
            bg = theme.lerp(theme.PANEL, self.accent, 0.22)
            self._bg.color = bg
            self._bg.border_color = theme.tint(self.accent, 0.2)
            self._label.color = theme.with_alpha(theme.readable_on(bg), 255)
        else:
            self._bg.color = theme.PANEL
            self._bg.border_color = self.accent
            self._label.color = theme.with_alpha(theme.TEXT, 255)

    def delete(self) -> None:
        self._bg.delete()
        self._label.delete()
