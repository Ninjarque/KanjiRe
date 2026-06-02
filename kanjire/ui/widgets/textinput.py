"""Inline text input built on pyglet's Caret + IncrementalTextLayout.

Native-feeling editing (cursor, selection, IME-friendly text events) without
spawning a tkinter popup. Used for the search box on the Stats Words / Kanji
tabs.
"""
from __future__ import annotations

from collections.abc import Callable

from pyglet import shapes
from pyglet.text import caret as _caret
from pyglet.text import document as _doc
from pyglet.text import layout as _layout
from pyglet.text import Label

from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT


class TextInput:
    def __init__(
        self,
        batch,
        bg_group,
        layout_group,
        text_group,
        *,
        font_size: int = 13,
        placeholder: str = "Search…",
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.on_change = on_change
        self._focused = False
        self.x = self.y = 0.0
        self.w = self.h = 10.0

        self._bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2,
            color=theme.PANEL, border_color=theme.DIM,
            batch=batch, group=bg_group,
        )
        self._document = _doc.UnformattedDocument("")
        self._document.set_style(0, 0, dict(
            font_name=JP_FONT, font_size=font_size,
            color=theme.with_alpha(theme.TEXT, 255),
        ))
        self._layout = _layout.IncrementalTextLayout(
            self._document, 10, 10, multiline=False,
            batch=batch, group=layout_group,
        )
        self._caret = _caret.Caret(self._layout, color=theme.readable_on(theme.PANEL))
        self._caret.visible = False

        self._placeholder = Label(
            placeholder, font_name=JP_FONT, font_size=font_size,
            color=theme.with_alpha(theme.DIM, 255),
            anchor_x="left", anchor_y="center",
            batch=batch, group=text_group,
        )

    # ---------------------------------------------------------------- #
    def set_rect(self, x: float, y: float, w: float, h: float) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self._bg.x = x
        self._bg.y = y
        self._bg.width = w
        self._bg.height = h
        pad_x = 10
        pad_y = 4
        # IncrementalTextLayout feeds these straight into glScissor, which needs
        # ints — callers may pass scaled floats, so coerce here.
        self._layout.x = int(x + pad_x)
        self._layout.y = int(y + pad_y)
        self._layout.width = int(max(10, w - 2 * pad_x))
        self._layout.height = int(max(8, h - 2 * pad_y))
        self._placeholder.x = x + pad_x
        self._placeholder.y = y + h / 2

    @property
    def text(self) -> str:
        return self._document.text

    def set_text(self, value: str) -> None:
        if self._document.text != value:
            self._document.text = value
            self._notify()

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    # ---------------------------------------------------------------- #
    # Focus management
    # ---------------------------------------------------------------- #
    def focus(self) -> None:
        if self._focused:
            return
        self._focused = True
        self._caret.visible = True
        self._caret.position = len(self._document.text)
        self._bg.border_color = theme.ACCENT
        self._update_placeholder()

    def unfocus(self) -> None:
        if not self._focused:
            return
        self._focused = False
        self._caret.visible = False
        self._bg.border_color = theme.DIM
        self._update_placeholder()

    @property
    def focused(self) -> bool:
        return self._focused

    # ---------------------------------------------------------------- #
    # Event delegation - scene routes window events here
    # ---------------------------------------------------------------- #
    def on_mouse_press(self, x, y, button, modifiers) -> bool:
        if self.contains(x, y):
            self.focus()
            self._caret.on_mouse_press(x, y, button, modifiers)
            return True
        self.unfocus()
        return False

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        # No-op for now; could implement text selection drag later.
        pass

    def on_text(self, text: str) -> bool:
        if not self._focused:
            return False
        self._caret.on_text(text)
        self._notify()
        return True

    def on_text_motion(self, motion) -> bool:
        if not self._focused:
            return False
        self._caret.on_text_motion(motion)
        self._notify()
        return True

    def on_text_motion_select(self, motion) -> bool:
        if not self._focused:
            return False
        self._caret.on_text_motion_select(motion)
        return True

    def on_key_press(self, symbol, modifiers) -> bool:
        if not self._focused:
            return False
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self.unfocus()
            return True
        return False

    # ---------------------------------------------------------------- #
    def _notify(self) -> None:
        self._update_placeholder()
        if self.on_change is not None:
            self.on_change(self._document.text)

    def _update_placeholder(self) -> None:
        empty_and_blurred = (not self._document.text) and not self._focused
        empty_and_focused = (not self._document.text) and self._focused
        # Show placeholder when not focused and empty; hide otherwise.
        self._placeholder.opacity = 255 if empty_and_blurred else 0
        # Slight dim when focused-empty so user sees the cursor not the hint.
        if empty_and_focused:
            self._placeholder.opacity = 0

    def delete(self) -> None:
        self._caret.delete()
        self._layout.delete()
        self._bg.delete()
        self._placeholder.delete()
