"""Friend invites and join requests, shown wherever you happen to be.

An overlay like the update banner (top-right instead of bottom), so an invite
reaches you in the Reading Room or halfway through Stats - not only if you
happen to be sitting on the multiplayer screen. One card at a time; the rest
queue behind it.
"""
from __future__ import annotations

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.metrics import scale_for
from kanjire.ui.widgets.button import Button


class InviteToast:
    def __init__(self, app) -> None:
        self.app = app
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.queue: list[dict] = []
        self.current: dict | None = None
        self._parts: dict | None = None
        self.buttons: list[Button] = []
        self._s = 1.0

    @property
    def visible(self) -> bool:
        return self._parts is not None

    # ---- intake --------------------------------------------------------- #
    def push(self, msg: dict) -> None:
        """A friend invited us, or asked to join us."""
        self.queue.append(msg)
        if self.current is None:
            self._next()

    def _next(self) -> None:
        self._destroy()
        self.current = self.queue.pop(0) if self.queue else None
        if self.current is not None:
            self._build()

    # ---- widgets --------------------------------------------------------- #
    def _text(self) -> str:
        m = self.current or {}
        who = m.get("name") or "?"
        if m.get("type") == "invite":
            return tr("FR_INVITE_MSG", name=who)
        return tr("FR_REQUEST_MSG", name=who)

    def _build(self) -> None:
        m = self.current or {}
        accent = theme.SUCCESS if m.get("type") == "invite" else theme.GOLD
        bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2, color=theme.PANEL, border_color=accent,
            batch=self.batch, group=self.g_bg)
        label = Label(self._text(), font_name=JP_FONT, font_size=13,
                      color=theme.with_alpha(theme.TEXT, 255),
                      anchor_x="left", anchor_y="center",
                      batch=self.batch, group=self.g_text)
        yes = Button(tr("FR_ACCEPT"), self._accept, self.batch, self.g_bg,
                     self.g_text, accent=accent, font_size=12)
        no = Button(tr("FR_DECLINE"), self._decline, self.batch, self.g_bg,
                    self.g_text, accent=theme.DIM, font_size=12)
        self._parts = {"bg": bg, "label": label}
        self.buttons = [yes, no]
        self.layout()

    def _destroy(self) -> None:
        if self._parts:
            self._parts["bg"].delete()
            self._parts["label"].delete()
        for b in self.buttons:
            b.delete()
        self._parts = None
        self.buttons = []

    def layout(self) -> None:
        if not self._parts:
            return
        w_win, h_win = self.app.window.width, self.app.window.height
        s = scale_for(w_win, h_win)
        self._s = s
        w, h = 460 * s, 84 * s
        x = w_win - w - 16 * s
        # Bottom-right, above the update strip if that's showing too. It used to
        # sit top-right, straight on top of the multiplayer friends panel - the
        # one screen where an invite is most likely to arrive.
        y = 16 * s + self.app.banner.height()
        bg = self._parts["bg"]
        bg.x, bg.y, bg.width, bg.height = x, y, w, h
        label = self._parts["label"]
        label.x = x + 16 * s
        label.y = y + h - 26 * s
        label.font_size = max(9, round(13 * s))
        bw, bh, gap = 120 * s, 30 * s, 10 * s
        yes, no = self.buttons
        yes.set_scale(s)
        no.set_scale(s)
        yes.set_rect(x + w - 16 * s - bw, y + 14 * s, bw, bh)
        no.set_rect(x + w - 16 * s - 2 * bw - gap, y + 14 * s, bw, bh)

    def draw(self) -> None:
        if self._parts:
            self.batch.draw()

    # ---- input ----------------------------------------------------------- #
    def on_mouse_press(self, x, y, button, modifiers) -> bool:
        if not self._parts:
            return False
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return True
        bg = self._parts["bg"]
        return (bg.x <= x <= bg.x + bg.width
                and bg.y <= y <= bg.y + bg.height)

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    # ---- actions ---------------------------------------------------------- #
    def _accept(self) -> None:
        m = self.current or {}
        if m.get("type") == "invite":
            # Straight into their room - the whole point is that it's one click.
            self.app.go_multiplayer(join_room=str(m.get("room") or ""))
        else:
            # They asked to join us: answer with an invite carrying our code, so
            # they don't have to be told it out loud.
            room = self.app.current_room_code()
            if room:
                self.app.friends.invite(str(m.get("from") or ""), room)
        self._next()

    def _decline(self) -> None:
        self._next()
