"""Play tab: mode select + per-mode options + PLAY.

Persists every change through ``UserState.set_last_for_mode`` in the exact
dict shape the pyglet menu uses, so desktop and mobile pick up each other's
last-used settings, and launches configs built by the shared
:mod:`kanjire.game.menuconfig`.
"""
from __future__ import annotations

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from kanjire import kana
from kanjire.data import db
from kanjire.game import menuconfig as mc
from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import ChipRow, JPLabel, SectionLabel, ThemedButton

_MODES = list(mc.MODE_TR.items())  # [(preset key, tr key)]


class PlayScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self.mode = app.state.last_mode or "Time Attack"
        if self.mode not in mc.MODE_TR:
            self.mode = "Time Attack"
        self.settings = mc.normalized_settings(
            app.state.last_for_mode(self.mode))
        try:
            deck_rows = db.list_decks(app.con)
        except Exception:
            deck_rows = []
        self._decks = [r["name"] for r in deck_rows] or ["jlpt"]
        if kana.KANA_DECK not in self._decks:
            self._decks.append(kana.KANA_DECK)
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self.clear_widgets()
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))

        root.add_widget(JPLabel(text="KanjiRe 漢字", bold=True,
                                font_size=sp(24), size_hint_y=None,
                                height=dp(40)))

        scroller = ScrollView(do_scroll_x=False, bar_width=dp(3))
        body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                          padding=[0, 0, 0, dp(8)])
        body.bind(minimum_height=body.setter("height"))

        # ---- mode ----------------------------------------------------- #
        body.add_widget(SectionLabel(text=tr("SEC_MODE")))
        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        for key, tkey in _MODES:
            b = ThemedButton(
                text=tr(tkey), font_size=sp(15), height=dp(46),
                fill=theme.ACCENT if key == self.mode else theme.PANEL_HI)
            b.bind(on_release=lambda w, k=key: self._set_mode(k))
            grid.add_widget(b)
        body.add_widget(grid)

        s = self.settings

        # ---- deck ------------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_DECK")))
        body.add_widget(self._chips(
            [(d, tr("DECK_KANA") if d == kana.KANA_DECK else d)
             for d in self._decks],
            s["deck"], "deck"))

        # ---- levels (jlpt only) ---------------------------------------- #
        if s["deck"] == "jlpt":
            body.add_widget(SectionLabel(text=tr("SEC_LEVEL")))
            body.add_widget(self._chips(
                [(lv, f"N{lv}") for lv in mc.LEVELS], s["levels"],
                "levels", multi=True))

        # ---- board size ------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_WORDS")))
        body.add_widget(self._chips(
            [(n, str(n)) for n in mc.SIZES], s["board_size"], "board_size"))

        # ---- faces ------------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_CARDS")))
        body.add_widget(self._chips(
            [(2, "2"), (3, "3"), (4, "4")], s["face_mode"], "face_mode"))

        # ---- mode-specific rows ----------------------------------------- #
        if self.mode == "Familiarize":
            body.add_widget(SectionLabel(text=tr("SEC_PASSES")))
            body.add_widget(self._chips(
                [(n, f"×{n}") for n in mc.REPEAT_OPTIONS],
                s["repetitions"], "repetitions"))
        if self.mode == "Learn":
            for skey, tkey in (("learn_known", "SEC_KNOWN"),
                               ("learn_less_known", "SEC_LESS_KNOWN"),
                               ("learn_unknown", "SEC_UNKNOWN")):
                body.add_widget(SectionLabel(text=tr(tkey)))
                body.add_widget(self._chips(
                    [(n, "○" if n == 0 else "●" * n) for n in mc.LEARN_STEPS],
                    s[skey], skey))
        if self.mode == "Survival":
            body.add_widget(SectionLabel(text=tr("SEC_HEARTS")))
            body.add_widget(self._chips(
                [(n, "♥" * n) for n in mc.HEARTS_OPTIONS],
                s["start_hearts"], "start_hearts"))
            body.add_widget(SectionLabel(text=tr("SEC_BOUNTY")))
            body.add_widget(self._chips(
                [(v, tr(k)) for v, k in mc.BOUNTY_OPTIONS],
                s["bounty_freq"], "bounty_freq"))
        if self.mode == "Recall":
            body.add_widget(SectionLabel(text=tr("SEC_RECALL_PROMPT")))
            body.add_widget(self._chips(
                [("typed", tr("RECALL_P_TYPED")),
                 ("listen", tr("RECALL_P_LISTEN")),
                 ("mixed", tr("RECALL_P_MIXED"))],
                s["recall_prompt"], "recall_prompt"))
        if s["deck"] == kana.KANA_DECK:
            body.add_widget(SectionLabel(text=tr("SEC_KANA_LENGTH")))
            body.add_widget(self._chips(
                [(n, str(n)) for n in mc.KANA_LENGTHS],
                s["kana_length"], "kana_length"))
            body.add_widget(SectionLabel(text=tr("SEC_KANA_SCRIPT")))
            body.add_widget(self._chips(
                [("hiragana", tr("KANA_SCRIPT_HIRA")),
                 ("katakana", tr("KANA_SCRIPT_KATA")),
                 ("mixed", tr("KANA_SCRIPT_BOTH"))],
                s["kana_script"], "kana_script"))

        scroller.add_widget(body)
        root.add_widget(scroller)

        play = ThemedButton(text=tr("BTN_PLAY"), fill=theme.ACCENT,
                            font_size=sp(19), bold=True, height=dp(54))
        play.bind(on_release=lambda *_: self._play())
        root.add_widget(play)
        # 対戦 = "versus"; ⚡ has no glyph in the bundled JP fonts.
        mp = ThemedButton(text="対戦 · " + tr("MP_TITLE"), font_size=sp(14),
                          height=dp(44))
        mp.bind(on_release=lambda *_: self._app.go_multiplayer())
        root.add_widget(mp)
        self.add_widget(root)

    def _chips(self, options, selected, key, *, multi=False) -> ChipRow:
        return ChipRow(options, selected, multi=multi,
                       on_change=lambda v, k=key: self._set(k, v))

    # ------------------------------------------------------------------ #
    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._app.state.set_last_mode(mode)
        self.settings = mc.normalized_settings(
            self._app.state.last_for_mode(mode))
        self._build()

    def _set(self, key: str, value) -> None:
        self.settings[key] = value
        self._app.state.set_last_for_mode(self.mode, dict(self.settings))
        # Deck switches change which option rows exist.
        if key == "deck":
            self._build()

    def _play(self) -> None:
        cfg = mc.config_for(self.mode, self.settings)
        self._app.go_game(cfg)
