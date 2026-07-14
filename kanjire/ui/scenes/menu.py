"""The main menu: pick a mode, a deck, levels, and board options, then play."""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire import kana
from kanjire.data import db
from kanjire.game.config import DEFAULT_FACES, PRESETS, GameConfig
from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar

LEVELS = (5, 4, 3, 2, 1)
SIZES = (4, 6, 8, 12, 24)
#: (state value, translation key) for the WRITING toggle row.
WRITING_OPTIONS = (("off", "WRITE_HORIZ"), ("random", "WRITE_MIX"), ("all", "WRITE_VERT"))
REPEAT_OPTIONS = (1, 2, 3, 5)
#: Kana-mode controls (visible only when the "kana" deck is selected).
KANA_LENGTHS = (1, 2, 3)
KANA_SCRIPTS = (("hira", "KANA_SCRIPT_HIRA"),
                ("kata", "KANA_SCRIPT_KATA"),
                ("both", "KANA_SCRIPT_BOTH"))
#: Discrete Learn-mode bucket selector values (None / Few / Some / Many).
LEARN_STEPS = (0, 1, 2, 3)
_LEARN_LABEL_KEYS = {0: "LEARN_NONE", 1: "LEARN_FEW", 2: "LEARN_SOME", 3: "LEARN_MANY"}
#: Survival difficulty: starting hearts → inferred max hearts.
HEARTS_OPTIONS = (2, 3, 5)
_HEARTS_MAX = {2: 4, 3: 5, 5: 6}
#: Survival heart-bounty frequency (state value, translation key) → probability.
BOUNTY_OPTIONS = (("none", "BOUNTY_NONE"), ("low", "BOUNTY_LOW"),
                  ("med", "BOUNTY_MED"), ("high", "BOUNTY_HIGH"))
_BOUNTY_CHANCE = {"none": 0.0, "low": 0.35, "med": 0.6, "high": 0.9}

#: Stable English preset keys → translation keys for their displayed labels.
_MODE_TR = {
    "Time Attack": "MODE_TIME",
    "Survival":    "MODE_SURVIVAL",
    "Zen":         "MODE_ZEN",
    "Familiarize": "MODE_FAMILIAR",
    "Learn":       "MODE_LEARN",
}


def _mode_label(name: str) -> str:
    """Display label for a mode (built-in localised, custom presets verbatim)."""
    key = _MODE_TR.get(name)
    return tr(key) if key else name


def _deck_label(name: str, description: str = "") -> str:
    if name == kana.KANA_DECK:
        return tr("DECK_KANA")
    if name == "jlpt":
        return "JLPT"
    if name.startswith("corpus:"):
        return name[len("corpus:"):].replace("-", " ").title()
    return name


_PRESET_FIELDS = (
    "decks", "levels", "faces", "words_per_round", "frequency_bias",
    "duration", "max_mistakes", "base_points", "mismatch_penalty", "round_bonus",
    "repetitions", "random_fonts", "vertical_writing",
    "learn_known", "learn_less_known", "learn_unknown",
    "lives_mode", "start_lives", "max_lives", "heart_chance",
    "name",
)


def _config_to_dict(cfg: GameConfig) -> dict:
    """JSON-serialisable subset of a :class:`GameConfig` for saved presets."""
    out: dict = {}
    for f in _PRESET_FIELDS:
        v = getattr(cfg, f)
        if isinstance(v, tuple):
            v = list(v)
        out[f] = v
    return out


class MenuScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        # The update banner floats above everything else.

        # ---- state ---- #
        # Saved presets are loaded into self._user_presets below; use them to
        # validate any persisted last-mode (a deleted preset shouldn't crash
        # the menu, just fall back to the default).
        all_decks = db.list_decks(app.con)
        saved_names = {p["name"] for p in app.state.presets}
        remembered = app.state.last_mode
        if remembered and (remembered in PRESETS or remembered in saved_names):
            self.mode = remembered
        else:
            self.mode = "Time Attack"
        # Inject the synthetic "Kana" deck at the front of the deck row so it's
        # the obvious choice for someone still learning their kana.
        self.deck_rows: list[dict] = [
            {"name": kana.KANA_DECK, "kind": "kana"}
        ] + [dict(r) for r in all_decks]
        self.deck = "jlpt" if any(r["name"] == "jlpt" for r in self.deck_rows) else (
            self.deck_rows[0]["name"] if self.deck_rows else "jlpt"
        )
        self.levels: set[int] = {5}
        self.board_size = 6
        self.face_mode = 4   # 2 | 3 | 4 cards per word (4 = with romaji)
        # Visual toggles (also part of saved presets)
        self.random_fonts = False
        self.vertical_writing = "off"
        self.repetitions = 1
        # Learn-mode bucket mix (only shown when "Learn" is the active mode).
        self.learn_known = 0
        self.learn_less_known = 0
        self.learn_unknown = 0
        # Kana-mode controls (only shown when the "kana" deck is active).
        self.kana_length = 1
        self.kana_script = "both"
        # Survival difficulty (only shown when "Survival" is the active mode).
        self.start_hearts = 3
        self.bounty_freq = "low"
        # Which menu sub-tab is showing: "quick" (mode/deck/level/words) or
        # "advanced" (cards/fonts/writing/passes/learn buckets).
        self.active_subtab = "quick"

        # Snapshot of currently-saved presets (names of those are user-deletable).
        self._user_presets = list(app.state.presets)
        self._user_preset_names = {p["name"] for p in self._user_presets}

        # Today's Training plan (due reviews + new-word trickle). Computed
        # lazily and invalidated when the deck/level scope changes.
        self._today_plan = None
        self._today_dirty = True

        self.buttons: list[Button] = []
        self.section_labels: list[Label] = []
        self._build_widgets()
        self._sync_from_mode(self.mode)
        # Restore the player's last session for this mode, if any.
        last = app.state.last_for_mode(self.mode)
        if last:
            self._apply_settings(last)
        self._refresh()

    # ------------------------------------------------------------------ #
    def _section(self, text: str) -> Label:
        lbl = Label(
            text, font_name=JP_FONT, font_size=12, bold=True,
            color=theme.with_alpha(theme.MUTED, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )
        self.section_labels.append(lbl)
        return lbl

    def _btn(self, text, on_click, accent=None, font_size=14) -> Button:
        # accent defaults to theme.ACCENT, resolved here (not as a def-time
        # default) so live palette switches are honoured.
        b = Button(text, on_click, self.batch, self.g_bg, self.g_text,
                   accent=accent if accent is not None else theme.ACCENT,
                   font_size=font_size)
        self.buttons.append(b)
        return b

    def _build_widgets(self) -> None:
        # Top tab navigation: Play (active) | Stats | Settings.
        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: None),
             (tr("NAV_JOURNEY"),  lambda: self.app.go_journey()),
             (tr("NAV_READ"),     lambda: self.app.go_reading()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: self.app.go_friends()),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_PLAY"))

        # Secondary sub-tabs splitting the controls so neither half crops.
        self.subtabs = TabBar(
            [(tr("MENU_QUICK"),    lambda: self._set_subtab("quick")),
             (tr("MENU_ADVANCED"), lambda: self._set_subtab("advanced"))],
            self.batch, self.g_bg, self.g_text,
            accent=theme.GOLD, font_size=13,
        )
        self.subtabs.set_active(0 if self.active_subtab == "quick" else 1)

        self.title = Label(
            "KanjiRe", font_name=JP_FONT, font_size=52, bold=True,
            color=theme.with_alpha(theme.TEXT, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )
        self.title_kanji = Label(
            "漢字", font_name=JP_FONT, font_size=52, bold=True,
            color=theme.with_alpha(theme.ACCENT, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )
        self.subtitle = Label(
            tr("SUBTITLE"), font_name=JP_FONT, font_size=15,
            color=theme.with_alpha(theme.MUTED, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )

        self.lbl_mode = self._section(tr("SEC_MODE"))
        self.mode_btns: list[tuple[str, Button]] = [
            (m, self._btn(_mode_label(m), lambda m=m: self._set_mode(m))) for m in PRESETS
        ]
        # Append any saved custom presets.
        for p in self._user_presets:
            n = p["name"]
            self.mode_btns.append(
                (n, self._btn(n, lambda n=n: self._set_mode(n), accent=theme.GOLD))
            )
        self.lbl_deck = self._section(tr("SEC_DECK"))
        self.deck_btns = [
            (r["name"], self._btn(_deck_label(r["name"]),
                                  lambda n=r["name"]: self._set_deck(n)))
            for r in self.deck_rows
        ]
        self.import_btn = self._btn(
            tr("BTN_IMPORT_FILE"), self._open_import,
            accent=theme.FACE_COLORS["meaning"], font_size=12,
        )
        self.paste_btn = self._btn(
            tr("BTN_PASTE_TEXT"), self._open_paste,
            accent=theme.FACE_COLORS["meaning"], font_size=12,
        )
        self.lbl_level = self._section(tr("SEC_LEVEL"))
        self.level_btns = [
            (lv, self._btn(f"N{lv}", lambda lv=lv: self._toggle_level(lv),
                           accent=theme.GOLD))
            for lv in LEVELS
        ]
        self.lbl_size = self._section(tr("SEC_WORDS"))
        self.size_btns = [
            (s, self._btn(str(s), lambda s=s: self._set_size(s), accent=theme.SUCCESS))
            for s in SIZES
        ]
        self.lbl_faces = self._section(tr("SEC_CARDS"))
        self.faces_btns = [
            (2, self._btn(tr("FACES_TWO"), lambda: self._set_faces(2),
                          accent=theme.FACE_COLORS["meaning"], font_size=12)),
            (3, self._btn(tr("FACES_THREE"), lambda: self._set_faces(3),
                          accent=theme.FACE_COLORS["meaning"], font_size=12)),
            (4, self._btn(tr("FACES_FOUR"), lambda: self._set_faces(4),
                          accent=theme.FACE_COLORS["romaji"], font_size=12)),
        ]

        # --- visual / familiarization toggles --- #
        self.lbl_fonts = self._section(tr("SEC_FONTS"))
        self.font_btns = [
            (False, self._btn(tr("FONT_SINGLE"), lambda: self._set_random_fonts(False),
                              accent=theme.FACE_COLORS["kanji"], font_size=12)),
            (True,  self._btn(tr("FONT_RANDOM"), lambda: self._set_random_fonts(True),
                              accent=theme.FACE_COLORS["kanji"], font_size=12)),
        ]
        self.lbl_writing = self._section(tr("SEC_WRITING"))
        self.writing_btns = [
            (val, self._btn(tr(lab_key), lambda v=val: self._set_writing(v),
                            accent=theme.FACE_COLORS["reading"], font_size=12))
            for val, lab_key in WRITING_OPTIONS
        ]
        self.lbl_repeat = self._section(tr("SEC_PASSES"))
        self.repeat_btns = [
            (n, self._btn(f"{n}×", lambda n=n: self._set_repeat(n),
                          accent=theme.GOLD, font_size=12))
            for n in REPEAT_OPTIONS
        ]

        # Learn-mode bucket selectors (only displayed when Learn is active).
        self.lbl_known = self._section(tr("SEC_KNOWN"))
        self.lbl_less_known = self._section(tr("SEC_LESS_KNOWN"))
        self.lbl_unknown = self._section(tr("SEC_UNKNOWN"))
        self.known_btns = [
            (n, self._btn(tr(_LEARN_LABEL_KEYS[n]),
                          lambda n=n: self._set_learn("known", n),
                          accent=theme.SUCCESS, font_size=11))
            for n in LEARN_STEPS
        ]
        self.less_known_btns = [
            (n, self._btn(tr(_LEARN_LABEL_KEYS[n]),
                          lambda n=n: self._set_learn("less_known", n),
                          accent=theme.GOLD, font_size=11))
            for n in LEARN_STEPS
        ]
        self.unknown_btns = [
            (n, self._btn(tr(_LEARN_LABEL_KEYS[n]),
                          lambda n=n: self._set_learn("unknown", n),
                          accent=theme.DIM, font_size=11))
            for n in LEARN_STEPS
        ]

        # Kana training: length 1/2/3 and which script(s) appear on cards.
        # Shown only when the "Kana" deck is selected.
        self.lbl_kana_length = self._section(tr("SEC_KANA_LENGTH"))
        self.kana_length_btns = [
            (n, self._btn(f"×{n}", lambda n=n: self._set_kana_length(n),
                          accent=theme.FACE_COLORS["reading"], font_size=12))
            for n in KANA_LENGTHS
        ]
        self.lbl_kana_script = self._section(tr("SEC_KANA_SCRIPT"))
        self.kana_script_btns = [
            (val, self._btn(tr(label_key),
                            lambda v=val: self._set_kana_script(v),
                            accent=theme.FACE_COLORS["kanji"], font_size=12))
            for val, label_key in KANA_SCRIPTS
        ]

        # Survival difficulty: starting hearts + heart-bounty frequency. Shown
        # only when "Survival" is the active mode (like the Learn buckets).
        self.lbl_hearts = self._section(tr("SEC_HEARTS"))
        self.hearts_btns = [
            (n, self._btn(f"{n} ♥", lambda n=n: self._set_hearts(n),
                          accent=theme.DANGER, font_size=12))
            for n in HEARTS_OPTIONS
        ]
        self.lbl_bounty = self._section(tr("SEC_BOUNTY"))
        self.bounty_btns = [
            (val, self._btn(tr(key), lambda v=val: self._set_bounty(v),
                            accent=theme.GOLD, font_size=11))
            for val, key in BOUNTY_OPTIONS
        ]

        self.save_preset_btn = self._btn(
            tr("BTN_SAVE_PRESET"), self._save_preset_dialog,
            accent=theme.GOLD, font_size=12,
        )
        self.mp_btn = self._btn(tr("BTN_MULTIPLAYER"),
                                lambda: self.app.go_multiplayer(),
                                accent=theme.DANGER, font_size=12)

        self.play_btn = self._btn(tr("BTN_PLAY"), self._play, accent=theme.SUCCESS, font_size=20)
        # Today's Training: the daily-habit entry point (label set in _refresh).
        self.today_btn = self._btn("", self._play_today, accent=theme.GOLD,
                                   font_size=14)
        self.streak_label = Label(
            "", font_name=JP_FONT, font_size=12,
            color=theme.with_alpha(theme.GOLD, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )
        self.avail_label = Label(
            "", font_name=JP_FONT, font_size=12,
            color=theme.with_alpha(theme.DIM, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )
        self.hiscore_label = Label(
            "", font_name=JP_FONT, font_size=13,
            color=theme.with_alpha(theme.GOLD, 255),
            anchor_x="center", anchor_y="center", batch=self.batch, group=self.g_text,
        )

        # Flat per-tab widget lists, used to show/hide a whole tab at once.
        def _btns(*pairs_lists):
            return [b for pairs in pairs_lists for _v, b in pairs]
        self._quick_buttons = _btns(
            self.mode_btns, self.deck_btns, self.level_btns,
            self.kana_length_btns, self.kana_script_btns, self.size_btns,
        ) + [self.import_btn, self.paste_btn]
        self._quick_labels = [
            self.lbl_mode, self.lbl_deck, self.lbl_level,
            self.lbl_kana_length, self.lbl_kana_script, self.lbl_size,
        ]
        self._adv_buttons = _btns(
            self.faces_btns, self.font_btns, self.writing_btns,
            self.repeat_btns,
            self.known_btns, self.less_known_btns, self.unknown_btns,
            self.hearts_btns, self.bounty_btns,
        )
        self._adv_labels = [
            self.lbl_faces, self.lbl_fonts, self.lbl_writing, self.lbl_repeat,
            self.lbl_known, self.lbl_less_known, self.lbl_unknown,
            self.lbl_hearts, self.lbl_bounty,
        ]

    # ------------------------------------------------------------------ #
    # State changes
    # ------------------------------------------------------------------ #
    def _set_mode(self, m):
        self.mode = m
        # Restore the player's last-used settings for this mode if we have any,
        # otherwise fall back to the preset/built-in defaults.
        self._sync_from_mode(m)
        last = self.app.state.last_for_mode(m)
        if last:
            self._apply_settings(last)
        # Remember which mode is active so the menu opens here next launch.
        self.app.state.set_last_mode(m)
        self._refresh()
        # Re-layout: switching to/from Learn changes which rows take space.
        self.on_resize(self.width, self.height)

    def _set_subtab(self, name):
        if name not in ("quick", "advanced"):
            return
        self.active_subtab = name
        self.subtabs.set_active(0 if name == "quick" else 1)
        # on_resize re-lays the active tab, hides the other, and re-refreshes.
        self.on_resize(self.width, self.height)

    def _set_deck(self, n):
        self.deck = n
        self._today_dirty = True
        self._after_change()
        # Selecting / leaving Kana changes which rows take space.
        self.on_resize(self.width, self.height)
    def _set_size(self, s):       self.board_size = s;           self._after_change()
    def _set_faces(self, mode):   self.face_mode = int(mode);    self._after_change()
    def _set_random_fonts(self, v): self.random_fonts = bool(v); self._after_change()
    def _set_writing(self, v):    self.vertical_writing = v;     self._after_change()
    def _set_repeat(self, n):     self.repetitions = int(n);     self._after_change()

    def _set_learn(self, bucket: str, value: int) -> None:
        if bucket == "known":         self.learn_known = int(value)
        elif bucket == "less_known":  self.learn_less_known = int(value)
        elif bucket == "unknown":     self.learn_unknown = int(value)
        self._after_change()

    def _set_kana_length(self, n: int) -> None:
        self.kana_length = int(n)
        self._after_change()

    def _set_kana_script(self, v: str) -> None:
        self.kana_script = v
        self._after_change()

    def _set_hearts(self, n: int) -> None:
        self.start_hearts = int(n)
        self._after_change()

    def _set_bounty(self, v: str) -> None:
        self.bounty_freq = v
        self._after_change()

    def _after_change(self) -> None:
        """Persist current settings under the active mode, then re-render."""
        self.app.state.set_last_for_mode(self.mode, self._settings_dict())
        self._refresh()

    def _settings_dict(self) -> dict:
        return {
            "deck": self.deck,
            "levels": sorted(self.levels),
            "board_size": self.board_size,
            "face_mode": self.face_mode,
            "random_fonts": self.random_fonts,
            "vertical_writing": self.vertical_writing,
            "repetitions": self.repetitions,
            "learn_known": self.learn_known,
            "learn_less_known": self.learn_less_known,
            "learn_unknown": self.learn_unknown,
            "kana_length": self.kana_length,
            "kana_script": self.kana_script,
            "start_hearts": self.start_hearts,
            "bounty_freq": self.bounty_freq,
        }

    def _apply_settings(self, d: dict) -> None:
        deck_names = {r["name"] for r in self.deck_rows}
        if d.get("deck") in deck_names:
            self.deck = d["deck"]
        levels = [lv for lv in d.get("levels", []) if lv in LEVELS]
        if levels:
            self.levels = set(levels)
        if d.get("board_size") in SIZES:
            self.board_size = d["board_size"]
        if d.get("face_mode") in (2, 3, 4):
            self.face_mode = int(d["face_mode"])
        elif "faces3" in d:   # settings saved before the 4-card option
            self.face_mode = 3 if d["faces3"] else 2
        if "random_fonts" in d:
            self.random_fonts = bool(d["random_fonts"])
        if d.get("vertical_writing") in {v for v, _ in WRITING_OPTIONS}:
            self.vertical_writing = d["vertical_writing"]
        if d.get("repetitions") in REPEAT_OPTIONS:
            self.repetitions = int(d["repetitions"])
        if d.get("learn_known") in LEARN_STEPS:
            self.learn_known = int(d["learn_known"])
        if d.get("learn_less_known") in LEARN_STEPS:
            self.learn_less_known = int(d["learn_less_known"])
        if d.get("learn_unknown") in LEARN_STEPS:
            self.learn_unknown = int(d["learn_unknown"])
        if d.get("kana_length") in KANA_LENGTHS:
            self.kana_length = int(d["kana_length"])
        if d.get("kana_script") in {v for v, _ in KANA_SCRIPTS}:
            self.kana_script = d["kana_script"]
        if d.get("start_hearts") in HEARTS_OPTIONS:
            self.start_hearts = int(d["start_hearts"])
        if d.get("bounty_freq") in _BOUNTY_CHANCE:
            self.bounty_freq = d["bounty_freq"]

    def _sync_from_mode(self, name: str) -> None:
        """Update toggle state from the chosen mode (built-in or saved)."""
        cfg = self._resolve_mode(name)
        if cfg is None:
            return
        self.random_fonts = bool(cfg.get("random_fonts", False))
        self.vertical_writing = cfg.get("vertical_writing", "off")
        self.repetitions = int(cfg.get("repetitions", 1))
        self.learn_known = int(cfg.get("learn_known", 0))
        self.learn_less_known = int(cfg.get("learn_less_known", 0))
        self.learn_unknown = int(cfg.get("learn_unknown", 0))
        # Saved presets also restore deck / levels / faces / board size.
        if name in self._user_preset_names:
            decks = tuple(cfg.get("decks", ()))
            if decks and decks[0] in {r["name"] for r in self.deck_rows}:
                self.deck = decks[0]
            lv = list(cfg.get("levels") or ())
            if lv:
                self.levels = set(lv)
            faces = tuple(cfg.get("faces", DEFAULT_FACES))
            self.face_mode = 4 if "romaji" in faces else min(3, len(faces))
            wpr = cfg.get("words_per_round")
            if wpr in SIZES:
                self.board_size = wpr

    def _resolve_mode(self, name: str) -> dict | None:
        if name in PRESETS:
            cfg = PRESETS[name]()
            return _config_to_dict(cfg)
        for p in self._user_presets:
            if p["name"] == name:
                return p
        return None

    def _save_preset_dialog(self) -> None:
        import tkinter
        from tkinter import simpledialog

        root = tkinter.Tk()
        root.withdraw()
        try:
            name = simpledialog.askstring(
                tr("PRESET_PROMPT_TITLE"),
                tr("PRESET_PROMPT"),
                initialvalue=f"My {_mode_label(self.mode)}",
                parent=root,
            )
        finally:
            try:
                root.destroy()
            except Exception:
                pass
        if not name:
            return
        name = name.strip()
        if not name or name in PRESETS:
            return  # don't allow shadowing built-in modes
        cfg = self._current_config()
        cfg_dict = _config_to_dict(cfg)
        cfg_dict["name"] = name
        self.app.state.save_preset(cfg_dict)
        # Rebuild the menu so the new preset shows up as a mode button.
        self.mode = name
        self.app.go_menu()

    def _open_import(self) -> None:
        from kanjire.ui.scenes.import_text import open_file_dialog

        path = open_file_dialog()
        if path is None:
            return
        self.app.go_import(path, path.stem)

    def _open_paste(self) -> None:
        from kanjire.ui.scenes.import_text import open_paste_dialog

        result = open_paste_dialog()
        if result is None:
            return
        text, name = result
        self.app.go_import_pasted(text, name)

    def _toggle_level(self, lv):
        if lv in self.levels:
            if len(self.levels) > 1:
                self.levels.discard(lv)
        else:
            self.levels.add(lv)
        self._today_dirty = True
        self._after_change()

    def _refresh(self) -> None:
        # Per-group state (selection / enabled / conditional visibility) is only
        # applied to the group on the active sub-tab, so we never re-show a
        # widget that belongs to the hidden tab.
        quick = self.active_subtab == "quick"
        is_jlpt = self.deck == "jlpt"
        kana_deck = self.deck == kana.KANA_DECK

        if quick:
            for m, b in self.mode_btns:
                b.set_selected(m == self.mode)
            for n, b in self.deck_btns:
                b.set_selected(n == self.deck)
            for s, b in self.size_btns:
                b.set_selected(s == self.board_size)
            # JLPT level row: enabled only for the JLPT deck (greyed for corpus
            # decks), hidden entirely in Kana mode (the layout stashes it).
            for lv, b in self.level_btns:
                b.enabled = is_jlpt and not kana_deck
                b.selected = b.enabled and (lv in self.levels)
                b._refresh()
            # Kana controls replace the level row when the Kana deck is active.
            for n, b in self.kana_length_btns:
                b.set_visible(kana_deck)
                if kana_deck:
                    b.set_selected(n == self.kana_length)
            for v, b in self.kana_script_btns:
                b.set_visible(kana_deck)
                if kana_deck:
                    b.set_selected(v == self.kana_script)
            self.lbl_kana_length.opacity = 255 if kana_deck else 0
            self.lbl_kana_script.opacity = 255 if kana_deck else 0
            self.lbl_level.opacity = 0 if kana_deck else 255
            # Hide corpus-import buttons in a play-only build (no jamdict).
            if not self.app.can_ingest:
                self.import_btn.set_visible(False)
                self.paste_btn.set_visible(False)
        else:
            # CARDS PER WORD is decided by KANA SCRIPT in Kana mode, so disable.
            for mode, b in self.faces_btns:
                b.enabled = not kana_deck
                b.set_selected((mode == self.face_mode) and b.enabled)
            for val, b in self.font_btns:
                b.set_selected(val == self.random_fonts)
            for val, b in self.writing_btns:
                b.set_selected(val == self.vertical_writing)
            for n, b in self.repeat_btns:
                b.set_selected(n == self.repetitions)
            # Learn-mode bucket selectors: visible only in Learn mode.
            showing_learn = self.mode == "Learn"
            for n, b in self.known_btns:
                b.set_visible(showing_learn)
                if showing_learn:
                    b.set_selected(n == self.learn_known)
            for n, b in self.less_known_btns:
                b.set_visible(showing_learn)
                if showing_learn:
                    b.set_selected(n == self.learn_less_known)
            for n, b in self.unknown_btns:
                b.set_visible(showing_learn)
                if showing_learn:
                    b.set_selected(n == self.learn_unknown)
            op = 255 if showing_learn else 0
            self.lbl_known.opacity = op
            self.lbl_less_known.opacity = op
            self.lbl_unknown.opacity = op
            # Survival difficulty selectors: visible only in Survival mode.
            showing_survival = self.mode == "Survival"
            for n, b in self.hearts_btns:
                b.set_visible(showing_survival)
                if showing_survival:
                    b.set_selected(n == self.start_hearts)
            for v, b in self.bounty_btns:
                b.set_visible(showing_survival)
                if showing_survival:
                    b.set_selected(v == self.bounty_freq)
            sop = 255 if showing_survival else 0
            self.lbl_hearts.opacity = sop
            self.lbl_bounty.opacity = sop

        # availability count
        if self.deck == kana.KANA_DECK:
            # Kana mode is generative - always "available", and the count
            # really means how many distinct syllables can appear.
            n = len(kana.KANA_SOUNDS)
            self.avail_label.text = tr("AVAILABLE_KANA", n=n)
            self.play_btn.enabled = True
        else:
            levels = tuple(self.levels) if is_jlpt else None
            try:
                n = db.word_count(self.app.con, decks=[self.deck], levels=levels,
                                  require_kanji=True)
            except Exception:
                n = 0
            self.avail_label.text = (
                tr("AVAILABLE", n=n) + ("" if is_jlpt else "  ·  full corpus")
            )
            self.play_btn.enabled = n >= 2
        self.play_btn._refresh()

        hs = self.app.state.high_score(self.mode)
        self.hiscore_label.text = (
            tr("HISCORE", mode=_mode_label(self.mode), score=hs) if hs else ""
        )

        # Today's Training button + streak line.
        plan = self._get_today_plan()
        streak = self.app.state.streak_status()
        if plan.empty:
            self.today_btn.set_text(tr("TODAY_DONE"))
            self.today_btn.enabled = False
        elif plan.comeback:
            self.today_btn.set_text(tr("TODAY_COMEBACK", n=len(plan.reviews)))
            self.today_btn.enabled = True
        elif streak["done_today"]:
            # Already stamped: extra rounds welcome, framed as a bonus.
            self.today_btn.set_text(tr("TODAY_MORE", rev=len(plan.reviews),
                                       new=len(plan.new_words)))
            self.today_btn.enabled = True
        else:
            self.today_btn.set_text(tr("BTN_TODAY", rev=len(plan.reviews),
                                       new=len(plan.new_words)))
            self.today_btn.enabled = True
        self.today_btn._refresh()
        if streak["count"] > 0:
            # Only glyphs the bundled fonts actually carry - ❄ and ✓ don't
            # exist in them and shipped as empty boxes on Linux.
            frz = " ◇" * streak["freezes"]
            check = " ○" if streak["done_today"] else ""
            self.streak_label.text = tr("STREAK_FOOTER", n=streak["count"]) \
                + frz + check
        else:
            self.streak_label.text = ""

    # ------------------------------------------------------------------ #
    # Build config & launch
    # ------------------------------------------------------------------ #
    def _current_config(self) -> GameConfig:
        """Translate every menu state field into a :class:`GameConfig`."""
        if self.deck == kana.KANA_DECK:
            # Kana mode: script picks which kana script(s) become cards.
            #   hira / kata  -> 2-face board (script + romaji)
            #   both         -> 3-face board (hira + kata + romaji)
            faces = (("kanji", "reading", "meaning") if self.kana_script == "both"
                     else ("kanji", "meaning"))
            levels = ()
        else:
            faces = {
                2: ("kanji", "meaning"),
                3: DEFAULT_FACES,
                4: ("kanji", "reading", "romaji", "meaning"),
            }.get(self.face_mode, DEFAULT_FACES)
            levels = tuple(sorted(self.levels)) if self.deck == "jlpt" else ()
        if self.mode in PRESETS:
            base = PRESETS[self.mode]()
        else:
            # Saved preset: rehydrate every preserved field.
            data = next((p for p in self._user_presets if p["name"] == self.mode), None)
            base = GameConfig()
            if data:
                for f in _PRESET_FIELDS:
                    if f in data and hasattr(base, f):
                        v = data[f]
                        if f in ("decks", "levels", "faces") and isinstance(v, list):
                            v = tuple(v)
                        setattr(base, f, v)
        return base.with_(
            decks=(self.deck,), levels=levels, faces=faces,
            words_per_round=self.board_size,
            random_fonts=self.random_fonts,
            vertical_writing=self.vertical_writing,
            repetitions=self.repetitions,
            learn_known=self.learn_known,
            learn_less_known=self.learn_less_known,
            learn_unknown=self.learn_unknown,
            kana_length=self.kana_length,
            kana_script=self.kana_script,
            start_lives=self.start_hearts,
            max_lives=_HEARTS_MAX[self.start_hearts],
            heart_chance=_BOUNTY_CHANCE[self.bounty_freq],
            name=self.mode,
        )

    def _play(self) -> None:
        if not self.play_btn.enabled:
            return
        self.app.go_game(self._current_config())

    def _get_today_plan(self):
        """Lazily (re)build the Today plan; deck/level changes invalidate it."""
        if self._today_dirty or self._today_plan is None:
            from kanjire.srs.session import TodayPlan, build_today_plan
            decks = None if self.deck == kana.KANA_DECK else [self.deck]
            levels = sorted(self.levels) if self.deck == "jlpt" else None
            try:
                self._today_plan = build_today_plan(
                    self.app.con, self.app.stats, decks=decks, levels=levels)
            except Exception:
                self._today_plan = TodayPlan()
            self._today_dirty = False
        return self._today_plan

    def _play_today(self) -> None:
        plan = self._get_today_plan()
        if plan.empty:
            return
        cfg = GameConfig(
            name="Today",
            decks=(self.deck if self.deck != kana.KANA_DECK else "jlpt",),
            levels=(), faces=DEFAULT_FACES,
            words_per_round=min(6, max(2, len(plan.pool))),
            duration=None, max_mistakes=None, mismatch_penalty=0,
            repetitions=1, session_mode=True,
        )
        # The hardest few reviews come back as a typed-recall epilogue
        # (plan.reviews is already most-at-risk-first).
        self.app.go_game(cfg, pool=plan.pool,
                         recall_words=plan.reviews[:8])

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        from pyglet.window import mouse

        if button == mouse.RIGHT:
            # Right-click on a saved preset button -> ask to delete it.
            for name, btn in self.mode_btns:
                if name in self._user_preset_names and btn.contains(x, y):
                    self._confirm_delete_preset(name)
                    return
        if self.nav.on_mouse_press(x, y):
            return
        if self.subtabs.on_mouse_press(x, y):
            return
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                break

    def _confirm_delete_preset(self, name: str) -> None:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        try:
            ok = messagebox.askyesno(
                tr("DELETE_PRESET_TITLE"),
                tr("DELETE_PRESET_MSG", name=name),
                parent=root,
            )
        finally:
            try:
                root.destroy()
            except Exception:
                pass
        if not ok:
            return
        self.app.state.delete_preset(name)
        if self.mode == name:
            self.mode = "Time Attack"
            self.app.state.set_last_mode(self.mode)
        self.app.go_menu()

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        self.subtabs.on_mouse_motion(x, y)
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol in (key.ENTER, key.RETURN, key.SPACE):
            self._play()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _row(self, btns, y, w, h, gap=12) -> None:
        total = len(btns) * w + (len(btns) - 1) * gap
        x0 = self.width / 2 - total / 2
        for i, (_v, b) in enumerate(btns):
            b.set_rect(x0 + i * (w + gap), y - h / 2, w, h)

    @staticmethod
    def _flat(pairs):
        return [b for _v, b in pairs]

    def _set_group_visible(self, buttons, labels, visible: bool) -> None:
        """Show a group of widgets, or hide them by moving fully off-screen
        (so a hidden tab's buttons can't be clicked or seen)."""
        for b in buttons:
            if visible:
                b.set_visible(True)
            else:
                b.set_rect(-4000, -4000, 1, 1)
                b.set_visible(False)
        for lbl in labels:
            if visible:
                lbl.opacity = 255
            else:
                lbl.x = lbl.y = -4000
                lbl.opacity = 0

    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        cx = width / 2
        # Scale every widget's font from its construction-time base, plus the
        # standalone Labels, so the whole menu shrinks on small screens and
        # grows on large ones.
        self.nav.set_scale(s)
        self.subtabs.set_scale(s)
        for b in self.buttons:
            b.set_scale(s)
        for lbl in self.section_labels:
            lbl.font_size = max(8, round(12 * s))
        self.title.font_size = self.title_kanji.font_size = max(20, round(52 * s))
        self.subtitle.font_size = max(9, round(15 * s))
        self.avail_label.font_size = max(8, round(12 * s))
        self.hiscore_label.font_size = max(8, round(13 * s))
        self.streak_label.font_size = max(8, round(12 * s))

        # Top nav bar (Play | Stats | Settings)
        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)
        y = height - 112 * s
        # Place "KanjiRe" and "漢字" side by side, centred as a group.
        gap = 14 * s
        tw = self.title.content_width
        kw = self.title_kanji.content_width
        left = cx - (tw + gap + kw) / 2
        self.title.anchor_x = "left"
        self.title.x, self.title.y = left, y
        self.title_kanji.anchor_x = "left"
        self.title_kanji.x, self.title_kanji.y = left + tw + gap, y
        # Clear the title's descenders before the subtitle.
        y -= 60 * s
        self.subtitle.x, self.subtitle.y = cx, y
        # Quick | Advanced sub-tab bar.
        y -= 46 * s
        self.subtabs.set_rect(cx - 150 * s, y - 18 * s, 300 * s, 36 * s)
        content_top = y - 42 * s

        if self.active_subtab == "quick":
            self._set_group_visible(self._adv_buttons, self._adv_labels, False)
            self._set_group_visible(self._quick_buttons, self._quick_labels, True)
            self._layout_quick(cx, content_top, s)
        else:
            self._set_group_visible(self._quick_buttons, self._quick_labels, False)
            self._set_group_visible(self._adv_buttons, self._adv_labels, True)
            self._layout_advanced(cx, content_top, s)

        self._layout_footer(cx, s)
        # Apply selection / enabled / conditional visibility on top of the
        # base per-tab show/hide.
        self._refresh()

    # -- per-tab layouts ------------------------------------------------- #
    def _layout_quick(self, cx, y, s) -> None:
        def section(lbl, dy=42):
            nonlocal y
            y -= dy * s
            lbl.x, lbl.y = cx, y

        section(self.lbl_mode, dy=10)
        y -= 30 * s
        n = max(1, len(self.mode_btns))
        budget = min(1080 * s, self.width - 80 * s)
        mode_w = max(80 * s, min(150 * s, (budget - (n - 1) * 12 * s) / n))
        self._row(self.mode_btns, y, mode_w, 40 * s, gap=12 * s)
        section(self.lbl_deck)
        y -= 30 * s
        self._row(self.deck_btns, y, 150 * s, 40 * s, gap=12 * s)
        y -= 34 * s
        bw, gp = 175 * s, 14 * s
        self.import_btn.set_rect(cx - bw - gp / 2, y - 13 * s, bw, 26 * s)
        self.paste_btn.set_rect(cx + gp / 2, y - 13 * s, bw, 26 * s)
        if self.deck == kana.KANA_DECK:
            # Kana mode: KANA LENGTH + KANA SCRIPT replace the JLPT LEVEL row.
            section(self.lbl_kana_length, dy=40)
            y -= 30 * s
            self._row(self.kana_length_btns, y, 80 * s, 38 * s, gap=12 * s)
            section(self.lbl_kana_script)
            y -= 30 * s
            self._row(self.kana_script_btns, y, 130 * s, 38 * s, gap=12 * s)
            self._set_group_visible(self._flat(self.level_btns), [self.lbl_level], False)
        else:
            section(self.lbl_level, dy=40)
            y -= 30 * s
            self._row(self.level_btns, y, 70 * s, 38 * s, gap=12 * s)
            self._set_group_visible(
                self._flat(self.kana_length_btns) + self._flat(self.kana_script_btns),
                [self.lbl_kana_length, self.lbl_kana_script], False,
            )
        section(self.lbl_size)
        y -= 30 * s
        self._row(self.size_btns, y, 90 * s, 38 * s, gap=12 * s)

    def _layout_advanced(self, cx, y, s) -> None:
        def section(lbl, dy=42):
            nonlocal y
            y -= dy * s
            lbl.x, lbl.y = cx, y

        section(self.lbl_faces, dy=10)
        y -= 30 * s
        self._row(self.faces_btns, y, 200 * s, 40 * s, gap=12 * s)
        section(self.lbl_fonts)
        y -= 28 * s
        self._row(self.font_btns, y, 120 * s, 32 * s, gap=12 * s)
        section(self.lbl_writing)
        y -= 28 * s
        self._row(self.writing_btns, y, 100 * s, 32 * s, gap=12 * s)
        section(self.lbl_repeat)
        y -= 28 * s
        self._row(self.repeat_btns, y, 76 * s, 32 * s, gap=12 * s)
        learn_widgets = (
            self._flat(self.known_btns) + self._flat(self.less_known_btns)
            + self._flat(self.unknown_btns),
            [self.lbl_known, self.lbl_less_known, self.lbl_unknown],
        )
        survival_widgets = (
            self._flat(self.hearts_btns) + self._flat(self.bounty_btns),
            [self.lbl_hearts, self.lbl_bounty],
        )
        if self.mode == "Learn":
            # Compact inline rows (label left, buttons right) — the stacked
            # version was ~180px taller and collided with the footer on
            # short windows.
            bw2, bh2, gap2 = 78 * s, 30 * s, 8 * s
            row_w = 4 * bw2 + 3 * gap2
            for lbl, btns in ((self.lbl_known, self.known_btns),
                              (self.lbl_less_known, self.less_known_btns),
                              (self.lbl_unknown, self.unknown_btns)):
                y -= 40 * s
                lbl.anchor_x = "right"
                lbl.x, lbl.y = cx - row_w / 2 - 16 * s, y
                x0 = cx - row_w / 2
                for i, (_v, b) in enumerate(btns):
                    b.set_rect(x0 + i * (bw2 + gap2), y - bh2 / 2, bw2, bh2)
            self._set_group_visible(*survival_widgets, False)
        elif self.mode == "Survival":
            section(self.lbl_hearts)
            y -= 28 * s
            self._row(self.hearts_btns, y, 74 * s, 30 * s, gap=12 * s)
            section(self.lbl_bounty, dy=38)
            y -= 28 * s
            self._row(self.bounty_btns, y, 92 * s, 30 * s, gap=10 * s)
            self._set_group_visible(*learn_widgets, False)
        else:
            self._set_group_visible(*learn_widgets, False)
            self._set_group_visible(*survival_widgets, False)

    def _layout_footer(self, cx, s) -> None:
        # Persistent footer, bottom-anchored so the buttons sit in the same
        # place on both sub-tabs. Today's Training and PLAY share one row (the
        # same vertical envelope as the old lone PLAY button, so the tab
        # content above never collides); save-preset tucks into the corner.
        #
        # The update banner is its own bottom strip, so everything here rides up
        # by its height while it's showing - it used to sit straight on top of
        # Multiplayer, Save-as-preset and the streak line.
        # The update banner is an app-level strip along the bottom, so the
        # footer rides up by its height while it's showing (it used to sit
        # straight on top of Multiplayer, Save-as-preset and the streak line).
        lift = self.app.banner.height()
        self.today_btn.set_rect(cx - 340 * s, 120 * s + lift, 330 * s, 56 * s)
        self.play_btn.set_rect(cx + 10 * s, 120 * s + lift, 330 * s, 56 * s)
        self.save_preset_btn.set_rect(16 * s, 16 * s + lift, 180 * s, 26 * s)
        self.mp_btn.set_rect(self.width - 196 * s, 16 * s + lift, 180 * s, 26 * s)
        self.avail_label.x, self.avail_label.y = cx, 90 * s + lift
        self.hiscore_label.x, self.hiscore_label.y = cx, 64 * s + lift
        self.streak_label.x, self.streak_label.y = cx, 40 * s + lift

    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> None:
        pass

    # ------------------------------------------------------------------ #
    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        self.batch.draw()

    def on_exit(self) -> None:
        self.nav.delete()
        self.subtabs.delete()
        for b in self.buttons:
            b.delete()
