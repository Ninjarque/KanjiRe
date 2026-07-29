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
from kanjire.data import clusters, db
from kanjire.data.genres import GENRES
from kanjire.game import menuconfig as mc
from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import ChipRow, JPLabel, SectionLabel, ThemedButton

def _mode_label(name: str) -> str:
    """Display label: built-ins localised, custom modes verbatim."""
    key = mc.MODE_TR.get(name) or mc.PRESET_TR.get(name)
    return tr(key) if key else name


class PlayScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._presets = mc.all_presets(app.state)
        self._preset_names = {p["name"] for p in self._presets}
        # Only the player's own modes are deletable — not the factory ones.
        self._user_preset_names = {p.get("name") for p in app.state.presets}
        self.mode = app.state.last_mode or "Time Attack"
        if self.mode not in mc.MODE_TR and self.mode not in self._preset_names:
            self.mode = "Time Attack"
        self.settings = self._settings_for(self.mode)
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

        # Today's Training + streak: the daily driver sits right on top.
        plan = self._today_plan()
        streak = self._app.state.streak_status()
        today = ThemedButton(text=self._today_label(plan, streak),
                             fill=theme.GOLD, font_size=sp(14),
                             height=dp(46))
        today.disabled = plan.empty
        today.bind(on_release=lambda *_: self._play_today())
        root.add_widget(today)
        if streak["count"] > 0:
            line = (tr("STREAK_FOOTER", n=streak["count"])
                    + " ◇" * streak["freezes"]
                    + (" ○" if streak["done_today"] else ""))
            lbl = JPLabel(text=line, color=rgba(theme.GOLD),
                          font_size=sp(11.5), size_hint_y=None,
                          height=dp(18))
            root.add_widget(lbl)

        scroller = ScrollView(do_scroll_x=False, bar_width=dp(3))
        body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                          padding=[0, 0, 0, dp(8)])
        body.bind(minimum_height=body.setter("height"))

        # ---- modes: the three you start from, plus + ------------------- #
        body.add_widget(SectionLabel(text=tr("SEC_MODE")))
        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        for key in mc.FRONT_MODES:
            b = ThemedButton(
                text=_mode_label(key), font_size=sp(15), height=dp(46),
                fill=theme.ACCENT if key == self.mode else theme.PANEL_HI)
            b.bind(on_release=lambda w, k=key: self._set_mode(k))
            grid.add_widget(b)
        add = ThemedButton(text=tr("BTN_NEW_MODE"), font_size=sp(15),
                           height=dp(46), fill=theme.PANEL_HI,
                           text_color=theme.SUCCESS)
        add.bind(on_release=lambda *_: self._new_mode())
        grid.add_widget(add)
        # Factory modes then the player's own — gold, one row down.
        for n in mc.second_row_modes(self._app.state):
            b = ThemedButton(
                text=_mode_label(n), font_size=sp(13), height=dp(40),
                fill=theme.GOLD if n == self.mode else theme.PANEL_HI,
                text_color=None if n == self.mode else theme.GOLD)
            b.bind(on_release=lambda w, k=n: self._set_mode(k))
            grid.add_widget(b)
        body.add_widget(grid)
        if self.mode in self._user_preset_names:
            # Custom modes are deletable; the factory ones are not. (Phones
            # have no right-click, so this button is the only way in.)
            rm = ThemedButton(text=tr("BTN_DELETE_MODE"), font_size=sp(13),
                              height=dp(38), fill=theme.PANEL_HI,
                              text_color=theme.DANGER)
            rm.bind(on_release=lambda *_: self._delete_mode())
            body.add_widget(rm)

        s = self.settings

        # ---- deck ------------------------------------------------------ #
        def deck_label(d: str) -> str:
            if d == kana.KANA_DECK:
                return tr("DECK_KANA")
            if d == "jlpt":
                return "JLPT"
            if d.startswith("corpus:"):
                return d[len("corpus:"):].replace("-", " ").title()
            return d
        body.add_widget(SectionLabel(text=tr("SEC_DECK")))
        body.add_widget(ChipRow(
            [(d, deck_label(d)) for d in self._decks],
            s["decks"], multi=True,
            on_change=self._set_decks))

        # ---- levels (jlpt only) ---------------------------------------- #
        if "jlpt" in s["decks"]:
            body.add_widget(SectionLabel(text=tr("SEC_LEVEL")))
            body.add_widget(self._chips(
                [(lv, f"N{lv}") for lv in mc.LEVELS], s["levels"],
                "levels", multi=True))

        # ---- kana controls (take the level row's spot, like desktop) ---- #
        if kana.KANA_DECK in s["decks"]:
            body.add_widget(SectionLabel(text=tr("SEC_KANA_LENGTH")))
            body.add_widget(self._chips(
                [(n, str(n)) for n in mc.KANA_LENGTHS],
                s["kana_length"], "kana_length"))
            # Two toggles, like the card faces: both on = the paired
            # hira ↔ kata matching deck ("both").
            body.add_widget(SectionLabel(text=tr("SEC_KANA_SCRIPT")))
            sel = (["hira", "kata"] if s["kana_script"] == "both"
                   else [s["kana_script"]])
            body.add_widget(ChipRow(
                [("hira", tr("KANA_SCRIPT_HIRA")),
                 ("kata", tr("KANA_SCRIPT_KATA"))],
                sel, multi=True, min_selected=1,
                on_change=self._set_kana_scripts))

        # ---- board size ------------------------------------------------ #
        body.add_widget(SectionLabel(text=tr("SEC_WORDS")))
        body.add_widget(self._chips(
            [(n, str(n)) for n in mc.SIZES], s["board_size"], "board_size"))

        ruleset = self._ruleset()
        is_recall = ruleset == "Recall"

        # ---- faces: one colour-coded toggle per card face --------------- #
        # (board modes only — recall has no cards to split into faces, and
        # the kana deck derives its faces from the script choice instead)
        from kanjire.kivyui.widgets import ChipGrid
        if not is_recall and kana.KANA_DECK not in s["decks"]:
            body.add_widget(SectionLabel(text=tr("SEC_CARDS")))
            body.add_widget(ChipGrid(
                [(f, tr(k)) for f, k in mc.FACE_OPTIONS],
                s["faces"], cols=2, multi=True, min_selected=2,
                colors={f: theme.FACE_COLORS[f] for f, _ in mc.FACE_OPTIONS},
                on_change=lambda v: self._set(
                    "faces", [f for f in mc.FACE_ORDER if f in v])))

        # ---- unified board rows (every board mode, desktop parity) ------ #
        if not is_recall:
            body.add_widget(SectionLabel(text=tr("SEC_FONTS")))
            body.add_widget(self._chips(
                [(False, tr("FONT_SINGLE")), (True, tr("FONT_RANDOM"))],
                s["random_fonts"], "random_fonts"))
            body.add_widget(SectionLabel(text=tr("SEC_WRITING")))
            body.add_widget(self._chips(
                [(v, tr(k)) for v, k in mc.WRITING_OPTIONS],
                s["vertical_writing"], "vertical_writing"))
            body.add_widget(SectionLabel(text=tr("SEC_PASSES")))
            body.add_widget(self._chips(
                [(n, f"×{n}") for n in mc.REPEAT_OPTIONS],
                s["repetitions"], "repetitions"))
        # Knowledge-mix dials: they shape sampling in every mode.
        for skey, tkey in (("learn_known", "SEC_KNOWN"),
                           ("learn_less_known", "SEC_LESS_KNOWN"),
                           ("learn_unknown", "SEC_UNKNOWN")):
            body.add_widget(SectionLabel(text=tr(tkey)))
            body.add_widget(self._chips(
                [(n, "○" if n == 0 else "●" * n) for n in mc.LEARN_STEPS],
                s[skey], skey))

        # ---- clustering: genre filter + the three affinity dials -------- #
        if clusters.available() and kana.KANA_DECK not in s["decks"]:
            body.add_widget(SectionLabel(text=tr("ROW_GENRE")))
            body.add_widget(ChipGrid(
                [(g.key, f"{g.icon} {tr(g.tr)}") for g in GENRES],
                s["genres"], cols=2, multi=True,
                on_change=lambda v: self._set("genres", list(v))))
            for skey, tkey in (("aff_meaning", "AFF_MEANING"),
                               ("aff_looks", "AFF_LOOKS"),
                               ("aff_sound", "AFF_SOUND")):
                body.add_widget(SectionLabel(text=tr(tkey)))
                body.add_widget(self._chips(
                    [(n, "○" if n == 0 else "●" * n)
                     for n in mc.AFFINITY_STEPS_UI],
                    s[skey], skey))
        if ruleset == "Survival":
            body.add_widget(SectionLabel(text=tr("SEC_HEARTS")))
            body.add_widget(self._chips(
                [(n, "♥" * n) for n in mc.HEARTS_OPTIONS],
                s["start_hearts"], "start_hearts"))
            body.add_widget(SectionLabel(text=tr("SEC_BOUNTY")))
            body.add_widget(self._chips(
                [(v, tr(k)) for v, k in mc.BOUNTY_OPTIONS],
                s["bounty_freq"], "bounty_freq"))
        if is_recall:
            body.add_widget(SectionLabel(text=tr("SEC_RECALL_PROMPT")))
            # Five options: wrap over two rows (one row clips at phone width).
            body.add_widget(ChipGrid(
                [("typed", tr("RECALL_P_TYPED")),
                 ("listen", tr("RECALL_P_LISTEN")),
                 ("both", tr("RECALL_P_BOTH")),
                 ("choice", tr("RECALL_P_CHOICE")),
                 ("mixed", tr("RECALL_P_MIXED"))],
                s["recall_prompt"], cols=2,
                on_change=lambda v: self._set("recall_prompt", v)))
            body.add_widget(SectionLabel(text=tr("SEC_RECALL_PREVIEW")))
            body.add_widget(self._chips(
                [(True, tr("OPT_ON")), (False, tr("OPT_OFF"))],
                s["recall_preview"], "recall_preview"))
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
    def _preset_for(self, name: str) -> dict | None:
        return next((p for p in self._presets if p["name"] == name), None)

    def _ruleset(self) -> str:
        """The effective ruleset behind the selected mode/preset — a preset
        saved from Survival or Recall keeps that ruleset's option rows."""
        if self.mode in mc.MODE_TR:
            return self.mode
        p = self._preset_for(self.mode) or {}
        if p.get("recall_mode"):
            return "Recall"
        if p.get("lives_mode"):
            return "Survival"
        return "Zen"

    def _settings_for(self, mode: str) -> dict:
        """The mode's saved settings — seeded from the preset's own values
        the first time a preset is selected (so its rows show its identity,
        not the defaults)."""
        saved = self._app.state.last_for_mode(mode)
        if saved is None and mode not in mc.MODE_TR:
            p = self._preset_for(mode)
            if p:
                saved = mc.preset_overlay(p)
        return mc.normalized_settings(saved)

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._app.state.set_last_mode(mode)
        self.settings = self._settings_for(mode)
        self._build()

    def _set(self, key: str, value) -> None:
        self.settings[key] = value
        self._app.state.set_last_for_mode(self.mode, dict(self.settings))
        if key == "levels":
            self._today_cache = None   # Today's pool is scoped by these

    def _set_decks(self, values) -> None:
        """Multi-select decks; the generative kana deck stays exclusive."""
        prev = list(self.settings["decks"])
        vals = [v for v in (values or []) if v]
        if not vals:
            self._build()              # can't go empty — re-sync the chips
            return
        added = [v for v in vals if v not in prev]
        if kana.KANA_DECK in added:
            vals = [kana.KANA_DECK]    # picking kana clears DB decks…
        elif kana.KANA_DECK in vals and len(vals) > 1:
            vals = [v for v in vals if v != kana.KANA_DECK]  # …and back
        self.settings["decks"] = vals
        self.settings["deck"] = vals[0]   # legacy mirror (older clients)
        self._app.state.set_last_for_mode(self.mode, dict(self.settings))
        self._today_cache = None
        self._build()                  # deck mix changes which rows exist

    def _set_kana_scripts(self, values) -> None:
        vs = set(values or ())
        val = ("both" if vs >= {"hira", "kata"}
               else "kata" if "kata" in vs else "hira")
        self._set("kana_script", val)

    # -- custom modes ---------------------------------------------------- #
    def _new_mode(self) -> None:
        """Turn the settings on screen into a named mode of the player's own."""
        def save(name: str) -> None:
            name = (name or "").strip()
            # Don't let a custom mode shadow a built-in one: config_for
            # resolves built-ins first, so the custom one would never load.
            if not name or name in mc.MODE_TR or name in mc.FACTORY_MODES:
                return
            cfg = mc.config_for(self.mode, self.settings)
            self._app.state.save_preset(mc.preset_from_config(cfg, name))
            self._refresh_presets()
            self._set_mode(name)

        self._app.prompt(tr("PRESET_PROMPT"), save,
                         initial=f"My {_mode_label(self.mode)}")

    def _delete_mode(self) -> None:
        name = self.mode
        if name not in self._user_preset_names:
            return

        def apply() -> None:
            self._app.state.delete_preset(name)
            self._refresh_presets()
            self._set_mode("Time Attack")

        self._app.confirm(tr("DELETE_PRESET_MSG", name=name), apply,
                          danger=True)

    def _refresh_presets(self) -> None:
        self._presets = mc.all_presets(self._app.state)
        self._preset_names = {p["name"] for p in self._presets}
        self._user_preset_names = {p.get("name")
                                   for p in self._app.state.presets}

    def _play(self) -> None:
        cfg = mc.config_for(self.mode, self.settings)
        self._app.go_game(cfg)

    # ------------------------------------------------------------------ #
    # Today's Training (mirrors the pyglet menu)
    # ------------------------------------------------------------------ #
    def _today_plan(self):
        if getattr(self, "_today_cache", None) is None:
            from kanjire.srs.session import TodayPlan, build_today_plan
            s = self.settings
            decks = (None if kana.KANA_DECK in s["decks"]
                     else list(s["decks"]))
            levels = sorted(s["levels"]) if "jlpt" in s["decks"] else None
            try:
                self._today_cache = build_today_plan(
                    self._app.con, self._app.stats, decks=decks,
                    levels=levels)
            except Exception:
                self._today_cache = TodayPlan()
        return self._today_cache

    @staticmethod
    def _today_label(plan, streak) -> str:
        if plan.empty:
            return tr("TODAY_DONE")
        if plan.comeback:
            return tr("TODAY_COMEBACK", n=len(plan.reviews))
        if streak["done_today"]:
            return tr("TODAY_MORE", rev=len(plan.reviews),
                      new=len(plan.new_words))
        return tr("BTN_TODAY", rev=len(plan.reviews),
                  new=len(plan.new_words))

    def _play_today(self) -> None:
        from kanjire.game.config import DEFAULT_FACES, GameConfig
        plan = self._today_plan()
        if plan.empty:
            return
        s = self.settings
        decks = [d for d in s["decks"] if d != kana.KANA_DECK] or ["jlpt"]
        cfg = GameConfig(
            name="Today",
            decks=tuple(decks),
            levels=(), faces=DEFAULT_FACES,
            words_per_round=min(6, max(2, len(plan.pool))),
            duration=None, max_mistakes=None, mismatch_penalty=0,
            repetitions=1, session_mode=True,
        )
        self._today_cache = None   # replay rebuilds tomorrow's picture
        # The hardest few reviews come back as a typed-recall epilogue
        # (plan.reviews is already most-at-risk-first).
        self._app.go_game(cfg, pool=plan.pool,
                          recall_words=plan.reviews[:8])
