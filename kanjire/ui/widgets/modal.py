"""In-app modal dialogs: a yes/no confirm and a single-line text prompt.

These replace the tkinter dialogs the app used to pop for "delete this preset?",
"mark a whole JLPT level as known?" and "name this preset". tkinter is not part
of a frozen PyInstaller build (and python3-tk is often absent on Linux), so
calling it crashed the app the moment a player clicked one of those buttons.

Drawn as an app-level overlay on top of every scene, so any scene can raise one
without owning dialog machinery. It is *modal*: while open it eats every click,
keypress and text event, and dims the scene behind it. Results come back through
a callback (pyglet can't block), so callers pass ``on_confirm`` / ``on_submit``
instead of reading a return value.
"""
from __future__ import annotations

from collections.abc import Callable

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.metrics import scale_for
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.textinput import TextInput


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap to *width* characters (CJK has no spaces, so also break
    a run that is itself too long)."""
    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}".strip()
            if len(cand) <= width:
                line = cand
            else:
                if line:
                    out.append(line)
                while len(word) > width:
                    out.append(word[:width])
                    word = word[width:]
                line = word
        out.append(line)
    return out


class ModalDialog:
    def __init__(self, app) -> None:
        self.app = app
        self.batch = pyglet.graphics.Batch()
        self.g_dim = OrderedGroup(0)
        self.g_panel = OrderedGroup(1)
        self.g_bg = OrderedGroup(2)
        self.g_text = OrderedGroup(3)
        self.active = False
        self._parts: dict | None = None
        self.buttons: list[Button] = []
        self.input: TextInput | None = None
        self._message = ""
        self._on_ok: Callable[[str | None], None] | None = None
        self._kind = "confirm"
        self._s = 1.0

    # ---- raising ------------------------------------------------------- #
    def ask(self, message: str, on_confirm: Callable[[], None], *,
            confirm_label: str | None = None,
            cancel_label: str | None = None,
            danger: bool = False) -> None:
        """Yes/no. *on_confirm* runs only if the player confirms."""
        self._open("confirm", message,
                   lambda val: (on_confirm() if val is not None else None),
                   confirm_label or tr("DLG_OK"),
                   cancel_label or tr("DLG_CANCEL"), danger)

    def prompt(self, message: str, on_submit: Callable[[str], None], *,
               initial: str = "", confirm_label: str | None = None) -> None:
        """Ask for a line of text. *on_submit* gets the (stripped) value; it is
        not called on cancel or when the value is empty."""
        def done(val: str | None) -> None:
            if val:
                on_submit(val.strip())
        self._open("prompt", message, done,
                   confirm_label or tr("DLG_OK"), tr("DLG_CANCEL"), False,
                   initial=initial)

    def _open(self, kind, message, on_ok, ok_label, cancel_label, danger,
              initial: str = "") -> None:
        self.close()
        self._kind = kind
        self._message = message
        self._on_ok = on_ok
        accent = theme.DANGER if danger else theme.SUCCESS

        dim = shapes.Rectangle(0, 0, 10, 10, color=theme.BG,
                               batch=self.batch, group=self.g_dim)
        dim.opacity = 200
        panel = shapes.BorderedRectangle(
            0, 0, 10, 10, border=2, color=theme.PANEL,
            border_color=theme.tint(accent, 0.2),
            batch=self.batch, group=self.g_panel)
        lines = [Label("", font_name=JP_FONT, font_size=14,
                       color=theme.with_alpha(theme.TEXT, 255),
                       anchor_x="center", anchor_y="center",
                       batch=self.batch, group=self.g_text)
                 for _ in range(6)]
        self._parts = {"dim": dim, "panel": panel, "lines": lines}

        if kind == "prompt":
            self.input = TextInput(self.batch, self.g_bg, self.g_text,
                                   self.g_text, font_size=15, placeholder="")
            self.input.set_text(initial)
            self.input.focus()

        ok = Button(ok_label, self._confirm, self.batch, self.g_bg,
                    self.g_text, accent=accent, font_size=13)
        cancel = Button(cancel_label, self._cancel, self.batch, self.g_bg,
                        self.g_text, accent=theme.DIM, font_size=13)
        self.buttons = [cancel, ok]
        self.active = True
        self.layout()

    # ---- closing ------------------------------------------------------- #
    def close(self) -> None:
        for lbl in (self._parts or {}).get("lines", []):
            lbl.delete()
        if self._parts:
            self._parts["dim"].delete()
            self._parts["panel"].delete()
        for b in self.buttons:
            b.delete()
        if self.input is not None:
            self.input.delete()
        self._parts = None
        self.buttons = []
        self.input = None
        self.active = False

    def _confirm(self) -> None:
        val = self.input.text if self.input is not None else ""
        cb = self._on_ok
        self.close()
        if cb is not None:
            cb(val if self._kind != "prompt" else (val or ""))

    def _cancel(self) -> None:
        cb = self._on_ok
        self.close()
        if cb is not None:
            cb(None)

    # ---- layout / draw -------------------------------------------------- #
    def layout(self) -> None:
        if not self._parts:
            return
        w_win, h_win = self.app.window.width, self.app.window.height
        s = scale_for(w_win, h_win)
        self._s = s
        self._parts["dim"].width = w_win
        self._parts["dim"].height = h_win

        wrapped = _wrap(self._message, 40)[:6]
        pw = min(560 * s, w_win - 60 * s)
        has_input = self.input is not None
        ph = (120 + 26 * len(wrapped) + (52 if has_input else 0)) * s
        px = (w_win - pw) / 2
        py = (h_win - ph) / 2
        panel = self._parts["panel"]
        panel.x, panel.y, panel.width, panel.height = px, py, pw, ph

        cx = px + pw / 2
        ty = py + ph - 34 * s
        for i, lbl in enumerate(self._parts["lines"]):
            if i < len(wrapped):
                lbl.text = wrapped[i]
                lbl.font_size = max(10, round(14 * s))
                lbl.x, lbl.y = cx, ty - i * 26 * s
            else:
                lbl.text = ""
        bottom = py + 16 * s
        if has_input:
            iw = pw - 48 * s
            self.input.set_scale(s)
            self.input.set_rect(px + 24 * s, bottom + 44 * s, iw, 38 * s)

        bw, bh, gap = 130 * s, 36 * s, 12 * s
        bx = cx + gap / 2
        self.buttons[1].set_scale(s)
        self.buttons[1].set_rect(bx, bottom, bw, bh)          # OK (right)
        self.buttons[0].set_scale(s)
        self.buttons[0].set_rect(cx - gap / 2 - bw, bottom, bw, bh)  # Cancel

    def draw(self) -> None:
        if self.active:
            self.batch.draw()

    # ---- input (modal: consume everything while open) ------------------- #
    def on_mouse_press(self, x, y, button, modifiers) -> bool:
        if not self.active:
            return False
        if self.input is not None:
            self.input.on_mouse_press(x, y, button, modifiers)
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return True
        return True   # swallow clicks on the backdrop - it's modal

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        if not self.active:
            return
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> bool:
        if not self.active:
            return False
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self._cancel()
        elif symbol in (key.ENTER, key.RETURN, key.NUM_ENTER):
            self._confirm()
        return True

    def on_text(self, text) -> bool:
        if not self.active:
            return False
        if self.input is not None:
            self.input.on_text(text)
        return True

    def on_text_motion(self, motion) -> bool:
        if not self.active:
            return False
        if self.input is not None:
            self.input.on_text_motion(motion)
        return True

    def on_text_motion_select(self, motion) -> bool:
        if not self.active:
            return False
        if self.input is not None:
            self.input.on_text_motion_select(motion)
        return True
