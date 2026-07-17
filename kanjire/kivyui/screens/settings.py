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
        # Back-button exit confirmation (the "never ask again" undo lives
        # here). Meaningful on Android; harmless (Esc) on desktop Kivy.
        body.add_widget(_ToggleRow(
            tr("SET_BACK_CONFIRM"),
            app.state.setting("back_confirm", "on") == "on",
            lambda v: app.state.set_setting("back_confirm",
                                            "on" if v else "off")))
        # After-match sentence display (all card modes incl. multiplayer).
        body.add_widget(SectionLabel(text=tr("SET_SENTENCES")))
        from kanjire.kivyui.sentence_toast import display_mode
        body.add_widget(ChipRow(
            [("off", tr("SENT_OFF")), ("default", tr("SENT_DEFAULT")),
             ("big", tr("SENT_BIG"))],
            display_mode(app.state),
            on_change=lambda v: app.state.set_setting("sentence_display",
                                                      v)))

        hint = JPLabel(text=tr("SET_HINT"), color=rgba(theme.DIM),
                       font_size=sp(12), halign="left", size_hint_y=None)
        hint.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                  texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(8)))
        body.add_widget(hint)

        # ---- device sync ------------------------------------------------- #
        body.add_widget(SectionLabel(text=tr("SEC_SYNC")))
        intro = JPLabel(text=tr("SYNC_INTRO"), color=rgba(theme.DIM),
                        font_size=sp(12), halign="left", size_hint_y=None)
        intro.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                   texture_size=lambda w, ts: setattr(w, "height",
                                                      ts[1] + dp(6)))
        body.add_widget(intro)
        self._sync_status = JPLabel(text="", color=rgba(theme.MUTED),
                                    font_size=sp(13), halign="left",
                                    size_hint_y=None, height=dp(22))
        self._sync_status.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(w, "height",
                                               max(dp(22), ts[1] + dp(4))))
        body.add_widget(self._sync_status)
        # Wrapped + autoheight: instruction on one line, the code alone on
        # the next — a fixed-height single line clipped after "7G…" on
        # phone-width screens.
        self._sync_code = JPLabel(text="", bold=True, font_size=sp(26),
                                  color=rgba(theme.GOLD), halign="center",
                                  size_hint_y=None, height=0)
        self._sync_code.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(
                w, "height", ts[1] + dp(8) if w.text else 0))
        body.add_widget(self._sync_code)
        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(44))
        self._sync_host = ThemedButton(text=tr("SYNC_SHOW_CODE"),
                                       fill=theme.ACCENT, font_size=sp(12.5))
        self._sync_host.bind(on_release=lambda *_: self._sync_show_code())
        self._sync_join = ThemedButton(text=tr("SYNC_ENTER_CODE"),
                                       font_size=sp(12.5))
        self._sync_join.bind(on_release=lambda *_: self._sync_enter_code())
        row.add_widget(self._sync_host)
        row.add_widget(self._sync_join)
        body.add_widget(row)
        row2 = BoxLayout(orientation="horizontal", spacing=dp(8),
                         size_hint_y=None, height=dp(40))
        self._sync_now = ThemedButton(text=tr("SYNC_NOW"), height=dp(40),
                                      font_size=sp(12.5))
        self._sync_now.bind(on_release=lambda *_: self._sync_push())
        self._sync_unlink = ThemedButton(text=tr("SYNC_UNLINK"),
                                         height=dp(40), font_size=sp(12.5),
                                         text_color=theme.DANGER)
        self._sync_unlink.bind(on_release=lambda *_: self._sync_unlink_ask())
        row2.add_widget(self._sync_now)
        row2.add_widget(self._sync_unlink)
        body.add_widget(row2)
        self._sync_refresh()

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
        # Autoheight: error diagnostics can be several lines.
        self._upd_status.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(w, "height",
                                               max(dp(22), ts[1] + dp(4))))
        body.add_widget(self._upd_status)

        scroller.add_widget(body)
        root.add_widget(scroller)
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    # Device sync
    # ------------------------------------------------------------------ #
    def _sync_refresh(self, *_) -> None:
        sync = self._app.sync
        if sync._pair_code and not sync._join_waiting:
            # Instruction and code on separate lines — the code must never
            # be the part that gets clipped on a narrow screen.
            self._sync_code.text = (tr("SYNC_CODE_IS") + "\n"
                                    + sync._pair_code)
            self._sync_host.text = tr("SYNC_CANCEL_CODE")
        else:
            self._sync_code.text = ""
            self._sync_host.text = tr("SYNC_SHOW_CODE")
        if sync.linked:
            last = self._app.state.setting("sync_last", "")
            base = (tr("SYNC_LINKED", when=last) if last
                    else tr("SYNC_LINKED_NEVER"))
            if sync.status:
                base += f"\n{sync.status}"
            self._sync_status.text = base
        else:
            self._sync_status.text = (tr("SYNC_NOT_LINKED")
                                      + (f"\n{sync.status}"
                                         if sync.status else ""))
        self._sync_now.disabled = not sync.linked
        self._sync_unlink.disabled = not sync.linked

    def _sync_watch(self) -> None:
        """Refresh the section while pairing/merging is in flight."""
        from kivy.clock import Clock
        self._sync_ticks = 0

        def poll(_dt):
            self._sync_ticks += 1
            self._sync_refresh()
            if self._sync_ticks > 600:   # pairing window is 10 min
                return False

        Clock.schedule_interval(poll, 1.0)

    def _sync_show_code(self) -> None:
        sync = self._app.sync
        if sync._pair_code and not sync._join_waiting:
            sync.cancel_pairing()
            self._sync_refresh()
            return
        try:
            sync.start_pairing()
        except Exception as exc:
            sync.status = str(exc)
        self._sync_refresh()
        self._sync_watch()

    def _sync_enter_code(self) -> None:
        from kanjire.kivyui import modal

        def submit(code):
            err = self._app.sync.join(code)
            if err:
                self._app.sync.status = err
            self._sync_refresh()
            self._sync_watch()

        modal.prompt(tr("SYNC_PROMPT_CODE"), submit)

    def _sync_push(self) -> None:
        sync = self._app.sync
        if not sync.connected:
            sync.connect()
        sync.push_soon()
        self._sync_watch()

    def _sync_unlink_ask(self) -> None:
        from kanjire.kivyui import modal
        modal.confirm(tr("SYNC_UNLINK_ASK"),
                      lambda: (self._app.sync.unlink(),
                               self._sync_refresh()),
                      danger=True)

    def _check_updates(self) -> None:
        from kivy.clock import Clock

        from kanjire import __version__
        from kanjire.update import checker
        from kanjire.update import controller as uc
        upd = self._app.updates
        if not upd.self_update_capable():
            self._upd_status.text = tr("UPDATE_MANAGED")
            return
        self._upd_status.text = tr("UPDATE_CHECKING")
        upd.maybe_start(force=True)
        self._upd_waited = 0.0

        def diag() -> str:
            # The user can't read the app-private update.log on Android —
            # show the checker's last breadcrumbs right here instead.
            lines = list(checker.RECENT)[-2:]
            return ("\n" + "\n".join(lines)) if lines else ""

        def poll(_dt):
            self._upd_waited += 0.5
            if upd.status in (uc.CHECKING, uc.DOWNLOADING):
                if self._upd_waited < 60:
                    return  # keep polling
                self._upd_status.text = tr("UPDATE_ERROR") + diag()
                return False
            if upd.status == uc.READY and upd.info:
                self._upd_status.text = tr("UPDATE_READY",
                                           version=upd.info.version)
            elif upd.status == uc.UP_TO_DATE:
                self._upd_status.text = tr("UPDATE_UPTODATE",
                                           version=__version__)
            elif upd.status == uc.ERROR:
                self._upd_status.text = (tr("UPDATE_ERROR")
                                         + f"\n{upd.error or ''}" + diag())
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
