"""The after-match example sentence, sized by the player's setting.

``sentence_display`` (UserState setting): ``off`` | ``default`` | ``big``.
Default = the slim bottom strip; big = a centred card with large text shown
50% longer. Tap dismisses either. Used by the solo board AND multiplayer
(each client resolves the sentence locally from the matched word — the
lookup is deterministic, so everyone reads the same one).
"""
from __future__ import annotations

from kivy.clock import Clock
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout

from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel

DEFAULT_SECONDS = 5.0
BIG_SECONDS = 7.5   # 50% longer — the point of "big" is having time to read


def display_mode(state) -> str:
    mode = state.setting("sentence_display", "default")
    return mode if mode in ("off", "default", "big") else "default"


class SentenceToast(ButtonBehavior, BoxLayout):
    """Overlay widget; add to a Screen and call :meth:`show`."""

    def __init__(self, app, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", [dp(16), dp(14)])
        kw.setdefault("spacing", dp(6))
        super().__init__(**kw)
        self._app = app
        self._ev = None
        self.opacity = 0
        self.disabled = True
        with self.canvas.before:
            self._bg = Color(*rgba(theme.PANEL, 0.0))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[dp(12)])
            self._bcol = Color(*rgba(theme.GOLD, 0.0))
            self._border = Line(width=dp(1.2),
                                rounded_rectangle=(0, 0, 1, 1, dp(12)))
        self.bind(pos=self._sync, size=self._sync)
        # Autoheight labels: wrap on width only, height follows the texture.
        # (Binding full size into text_size height-constrains the texture —
        # a line that wraps at the boundary then CLIPS instead of growing.)
        self._ja = JPLabel(text="", halign="center", valign="middle",
                           size_hint_y=None)
        self._en = JPLabel(text="", halign="center", valign="middle",
                           color=rgba(theme.MUTED), size_hint_y=None)
        for lbl in (self._ja, self._en):
            lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                     texture_size=lambda w, ts: setattr(w, "height",
                                                        ts[1] + dp(4)))
            lbl.bind(height=self._fit_height)
        self.add_widget(self._ja)
        self.add_widget(self._en)
        self._big = False

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border.rounded_rectangle = (
            self.x + dp(1), self.y + dp(1),
            max(2, self.width - dp(2)), max(2, self.height - dp(2)), dp(12))

    # ------------------------------------------------------------------ #
    def show(self, expression: str, reading: str) -> None:
        mode = display_mode(self._app.state)
        if mode == "off":
            return
        from kanjire.data import kanjidata
        try:
            got = kanjidata.sentences_for(expression, reading, 1)
        except Exception:
            got = []
        if not got:
            return
        ja, en = got[0]
        parent = self.parent
        if parent is None:
            return
        self._big = big = mode == "big"
        self._ja.font_size = sp(22) if big else sp(15)
        self._en.font_size = sp(14) if big else sp(11)
        self.width = min(parent.width - dp(16), dp(560))
        self._ja.text = ja
        self._en.text = en if len(en) <= 110 else en[:109] + "…"
        if big:
            self._bg.rgba = rgba(theme.PANEL, 0.96)
            self._bcol.rgba = rgba(theme.GOLD, 0.9)
        else:
            self._bg.rgba = rgba(theme.PANEL, 0.85)
            self._bcol.rgba = rgba(theme.GOLD, 0.0)
        self._fit_height()
        self.opacity = 1
        self.disabled = False
        if self._ev is not None:
            self._ev.cancel()
        self._ev = Clock.schedule_once(
            self.dismiss, BIG_SECONDS if big else DEFAULT_SECONDS)

    def _fit_height(self, *_) -> None:
        """Box height = the labels' natural (texture) heights; re-anchor."""
        parent = self.parent
        if parent is None:
            return
        self.height = (self._ja.height + self._en.height
                       + self.padding[1] * 2 + self.spacing)
        self.center_x = parent.width / 2
        if self._big:
            self.center_y = parent.height * 0.5
        else:
            self.y = dp(4)

    def dismiss(self, *_) -> None:
        self.opacity = 0
        self.disabled = True
        self._ja.text = ""
        self._en.text = ""
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def on_release(self):
        self.dismiss()
