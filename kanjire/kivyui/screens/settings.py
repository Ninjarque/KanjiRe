"""Settings tab: language, theme, audio toggles, about."""
from __future__ import annotations

from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from kanjire import __version__, i18n
from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import (
    ChipRow,
    JPLabel,
    SectionLabel,
    ThemedButton,
)


class SettingsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._build()

    def _build(self) -> None:
        self.clear_widgets()
        app = self._app
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)])
        scroller = ScrollView(do_scroll_x=False, bar_width=dp(3))
        body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                          padding=[0, 0, 0, dp(10)])
        body.bind(minimum_height=body.setter("height"))

        # ---- language --------------------------------------------------- #
        body.add_widget(SectionLabel(text=tr("SEC_LANGUAGE")))
        body.add_widget(ChipRow(
            [("en", tr("LANG_EN")), ("fr", tr("LANG_FR"))],
            app.state.locale, on_change=self._set_locale))

        # ---- theme ------------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_THEME")))
        names = list(theme.PALETTES)
        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        current = theme.current_palette()
        for name in names:
            p = theme.PALETTES[name]
            b = ThemedButton(text=name, height=dp(42), font_size=sp(14),
                             fill=p["PANEL_HI"], text_color=p["TEXT"])
            if name == current:
                b.set_fill(p["ACCENT"])
            b.bind(on_release=lambda w, n=name: self._set_palette(n))
            grid.add_widget(b)
        body.add_widget(grid)

        # ---- audio ------------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_AUDIO")))
        toggles = (
            ("SET_MUTE", app.state.muted,
             lambda v: (app.state.set_muted(v), app.audio.set_muted(v))),
            ("SET_SPEAK_SELECT", app.state.tts_on_select,
             lambda v: app.state.set_audio_setting("tts_on_select", v)),
            ("SET_SPEAK_MATCH", app.state.tts_on_match,
             lambda v: app.state.set_audio_setting("tts_on_match", v)),
            ("SET_SPEAK_MISMATCH", app.state.tts_on_mismatch,
             lambda v: app.state.set_audio_setting("tts_on_mismatch", v)),
        )
        for key, value, setter in toggles:
            body.add_widget(_ToggleRow(tr(key), value, setter))

        hint = JPLabel(text=tr("SET_HINT"), color=rgba(theme.DIM),
                       font_size=sp(12), halign="left", size_hint_y=None)
        hint.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                  texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(8)))
        body.add_widget(hint)

        # ---- about ------------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_ABOUT")))
        body.add_widget(JPLabel(
            text=tr("ABOUT_VERSION", version=__version__) + "  ·  Kivy",
            color=rgba(theme.MUTED), font_size=sp(13), halign="left",
            size_hint_y=None, height=dp(24)))
        check_btn = ThemedButton(text=tr("UPDATE_CHECK"), height=dp(44),
                                 font_size=sp(13))
        check_btn.bind(on_release=lambda *_: self._check_updates())
        body.add_widget(check_btn)
        self._upd_status = JPLabel(text="", color=rgba(theme.MUTED),
                                   font_size=sp(12), halign="left",
                                   size_hint_y=None, height=dp(22))
        self._upd_status.bind(size=self._upd_status.setter("text_size"))
        body.add_widget(self._upd_status)

        scroller.add_widget(body)
        root.add_widget(scroller)
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    def _check_updates(self) -> None:
        from kivy.clock import Clock

        from kanjire import __version__
        from kanjire.update import controller as uc
        upd = self._app.updates
        if not upd.self_update_capable():
            self._upd_status.text = tr("UPDATE_MANAGED")
            return
        self._upd_status.text = tr("UPDATE_CHECKING")
        upd.maybe_start(force=True)

        def poll(_dt):
            if upd.status in (uc.CHECKING, uc.DOWNLOADING):
                return  # keep polling
            if upd.status == uc.READY and upd.info:
                self._upd_status.text = tr("UPDATE_READY",
                                           version=upd.info.version)
            elif upd.status == uc.UP_TO_DATE:
                self._upd_status.text = tr("UPDATE_UPTODATE",
                                           version=__version__)
            elif upd.status == uc.ERROR:
                self._upd_status.text = tr("UPDATE_ERROR")
            return False  # unschedule

        Clock.schedule_interval(poll, 0.5)

    def _set_locale(self, loc: str) -> None:
        self._app.state.set_locale(loc)
        i18n.set_locale(loc)
        self._app.rebuild_ui(keep="settings")

    def _set_palette(self, name: str) -> None:
        theme.apply_palette(name)
        self._app.state.set_setting("palette", name)
        Window.clearcolor = rgba(theme.BG)
        self._app.rebuild_ui(keep="settings")


class _ToggleRow(BoxLayout):
    """Label + on/off pill."""

    def __init__(self, label: str, value: bool, setter, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(44))
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self._value = bool(value)
        self._setter = setter
        lbl = JPLabel(text=label, halign="left", valign="middle",
                      font_size=sp(14))
        lbl.bind(size=lbl.setter("text_size"))
        self.add_widget(lbl)
        self._btn = ThemedButton(text="", size_hint=(None, None),
                                 size=(dp(64), dp(34)), radius=dp(17),
                                 font_size=sp(12), bold=True)
        self._btn.bind(on_release=lambda *_: self._toggle())
        self.add_widget(self._btn)
        self._paint()

    def _toggle(self) -> None:
        self._value = not self._value
        self._setter(self._value)
        self._paint()

    def _paint(self) -> None:
        if self._value:
            self._btn.set_fill(theme.SUCCESS)
            self._btn.text = "ON"
        else:
            self._btn.set_fill(theme.PANEL_HI)
            self._btn.text = "OFF"
            self._btn.color = rgba(theme.MUTED)
