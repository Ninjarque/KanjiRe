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

        # The nav lives in its OWN batch, drawn over a backdrop AFTER the
        # content: the page scrolls (wheel) and content must slide neatly
        # under the bar rather than colliding with it.
        self.nav_batch = pyglet.graphics.Batch()
        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_JOURNEY"),  lambda: self.app.go_journey()),
             (tr("NAV_READ"),     lambda: self.app.go_reading()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: self.app.go_friends()),
             (tr("NAV_SETTINGS"), lambda: None)],
            self.nav_batch, self.g_bg, self.g_text,
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
        # After-match sentence display: off / default strip / big centred
        # card shown 50% longer. Applies to every card mode, multiplayer too.
        self.lbl_sentences = self._row_label(tr("SET_SENTENCES"))
        self.sent_btns: list[tuple[str, Button]] = []

        def set_sent(v: str) -> None:
            s.set_setting("sentence_display", v)
            for val, b in self.sent_btns:
                b.set_selected(val == v)

        cur_sent = s.setting("sentence_display", "default")
        for val, key in (("off", "SENT_OFF"), ("default", "SENT_DEFAULT"),
                         ("big", "SENT_BIG")):
            b = Button(
                tr(key), lambda v=val: set_sent(v),
                self.batch, self.g_bg, self.g_text,
                accent=theme.GOLD, font_size=12,
            )
            b.set_selected(val == cur_sent)
            self.buttons.append(b)
            self.sent_btns.append((val, b))

        self._audio_rows = [
            (self.lbl_mute, self.mute_btns),
            (self.lbl_select, self.select_btns),
            (self.lbl_match, self.match_btns),
            (self.lbl_mismatch, self.mismatch_btns),
            (self.lbl_sentences, [b for _v, b in self.sent_btns]),
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
        # Enable the manual check only when this install can actually self-update
        # (a frozen bundle). pip/distro installs are updated by their manager.
        if not (update_config.updates_enabled() and self.app.updater.self_update_capable()):
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

        # DEVICE SYNC panel — pair devices, sync now, unlink.
        self.sync_panel = self._panel(tr("SEC_SYNC"), accent=theme.SUCCESS)
        self.lbl_sync_status = self._row_label(tr("SYNC_NOT_LINKED"))
        self.lbl_sync_code = Label(
            "", font_name=JP_FONT, font_size=22, bold=True,
            color=theme.with_alpha(theme.GOLD, 255),
            anchor_x="left", anchor_y="center",
            batch=self.batch, group=self.g_text,
        )
        self.sync_host_btn = Button(
            tr("SYNC_SHOW_CODE"), self._sync_show_code,
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=12,
        )
        self.sync_join_btn = Button(
            tr("SYNC_ENTER_CODE"), self._sync_enter_code,
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=12,
        )
        self.sync_now_btn = Button(
            tr("SYNC_NOW"), self._sync_push,
            self.batch, self.g_bg, self.g_text,
            accent=theme.SUCCESS, font_size=12,
        )
        self.sync_unlink_btn = Button(
            tr("SYNC_UNLINK"), self._sync_unlink,
            self.batch, self.g_bg, self.g_text,
            accent=theme.DANGER, font_size=12,
        )
        self.sync_btns = [self.sync_host_btn, self.sync_join_btn,
                          self.sync_now_btn, self.sync_unlink_btn]
        self.buttons.extend(self.sync_btns)
        self._sync_refresh()

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
    # -- device sync ---------------------------------------------------- #
    def _sync_refresh(self) -> None:
        sync = self.app.sync
        if sync._pair_code and not sync._join_waiting:
            self.lbl_sync_code.text = (tr("SYNC_CODE_IS") + "  "
                                       + sync._pair_code)
            self.sync_host_btn.set_text(tr("SYNC_CANCEL_CODE"))
        else:
            self.lbl_sync_code.text = ""
            self.sync_host_btn.set_text(tr("SYNC_SHOW_CODE"))
        if sync.linked:
            last = self.app.state.setting("sync_last", "")
            text = (tr("SYNC_LINKED", when=last) if last
                    else tr("SYNC_LINKED_NEVER"))
        else:
            text = tr("SYNC_NOT_LINKED")
        if sync.status:
            text += f"   ·   {sync.status}"
        self.lbl_sync_status.text = text
        for b in (self.sync_now_btn, self.sync_unlink_btn):
            b.enabled = sync.linked
            b._refresh()

    def _sync_show_code(self) -> None:
        sync = self.app.sync
        if sync._pair_code and not sync._join_waiting:
            sync.cancel_pairing()
        else:
            try:
                sync.start_pairing()
            except Exception as exc:  # noqa: BLE001 — surfaced in the row
                sync.status = str(exc)
        self._sync_refresh()

    def _sync_enter_code(self) -> None:
        def submit(code: str) -> None:
            err = self.app.sync.join(code)
            if err:
                self.app.sync.status = err
            self._sync_refresh()

        self.app.prompt(tr("SYNC_PROMPT_CODE"), submit)

    def _sync_push(self) -> None:
        sync = self.app.sync
        if not sync.connected:
            sync.connect()
        sync.push_soon()

    def _sync_unlink(self) -> None:
        self.app.confirm(
            tr("SYNC_UNLINK_ASK"),
            lambda: (self.app.sync.unlink(), self._sync_refresh()),
            danger=True)

    def _check_updates(self) -> None:
        self.app.updater.maybe_start(force=True)

    def _status_text(self) -> str:
        if not update_config.updates_enabled():
            return tr("UPDATE_DISABLED")
        u = self.app.updater
        if not u.self_update_capable():
            return tr("UPDATE_MANAGED")
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
        # And the sync section (pairing / merge results arrive async).
        self._sync_wait = getattr(self, "_sync_wait", 0.0) + dt
        if self._sync_wait >= 1.0 and hasattr(self, "lbl_sync_status"):
            self._sync_wait = 0.0
            self._sync_refresh()

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
    def on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        """The page grew past one screen (DEVICE SYNC panel): wheel-scroll."""
        max_off = getattr(self, "_scroll_max", 0)
        if max_off <= 0:
            return
        self._scroll = max(0.0, min(max_off,
                                    getattr(self, "_scroll", 0.0)
                                    - scroll_y * 60 * self._s))
        self.on_resize(self.width, self.height)

    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        cx = width / 2
        #: Content shifts up by the wheel offset; the nav stays fixed.
        off = getattr(self, "_scroll", 0.0)
        # Scale fonts from their bases.
        self.nav.set_scale(s)
        for p in self.panels:
            p.set_scale(s)
        for b in self.buttons:
            b.set_scale(s)
        for lbl in self.labels:
            lbl.font_size = max(9, round(14 * s))
        self.hint.font_size = max(8, round(11 * s))

        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)

        margin = 70 * s
        pw = width - 2 * margin
        label_x = margin + 24 * s
        row_h = 44 * s

        # --- AUDIO panel --- #
        audio_top = height - 96 * s + off
        audio_h = 52 * s + len(self._audio_rows) * row_h
        self.audio_panel.set_rect(margin, audio_top - audio_h, pw, audio_h)
        ry = audio_top - 50 * s
        for lbl, btns in self._audio_rows:
            lbl.x, lbl.y = label_x, ry
            # Right-align each row whatever its button count (toggles have
            # 2, the sentence selector 3).
            bx0 = margin + pw - 24 * s - (len(btns) * 74 * s - 4 * s)
            for i, b in enumerate(btns):
                b.set_rect(bx0 + i * 74 * s, ry - 14 * s, 70 * s, 28 * s)
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

        # --- DEVICE SYNC panel --- #
        ys = ya - about_h - 24 * s
        sync_h = 52 * s + 2 * row_h + 20 * s
        self.sync_panel.set_rect(margin, ys - sync_h, pw, sync_h)
        sr = ys - 50 * s
        self.lbl_sync_status.x, self.lbl_sync_status.y = label_x, sr
        self.lbl_sync_code.font_size = max(11, round(20 * s))
        self.lbl_sync_code.x = label_x
        self.lbl_sync_code.y = sr - row_h
        sbw, sgap = 168 * s, 10 * s
        bx = margin + pw - 24 * s - 4 * sbw - 3 * sgap
        for i, b in enumerate(self.sync_btns):
            b.set_rect(bx + i * (sbw + sgap), sr - row_h - 14 * s,
                       sbw, 28 * s)

        # --- hint --- #
        y4 = ys - sync_h - 26 * s
        self.hint.x = label_x
        self.hint.y = y4
        self.hint.width = pw - 48 * s

        # How far the content overflows the window bottom (scroll range).
        content_bottom = y4 - 30 * s - off   # at zero offset
        self._scroll_max = max(0.0, -(content_bottom - 16 * s))
        if off > self._scroll_max:           # window grew: clamp + re-lay
            self._scroll = self._scroll_max
            self.on_resize(width, height)

    def draw(self) -> None:
        # Content first; then the nav strip's backdrop OVER it (the page
        # scrolls under the bar); then the nav itself.
        self.batch.draw()
        h = round(64 * getattr(self, "_s", 1.0))
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.nav_batch.draw()

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
