"""Settings scene: audio toggles, language selector and a theme picker.

Lives in the Play | Stats | Settings top nav. Sections are framed in titled
:class:`Panel` cards. The THEME row switches colour palette live via
``app.apply_palette`` (which persists the choice, repaints the window
background and rebuilds this scene so every widget picks up the new colours).
"""
from __future__ import annotations

import math

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire import __version__, i18n
from kanjire.i18n import tr
from kanjire.update import config as update_config
from kanjire.update import controller as update_ctrl
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.panel import Panel
from kanjire.ui.widgets.tabs import TabBar


class SettingsScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        # Panels sit behind button backgrounds, which sit behind text.
        self.g_panel = OrderedGroup(0)
        self.g_bg = OrderedGroup(1)
        self.g_text = OrderedGroup(2)

        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_SETTINGS"), lambda: None)],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_SETTINGS"))

        self.buttons: list[Button] = []
        self.labels: list[Label] = []
        self.panels: list[Panel] = []
        self._build()

    # ------------------------------------------------------------------ #
    def _panel(self, title: str, accent=None) -> Panel:
        p = Panel(self.batch, self.g_panel, self.g_text, title=title, accent=accent)
        self.panels.append(p)
        return p

    def _row_label(self, text: str) -> Label:
        lbl = Label(
            text, font_name=JP_FONT, font_size=14,
            color=theme.with_alpha(theme.TEXT, 255),
            anchor_x="left", anchor_y="center",
            batch=self.batch, group=self.g_text,
        )
        self.labels.append(lbl)
        return lbl

    def _toggle_pair(self, getter, setter, accent) -> list[Button]:
        """Two buttons (Off | On). Clicking either updates state + visuals."""
        btns: list[Button] = []
        def set_to(v):
            setter(v)
            for x, b in zip((False, True), btns):
                b.set_selected(x == bool(getter()))
        for value, label_key in ((False, "TOGGLE_OFF"), (True, "TOGGLE_ON")):
            b = Button(
                tr(label_key),
                lambda v=value: set_to(v),
                self.batch, self.g_bg, self.g_text,
                accent=accent, font_size=12,
            )
            self.buttons.append(b)
            btns.append(b)
        for x, b in zip((False, True), btns):
            b.set_selected(x == bool(getter()))
        return btns

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        s = self.app.state

        # AUDIO panel
        self.audio_panel = self._panel(tr("SEC_AUDIO"), accent=theme.SUCCESS)
        self.lbl_mute  = self._row_label(tr("SET_MUTE"))
        self.mute_btns = self._toggle_pair(
            lambda: s.muted, self._set_muted, accent=theme.DANGER,
        )
        self.lbl_select = self._row_label(tr("SET_SPEAK_SELECT"))
        self.select_btns = self._toggle_pair(
            lambda: s.tts_on_select,
            lambda v: s.set_audio_setting("tts_on_select", v),
            accent=theme.FACE_COLORS["kanji"],
        )
        self.lbl_match = self._row_label(tr("SET_SPEAK_MATCH"))
        self.match_btns = self._toggle_pair(
            lambda: s.tts_on_match,
            lambda v: s.set_audio_setting("tts_on_match", v),
            accent=theme.SUCCESS,
        )
        self.lbl_mismatch = self._row_label(tr("SET_SPEAK_MISMATCH"))
        self.mismatch_btns = self._toggle_pair(
            lambda: s.tts_on_mismatch,
            lambda v: s.set_audio_setting("tts_on_mismatch", v),
            accent=theme.GOLD,
        )
        self._audio_rows = [
            (self.lbl_mute, self.mute_btns),
            (self.lbl_select, self.select_btns),
            (self.lbl_match, self.match_btns),
            (self.lbl_mismatch, self.mismatch_btns),
        ]

        # LANGUAGE panel
        self.lang_panel = self._panel(tr("SEC_LANGUAGE"), accent=theme.ACCENT)
        self.lbl_lang_row = self._row_label("EN / FR")
        self.lang_btns: list[Button] = []
        def set_lang(loc: str) -> None:
            if loc == self.app.state.locale:
                return
            self.app.state.set_locale(loc)
            i18n.set_locale(loc)
            self.app.go_settings()  # rebuild to pick up the new strings
        for loc, label_key in (("en", "LANG_EN"), ("fr", "LANG_FR")):
            b = Button(
                tr(label_key),
                lambda l=loc: set_lang(l),
                self.batch, self.g_bg, self.g_text,
                accent=theme.ACCENT, font_size=12,
            )
            b.set_selected(loc == self.app.state.locale)
            self.buttons.append(b)
            self.lang_btns.append(b)

        # THEME panel - one button per palette, switches live.
        self.theme_panel = self._panel(tr("SEC_THEME"), accent=theme.GOLD)
        self.theme_btns: list[Button] = []
        active = theme.current_palette()
        for name in theme.PALETTES:
            b = Button(
                name,
                lambda n=name: self.app.apply_palette(n),
                self.batch, self.g_bg, self.g_text,
                accent=theme.GOLD, font_size=12,
            )
            b.set_selected(name == active)
            self.buttons.append(b)
            self.theme_btns.append(b)

        # ABOUT panel — current version + a manual "Check for updates" button.
        self.about_panel = self._panel(tr("SEC_ABOUT"), accent=theme.ACCENT)
        self.lbl_version = self._row_label(tr("ABOUT_VERSION", version=__version__))
        self.update_btn = Button(
            tr("UPDATE_CHECK"), self._check_updates,
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=12,
        )
        if not update_config.updates_enabled():
            self.update_btn.enabled = False
            self.update_btn._refresh()
        self.buttons.append(self.update_btn)
        self.lbl_update_status = Label(
            self._status_text(),
            font_name=JP_FONT, font_size=12,
            color=theme.with_alpha(theme.DIM, 255),
            anchor_x="left", anchor_y="center",
            batch=self.batch, group=self.g_text,
        )

        # Help text
        self.hint = Label(
            tr("SET_HINT"),
            font_name=JP_FONT, font_size=11,
            color=theme.with_alpha(theme.DIM, 255),
            anchor_x="left", anchor_y="center", multiline=True, width=720,
            batch=self.batch, group=self.g_text,
        )

    def _set_muted(self, v: bool) -> None:
        if v != self.app.audio.muted:
            self.app.toggle_mute()

    # ------------------------------------------------------------------ #
    def _check_updates(self) -> None:
        self.app.updater.maybe_start(force=True)

    def _status_text(self) -> str:
        if not update_config.updates_enabled():
            return tr("UPDATE_DISABLED")
        u = self.app.updater
        if u.status == update_ctrl.CHECKING:
            return tr("UPDATE_CHECKING")
        if u.status == update_ctrl.DOWNLOADING:
            done, total = u.progress
            pct = f"  {int(100 * done / total)}%" if total else ""
            return tr("UPDATE_DOWNLOADING") + pct
        if u.status == update_ctrl.READY and u.info:
            return tr("UPDATE_READY", version=u.info.version)
        if u.status == update_ctrl.UP_TO_DATE:
            return tr("UPDATE_UPTODATE", version=__version__)
        if u.status == update_ctrl.ERROR:
            return tr("UPDATE_ERROR")
        return ""

    def update(self, dt: float) -> None:
        # Reflect the background updater's progress in the status line.
        if hasattr(self, "lbl_update_status"):
            self.lbl_update_status.text = self._status_text()

    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.nav.on_mouse_press(x, y):
            return
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                break

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self.app.go_menu()
        elif symbol == key.M:
            self.app.toggle_mute()
            muted = self.app.state.muted
            for x, b in zip((False, True), self.mute_btns):
                b.set_selected(x == muted)

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        cx = width / 2
        # Scale fonts from their bases.
        self.nav.set_scale(s)
        for p in self.panels:
            p.set_scale(s)
        for b in self.buttons:
            b.set_scale(s)
        for lbl in self.labels:
            lbl.font_size = max(9, round(14 * s))
        self.hint.font_size = max(8, round(11 * s))

        self.nav.set_rect(cx - 240 * s, height - 50 * s, 480 * s, 36 * s)

        margin = 70 * s
        pw = width - 2 * margin
        label_x = margin + 24 * s
        row_h = 44 * s

        # --- AUDIO panel --- #
        audio_top = height - 96 * s
        audio_h = 52 * s + len(self._audio_rows) * row_h
        self.audio_panel.set_rect(margin, audio_top - audio_h, pw, audio_h)
        toggle_x = margin + pw - 24 * s - 144 * s
        ry = audio_top - 50 * s
        for lbl, btns in self._audio_rows:
            lbl.x, lbl.y = label_x, ry
            for i, b in enumerate(btns):
                b.set_rect(toggle_x + i * 74 * s, ry - 14 * s, 70 * s, 28 * s)
            ry -= row_h

        # --- LANGUAGE panel --- #
        y2 = audio_top - audio_h - 24 * s
        lang_h = 52 * s + row_h
        self.lang_panel.set_rect(margin, y2 - lang_h, pw, lang_h)
        lry = y2 - 50 * s
        self.lbl_lang_row.x, self.lbl_lang_row.y = label_x, lry
        lang_x = margin + pw - 24 * s - (2 * 110 * s - 10 * s)
        for i, b in enumerate(self.lang_btns):
            b.set_rect(lang_x + i * 110 * s, lry - 14 * s, 100 * s, 28 * s)

        # --- THEME panel (4-col grid) --- #
        y3 = y2 - lang_h - 24 * s
        cols = 4
        n = len(self.theme_btns)
        rows_n = max(1, math.ceil(n / cols))
        gap = 10 * s
        bw = (pw - 48 * s - (cols - 1) * gap) / cols
        bh = 30 * s
        theme_h = 50 * s + rows_n * (bh + gap) + 4 * s
        self.theme_panel.set_rect(margin, y3 - theme_h, pw, theme_h)
        tx0 = margin + 24 * s
        ty0 = y3 - 48 * s
        for i, b in enumerate(self.theme_btns):
            r, c = divmod(i, cols)
            b.set_rect(tx0 + c * (bw + gap), ty0 - r * (bh + gap) - bh, bw, bh)

        # --- ABOUT panel --- #
        ya = y3 - theme_h - 24 * s
        about_h = 52 * s + row_h + 24 * s
        self.about_panel.set_rect(margin, ya - about_h, pw, about_h)
        ar = ya - 50 * s
        self.lbl_version.x, self.lbl_version.y = label_x, ar
        ubw = 220 * s
        self.update_btn.set_rect(margin + pw - 24 * s - ubw, ar - 14 * s, ubw, 28 * s)
        self.lbl_update_status.font_size = max(9, round(12 * s))
        self.lbl_update_status.x = label_x
        self.lbl_update_status.y = ar - row_h

        # --- hint --- #
        y4 = ya - about_h - 26 * s
        self.hint.x = label_x
        self.hint.y = y4
        self.hint.width = pw - 48 * s

    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        h = round(64 * getattr(self, "_s", 1.0))
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        self.nav.delete()
        for b in self.buttons:
            b.delete()
        for lbl in self.labels:
            lbl.delete()
        for p in self.panels:
            p.delete()
        self.lbl_update_status.delete()
        self.hint.delete()
