"""The "update ready" strip, drawn over whatever scene is showing.

This used to live inside :class:`~kanjire.ui.scenes.menu.MenuScene`, which meant
a staged update was only ever announced on the Play tab: a player sitting in
Stats, Journey, Reading or Settings never saw it. It's owned by the app now and
drawn on top of every scene, so it can't be missed - and there's exactly one of
it, rather than a copy per scene that would each have to be kept in sync.

Scenes don't have to do anything to get it. A scene whose own content reaches
the bottom of the window can ask :meth:`height` how much room the banner takes
and shift up by it (the menu does).
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


class UpdateBanner:
    def __init__(self, app) -> None:
        self.app = app
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self._parts: dict | None = None
        self.buttons: list[Button] = []
        self._s = 1.0

    # ---- lifecycle ----------------------------------------------------- #
    @property
    def visible(self) -> bool:
        return self._parts is not None

    def sync(self) -> bool:
        """Build/tear down to match the updater. True if it just changed."""
        want = self.app.updater.banner_visible
        if want and self._parts is None:
            self._build()
            return True
        if not want and self._parts is not None:
            self.destroy()
            return True
        return False

    def height(self) -> float:
        """Room the banner occupies at the bottom (0 when hidden), so a scene
        can lift its own footer clear of it."""
        if not self._parts:
            return 0.0
        return (56 if self._parts.get("notes") else 40) * self._s + 22 * self._s

    def _build(self) -> None:
        info = self.app.updater.info
        writable = self.app.updater.can_apply()
        bg = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2,
            color=theme.PANEL,
            border_color=theme.SUCCESS if writable else theme.DANGER,
            batch=self.batch, group=self.g_bg,
        )
        text = tr("UPDATE_BANNER", version=info.version if info else "")
        if not writable:
            text = f"{text}  ·  {tr('UPDATE_NOWRITE')}"
        label = Label(
            text, font_name=JP_FONT, font_size=13,
            color=theme.with_alpha(theme.TEXT, 255),
            anchor_x="left", anchor_y="center",
            batch=self.batch, group=self.g_text,
        )
        # Second line: the release notes from the signed manifest, so players
        # see what's new without opening anything.
        notes_txt = _format_notes(info.notes if info else "") if writable else ""
        notes = None
        if notes_txt:
            notes = Label(
                notes_txt, font_name=JP_FONT, font_size=11,
                color=theme.with_alpha(theme.MUTED, 255),
                anchor_x="left", anchor_y="center",
                batch=self.batch, group=self.g_text,
            )
        restart = Button(tr("UPDATE_RESTART"), self._apply, self.batch,
                         self.g_bg, self.g_text, accent=theme.SUCCESS,
                         font_size=12)
        if not writable:
            restart.set_enabled(False)
        later = Button(tr("UPDATE_LATER"), self._dismiss, self.batch,
                       self.g_bg, self.g_text, accent=theme.DIM, font_size=12)
        self._parts = {"bg": bg, "label": label, "notes": notes,
                       "restart": restart, "later": later}
        self.buttons = [restart, later]
        self.layout()

    def destroy(self) -> None:
        if not self._parts:
            return
        self._parts["bg"].delete()
        self._parts["label"].delete()
        if self._parts.get("notes"):
            self._parts["notes"].delete()
        for b in self.buttons:
            b.delete()
        self._parts = None
        self.buttons = []

    # ---- layout / draw -------------------------------------------------- #
    def layout(self) -> None:
        if not self._parts:
            return
        width, height = self.app.window.width, self.app.window.height
        s = scale_for(width, height)
        self._s = s
        notes = self._parts.get("notes")
        pad = 16 * s
        h = (56 if notes else 40) * s
        y = 14 * s
        w = width - 2 * pad
        bg = self._parts["bg"]
        bg.x, bg.y, bg.width, bg.height = pad, y, w, h
        cy = y + h / 2
        text_x = pad + 16 * s
        label = self._parts["label"]
        label.x = text_x
        label.font_size = max(9, round(13 * s))
        if notes:
            label.y = cy + 11 * s
            notes.x = text_x
            notes.y = cy - 11 * s
            notes.font_size = max(8, round(11 * s))
        else:
            label.y = cy
        rb_w, lb_w, bh, gap = 168 * s, 92 * s, 28 * s, 10 * s
        restart, later = self._parts["restart"], self._parts["later"]
        restart.set_scale(s)
        later.set_scale(s)
        rx = pad + w - 16 * s - rb_w
        restart.set_rect(rx, cy - bh / 2, rb_w, bh)
        later.set_rect(rx - gap - lb_w, cy - bh / 2, lb_w, bh)

    def draw(self) -> None:
        if self._parts:
            self.batch.draw()

    # ---- input (the app gives the banner first refusal) ----------------- #
    def on_mouse_press(self, x, y, button, modifiers) -> bool:
        """True if the click was ours - the scene beneath must not also get it."""
        if not self._parts:
            return False
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return True
        # Swallow clicks anywhere on the strip: the scene underneath must not
        # react to a click on a panel that's covering it.
        bg = self._parts["bg"]
        return (bg.x <= x <= bg.x + bg.width
                and bg.y <= y <= bg.y + bg.height)

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    # ---- actions --------------------------------------------------------- #
    def _apply(self) -> None:
        if self.app.updater.apply():
            # run()'s finally closes the DB/audio so the swap can take the lock.
            pyglet.app.exit()

    def _dismiss(self) -> None:
        self.app.updater.dismiss()
        self.destroy()
        self.app.on_banner_changed()


def _format_notes(raw: str) -> str:
    """Flatten changelog bullets into one compact, truncated banner line."""
    parts = [ln.strip().lstrip("-*• ").strip() for ln in (raw or "").splitlines()]
    parts = [p for p in parts if p]
    s = "  ·  ".join(parts)
    return (s[:116] + "…") if len(s) > 117 else s
