"""A modal genre picker: search box, badge grid, clear-all, done.

Forty topics is too many for a row of buttons and too many to thumb past
unfiltered, so picking a genre happens here — and from more than one place
(the Play tab's Advanced options and the multiplayer lobby), which is why it
is a widget rather than another block bolted into a scene.

Selection semantics match the sampler: **nothing selected means every
genre**. Clearing the filter is therefore a first-class action, not an
accident, and the hint says so.
"""
from __future__ import annotations

from collections.abc import Callable

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data.genres import search, valid_genres
from kanjire.i18n import tr
from kanjire.ui import theme
from pyglet import shapes

from kanjire.ui.fonts import JP_FONT
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.textinput import TextInput

#: Badges per row inside the panel.
COLS = 4


class GenrePicker:
    """Overlay owned by a scene: forward events, call ``draw`` last."""

    def __init__(self, on_done: Callable[[list[str]], None]) -> None:
        self._on_done = on_done
        self.visible = False
        self.selected: list[str] = []
        self._query = ""
        self._s = 1.0
        self.width = self.height = 0

        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_mid = OrderedGroup(1)
        self.g_text = OrderedGroup(2)

        def lbl(size, color, bold=False):
            return Label("", font_name=JP_FONT, font_size=size, bold=bold,
                         color=theme.with_alpha(color, 255),
                         anchor_x="center", anchor_y="center",
                         batch=self.batch, group=self.g_text)

        self.title = lbl(15, theme.TEXT, bold=True)
        self.title.text = tr("GENRE_PICK_TITLE")
        self.hint = lbl(11, theme.DIM)
        self.hint.text = tr("GENRE_ALL_HINT")
        self.empty = lbl(12, theme.MUTED)

        self.search = TextInput(self.batch, self.g_mid, self.g_mid,
                                self.g_text, font_size=13,
                                placeholder=tr("GENRE_SEARCH"),
                                on_change=self._on_query)
        self.clear_btn = Button(tr("GENRE_CLEAR_ALL"), self._clear,
                                self.batch, self.g_mid, self.g_text,
                                accent=theme.DANGER, font_size=12)
        self.done_btn = Button(tr("GENRE_DONE"), self._done,
                               self.batch, self.g_mid, self.g_text,
                               accent=theme.SUCCESS, font_size=12)
        # Scrim + panel as real shapes (fill_quad has no alpha, and the
        # modal dialog dims the same way).
        self._dim = shapes.Rectangle(0, 0, 10, 10, color=theme.BG,
                                     batch=self.batch, group=self.g_bg)
        self._dim.opacity = 200
        self._panel = shapes.Rectangle(0, 0, 10, 10, color=theme.PANEL,
                                       batch=self.batch, group=self.g_bg)
        self.badges: list[tuple[str, Button]] = []
        self._build_badges()
        self._set_shapes_visible(False)     # built closed

    # ------------------------------------------------------------------ #
    def open(self, selected) -> None:
        self.selected = list(valid_genres(selected))
        self._query = ""
        self.search.set_text("")
        self.visible = True
        self._build_badges()
        self._set_shapes_visible(True)
        self.layout(self.width, self.height)

    def close(self) -> None:
        self.visible = False
        self.search.unfocus()
        self._set_shapes_visible(False)

    def _set_shapes_visible(self, on: bool) -> None:
        self._dim.visible = on
        self._panel.visible = on
        for lbl in (self.title, self.hint, self.empty):
            lbl.opacity = 255 if on else 0
        for _k, b in self.badges:
            b.set_visible(on)
        self.clear_btn.set_visible(on)
        self.done_btn.set_visible(on)
        self.search.set_visible(on)

    def _done(self) -> None:
        self.close()
        self._on_done(list(self.selected))

    def _clear(self) -> None:
        self.selected = []
        self._refresh()

    def _on_query(self, text: str) -> None:
        self._query = text
        self._build_badges()
        self.layout(self.width, self.height)

    def _toggle(self, key: str) -> None:
        if key in self.selected:
            self.selected.remove(key)      # a filter you can always undo
        else:
            self.selected.append(key)
        self._refresh()

    def _shown(self):
        return search(self._query, label_of=lambda g: tr(g.tr))

    def _build_badges(self) -> None:
        for _k, b in self.badges:
            b.delete()
        self.badges.clear()
        for g in self._shown():
            b = Button(f"{g.icon} {tr(g.tr)}", lambda k=g.key: self._toggle(k),
                       self.batch, self.g_mid, self.g_text,
                       accent=theme.GOLD, font_size=11)
            b.set_visible(self.visible)
            self.badges.append((g.key, b))
        self._refresh()

    def _refresh(self) -> None:
        for key, b in self.badges:
            b.set_selected(key in self.selected)
        self.clear_btn.enabled = bool(self.selected)
        shown = bool(self.badges)
        self.empty.text = "" if shown else tr("GENRE_NO_MATCH")
        n = len(self.selected)
        self.hint.text = (tr("GENRE_ALL_HINT") if n == 0
                          else tr("GENRE_COUNT", n=n))

    # ------------------------------------------------------------------ #
    def layout(self, width: int, height: int, s: float = 1.0) -> None:
        self.width, self.height = width, height
        if s:
            self._s = s
        s = self._s
        cx, cy = width / 2, height / 2
        pw = min(width - 60 * s, 760 * s)
        rows = max(1, (len(self.badges) + COLS - 1) // COLS)
        bw = (pw - 40 * s - (COLS - 1) * 8 * s) / COLS
        bh = 30 * s
        grid_h = rows * bh + (rows - 1) * 8 * s
        ph = min(height - 60 * s, 150 * s + grid_h)
        self.px, self.py, self.pw, self.ph = cx - pw / 2, cy - ph / 2, pw, ph
        self._dim.width, self._dim.height = width, height
        self._panel.x, self._panel.y = self.px, self.py
        self._panel.width, self._panel.height = self.pw, self.ph

        top = self.py + self.ph
        self.title.x, self.title.y = cx, top - 24 * s
        self.search.set_scale(s)
        self.search.set_rect(self.px + 20 * s, top - 68 * s,
                             pw - 40 * s, 30 * s)
        gy = top - 84 * s
        for i, (_k, b) in enumerate(self.badges):
            r, c = divmod(i, COLS)
            b.set_scale(s)
            b.set_rect(self.px + 20 * s + c * (bw + 8 * s),
                       gy - r * (bh + 8 * s) - bh, bw, bh)
        self.empty.x, self.empty.y = cx, gy - 20 * s
        self.hint.x, self.hint.y = cx, self.py + 58 * s
        self.clear_btn.set_scale(s)
        self.done_btn.set_scale(s)
        self.clear_btn.set_rect(cx - 170 * s, self.py + 18 * s, 160 * s,
                                28 * s)
        self.done_btn.set_rect(cx + 10 * s, self.py + 18 * s, 160 * s, 28 * s)

    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> bool:
        """True when the click belongs to the picker (always, while open)."""
        if not self.visible:
            return False
        self.search.on_mouse_press(x, y, button, modifiers)
        for b in (self.clear_btn, self.done_btn):
            if b.enabled and b.contains(x, y):
                b.click()
                return True
        for _k, b in self.badges:
            if b.contains(x, y):
                b.click()
                return True
        # A click on the backdrop closes and keeps the choice — the same as
        # Done, because there is nothing here to cancel.
        if not (self.px <= x <= self.px + self.pw
                and self.py <= y <= self.py + self.ph):
            self._done()
        return True

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        if not self.visible:
            return
        for b in (self.clear_btn, self.done_btn):
            b.set_hover(b.contains(x, y))
        for _k, b in self.badges:
            b.set_hover(b.contains(x, y))

    def on_text(self, text: str) -> bool:
        return bool(self.visible and self.search.on_text(text))

    def on_text_motion(self, motion) -> bool:
        return bool(self.visible and self.search.on_text_motion(motion))

    def on_key_press(self, symbol, modifiers) -> bool:
        if not self.visible:
            return False
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self._done()
            return True
        if self.search.on_key_press(symbol, modifiers):
            return True
        return True          # swallow everything else while open

    def draw(self) -> None:
        if not self.visible:
            return
        self.batch.draw()

    def delete(self) -> None:
        for _k, b in self.badges:
            b.delete()
        self.badges.clear()
        self.clear_btn.delete()
        self.done_btn.delete()
        self.search.delete()
        self._dim.delete()
        self._panel.delete()
