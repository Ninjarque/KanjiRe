"""Update banner for the Kivy UI (counterpart of ui/widgets/update_banner).

A slim bar pinned above the nav bar whenever the controller has a staged,
verified update: "Update x.y.z ready — Restart & update / Later". On
desktop, applying launches the swap helper and exits; on Android it hands
the verified APK to the system installer.
"""
from __future__ import annotations

from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout

from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import GhostWhenHidden, JPLabel, ThemedButton


class UpdateBanner(GhostWhenHidden, BoxLayout):
    def __init__(self, app, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", dp(46))
        kw.setdefault("padding", [dp(10), dp(5)])
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self._app = app
        self._shown_for = None
        self.opacity = 0
        self.disabled = True
        with self.canvas.before:
            Color(*rgba(theme.PANEL))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[dp(10)])
        self.bind(pos=self._sync, size=self._sync)

        self._label = JPLabel(text="", font_size=sp(13), halign="left",
                              valign="middle", color=rgba(theme.GOLD))
        self._label.bind(size=self._label.setter("text_size"))
        self.add_widget(self._label)
        self._apply = ThemedButton(text=tr("UPDATE_RESTART"),
                                   fill=theme.SUCCESS, size_hint=(None, None),
                                   size=(dp(130), dp(36)), font_size=sp(12))
        self._apply.bind(on_release=lambda *_: self._do_apply())
        self.add_widget(self._apply)
        later = ThemedButton(text=tr("UPDATE_LATER"), size_hint=(None, None),
                             size=(dp(70), dp(36)), font_size=sp(12))
        later.bind(on_release=lambda *_: self._dismiss())
        self.add_widget(later)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def sync(self) -> None:
        """Poll the controller; show/hide accordingly. Called by the app."""
        upd = self._app.updates
        show = upd.banner_visible and upd.can_apply()
        if show and upd.info is not None:
            if self._shown_for != upd.info.version:
                self._shown_for = upd.info.version
                self._label.text = tr("UPDATE_BANNER",
                                      version=upd.info.version)
            self.opacity = 1
            self.disabled = False
        else:
            self.opacity = 0
            self.disabled = True

    def _do_apply(self) -> None:
        if self._app.updates.apply():
            # Desktop: the swap helper waits for us to exit. Android: the
            # system installer took over; exiting is equally correct.
            self._app.stop()

    def _dismiss(self) -> None:
        self._app.updates.dismiss()
        self.sync()
