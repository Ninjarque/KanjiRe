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
from kanjire.game.menuconfig import KANA_SCRIPTS
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
from kanjire.game.menuconfig import MODE_TR as _MODE_TR
from kanjire.game.menuconfig import PRESET_TR as _PRESET_TR
from kanjire.game.menuconfig import FACTORY_MODES, FRONT_MODES, second_row_modes
from kanjire.data.genres import GENRES, valid_genres


def _mode_label(name: str) -> str:
    """Display label for a mode (built-in localised, custom presets verbatim)."""
    key = _MODE_TR.get(name) or _PRESET_TR.get(name)
    return tr(key) if key else name


def _deck_label(name: str, description: str = "") -> str:
    if name == kana.KANA_DECK:
        return tr("DECK_KANA")
    if name == "jlpt":
        return "JLPT"
    if name.startswith("corpus:"):
        return name[len("corpus:"):].replace("-", " ").title()
    return name


#: Saved-preset fields. Shared with the Kivy UI (and with ``config_for``)
#: rather than re-listed here — this file's private copy had already drifted,
#: silently dropping ``recall_preview`` from every preset saved on desktop.
from kanjire.game.menuconfig import PRESET_FIELDS as _PRESET_FIELDS  # noqa: E402

#: (state value, translation key) for the Recall prompt-style row.
RECALL_PROMPT_OPTIONS = (("typed", "RECALL_P_TYPED"),
                         ("listen", "RECALL_P_LISTEN"),
                         ("both", "RECALL_P_BOTH"),
                         ("choice", "RECALL_P_CHOICE"),
                         ("mixed", "RECALL_P_MIXED"))


def _config_to_dict(cfg: GameConfig) -> dict:
    """JSON-serialisable subset of a :class:`GameConfig` for saved presets."""
    from kanjire.game.menuconfig import preset_from_config
    return preset_from_config(cfg, cfg.name)


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
        #: Selected decks — an ordered multi-select drawing a UNION pool.
        #: The synthetic kana deck is generative and stays exclusive.
        self.decks: list[str] = (
            ["jlpt"] if any(r["name"] == "jlpt" for r in self.deck_rows)
            else [self.deck_rows[0]["name"]] if self.deck_rows else ["jlpt"])
        self.levels: set[int] = {5}
        self.board_size = 6
        #: The card faces in play — any subset of FACE_ORDER, minimum two.
        self.faces_sel = list(DEFAULT_FACES)
        # Visual toggles (also part of saved presets)
        self.random_fonts = False
        self.vertical_writing = "off"
        self.repetitions = 1
        # Learn-mode bucket mix (only shown when "Learn" is the active mode).
        # Clustering state: no genre filter, dials off, until asked for.
        self.genres: list[str] = []
        self.aff_meaning = 0
        self.aff_looks = 0
        self.aff_sound = 0
        self.learn_known = 0
        self.learn_less_known = 0
        self.learn_unknown = 0
        # Kana-mode controls (only shown when the "kana" deck is active).
        self.kana_length = 1
        self.kana_script = "both"
        # Survival difficulty (only shown when "Survival" is the active mode).
        self.start_hearts = 3
        self.bounty_freq = "low"
        # Recall prompt style (only shown when "Recall" is the active mode).
        self.recall_prompt = "mixed"
        self.recall_preview = True
        # Which menu sub-tab is showing: "quick" (mode/deck/level/words) or
        # "advanced" (cards/fonts/writing/passes/learn buckets).
        self.active_subtab = "quick"

        # Snapshot of currently-saved presets (names of those are user-deletable).
        self._user_presets = list(app.state.presets)
        self._user_preset_names = {p["name"] for p in self._user_presets}
        # Built-in presets (Familiarize/Learn — ex-modes) + the user's.
        from kanjire.game.menuconfig import all_presets
        self._all_presets = all_presets(app.state)

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
        # Front row: the three modes people actually start from, plus the +
        # that turns the current settings into a custom mode of their own.
        self.front_mode_btns: list[tuple[str, Button]] = [
            (m, self._btn(_mode_label(m), lambda m=m: self._set_mode(m)))
            for m in FRONT_MODES
        ]
        self.new_mode_btn = self._btn(tr("BTN_NEW_MODE"),
                                      self._save_preset_dialog,
                                      accent=theme.SUCCESS)
        # Second row: factory modes (Zen / Recall / Familiarize) then the
        # player's own. Gold = "this is a configuration, not a ruleset".
        self.saved_mode_btns: list[tuple[str, Button]] = [
            (n, self._btn(_mode_label(n), lambda n=n: self._set_mode(n),
                          accent=theme.GOLD, font_size=12))
            for n in second_row_modes(self.app.state)
        ]
        # Every mode button, for hit-testing and selection refresh.
        self.mode_btns: list[tuple[str, Button]] = (
            self.front_mode_btns + self.saved_mode_btns)
        # Only shown while a *custom* mode is selected — the factory ones
        # can't be deleted.
        self.delete_mode_btn = self._btn(tr("BTN_DELETE_MODE"),
                                         self._delete_current_mode,
                                         accent=theme.DANGER, font_size=12)
        self.lbl_deck = self._section(tr("SEC_DECK"))
        self.deck_btns = [
            (r["name"], self._btn(_deck_label(r["name"]),
                                  lambda n=r["name"]: self._toggle_deck(n)))
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
        # One toggle per card face, in its own face colour (user request:
        # explicit AND customizable — any subset, minimum two).
        from kanjire.game.menuconfig import FACE_OPTIONS
        self.lbl_faces = self._section(tr("SEC_CARDS"))
        self.faces_btns = [
            (face, self._btn(tr(key), lambda f=face: self._toggle_face(f),
                             accent=theme.FACE_COLORS[face], font_size=12))
            for face, key in FACE_OPTIONS
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
        # Clustering: three affinity dials on the same 0-3 scale as the
        # knowledge mix, plus a genre filter drawn as its kanji badges (40
        # names would never fit; 40 single glyphs do, in two tidy rows).
        self.lbl_affinity = self._section(tr("ROW_AFFINITY"))
        self.aff_btns: dict[str, list] = {}
        for key, accent in (("aff_meaning", theme.FACE_COLORS["meaning"]),
                            ("aff_looks", theme.FACE_COLORS["kanji"]),
                            ("aff_sound", theme.FACE_COLORS["reading"])):
            self.aff_btns[key] = [
                (n, self._btn(tr(_LEARN_LABEL_KEYS[n]),
                              lambda k=key, n=n: self._set_affinity(k, n),
                              accent=accent, font_size=11))
                for n in LEARN_STEPS
            ]
        self.lbl_aff_rows = {
            "aff_meaning": self._section(tr("AFF_MEANING")),
            "aff_looks": self._section(tr("AFF_LOOKS")),
            "aff_sound": self._section(tr("AFF_SOUND")),
        }
        # Genres are *chosen* in the Genres browser (icons, per-level
        # progress, room to breathe). This tab only shows what's selected and
        # offers a way in and a way out — forty badges here collided with the
        # footer, and duplicated a screen that does the job far better.
        self.lbl_genre = self._section(tr("ROW_GENRE"))
        self.genre_pick_btn = self._btn(
            tr("GENRE_PICK_TITLE"), lambda: self.app.go_journey(tab="genres"),
            accent=theme.GOLD, font_size=11)
        self.genre_clear_btn = self._btn(
            tr("GENRE_ALL"), self._clear_genres,
            accent=theme.DIM, font_size=11)
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
        # Two toggles like the card faces: both on = the paired hira ↔ kata
        # matching deck ("both").
        self.lbl_kana_script = self._section(tr("SEC_KANA_SCRIPT"))
        self.kana_script_btns = [
            (val, self._btn(tr(label_key),
                            lambda v=val: self._toggle_kana_script(v),
                            accent=theme.FACE_COLORS["kanji"], font_size=12))
            for val, label_key in (("hira", "KANA_SCRIPT_HIRA"),
                                   ("kata", "KANA_SCRIPT_KATA"))
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

        # Recall prompt style: type / listen / … Shown only for Recall.
        self.lbl_recall_prompt = self._section(tr("SEC_RECALL_PROMPT"))
        self.recall_prompt_btns = [
            (val, self._btn(tr(key), lambda v=val: self._set_recall_prompt(v),
                            accent=theme.FACE_COLORS["reading"], font_size=11))
            for val, key in RECALL_PROMPT_OPTIONS
        ]
        # Study-first: show the drill's words once before quizzing them.
        self.lbl_recall_preview = self._section(tr("SEC_RECALL_PREVIEW"))
        self.recall_preview_btns = [
            (val, self._btn(tr(key), lambda v=val: self._set_recall_preview(v),
                            accent=theme.GOLD, font_size=11))
            for val, key in ((True, "OPT_ON"), (False, "OPT_OFF"))
        ]

        # (The old footer "Save as preset…" button is gone: the + in the mode
        # row does exactly the same thing, where modes are chosen.)
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
        ) + [self.import_btn, self.paste_btn, self.new_mode_btn,
             self.delete_mode_btn]
        self._quick_labels = [
            self.lbl_mode, self.lbl_deck, self.lbl_level,
            self.lbl_kana_length, self.lbl_kana_script, self.lbl_size,
        ]
        self._adv_buttons = _btns(
            self.faces_btns, self.font_btns, self.writing_btns,
            self.repeat_btns,
            self.known_btns, self.less_known_btns, self.unknown_btns,
            self.hearts_btns, self.bounty_btns, self.recall_prompt_btns,
            self.recall_preview_btns, *self.aff_btns.values(),
        ) + [self.genre_pick_btn, self.genre_clear_btn]
        self._adv_labels = [
            self.lbl_faces, self.lbl_fonts, self.lbl_writing, self.lbl_repeat,
            self.lbl_known, self.lbl_less_known, self.lbl_unknown,
            self.lbl_hearts, self.lbl_bounty, self.lbl_recall_prompt,
            self.lbl_recall_preview, self.lbl_genre, self.lbl_affinity,
            *self.lbl_aff_rows.values(),
        ]

    # ------------------------------------------------------------------ #
    # State changes
    # ------------------------------------------------------------------ #
    def _set_affinity(self, key: str, value: int) -> None:
        setattr(self, key, int(value))
        self._persist()
        self._refresh()

    def _clear_genres(self) -> None:
        """Back to every genre — the filter's off switch."""
        self.genres = []
        self._persist()
        self._refresh()

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

    def _toggle_deck(self, n):
        """Deck toggles form a union pool; the generative kana deck is
        exclusive (it can't blend with DB words on one board)."""
        if n in self.decks:
            if len(self.decks) > 1:
                self.decks.remove(n)
        elif n == kana.KANA_DECK:
            self.decks = [n]                    # kana clears the others…
        else:
            self.decks = [d for d in self.decks
                          if d != kana.KANA_DECK] + [n]   # …and back
        self._today_dirty = True
        self._after_change()
        # Selecting / leaving Kana changes which rows take space.
        self.on_resize(self.width, self.height)
    def _set_size(self, s):       self.board_size = s;           self._after_change()
    def _toggle_face(self, face: str) -> None:
        from kanjire.game.menuconfig import FACE_ORDER
        if face in self.faces_sel:
            if len(self.faces_sel) <= 2:
                return                      # a board needs at least two faces
            self.faces_sel.remove(face)
        else:
            self.faces_sel = [f for f in FACE_ORDER
                              if f in self.faces_sel or f == face]
        self._after_change()
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

    def _toggle_kana_script(self, v: str) -> None:
        on = ({"hira", "kata"} if self.kana_script == "both"
              else {self.kana_script})
        if v in on:
            if len(on) > 1:
                on.discard(v)
        else:
            on.add(v)
        self.kana_script = ("both" if on >= {"hira", "kata"}
                            else "kata" if "kata" in on else "hira")
        self._after_change()

    def _set_hearts(self, n: int) -> None:
        self.start_hearts = int(n)
        self._after_change()

    def _set_bounty(self, v: str) -> None:
        self.bounty_freq = v
        self._after_change()

    def _set_recall_preview(self, v: bool) -> None:
        self.recall_preview = bool(v)
        self._after_change()

    def _set_recall_prompt(self, v: str) -> None:
        self.recall_prompt = v
        self._after_change()

    def _after_change(self) -> None:
        """Persist current settings under the active mode, then re-render."""
        self.app.state.set_last_for_mode(self.mode, self._settings_dict())
        self._refresh()

    def _settings_dict(self) -> dict:
        return {
            "decks": list(self.decks),
            # Legacy mirror so pre-multi-deck builds (and their synced
            # settings) still read something sensible.
            "deck": self.decks[0],
            "levels": sorted(self.levels),
            "board_size": self.board_size,
            "faces": list(self.faces_sel),
            # Mirror the legacy key so pre-toggle builds (and their synced
            # settings) still read something sensible.
            "face_mode": len(self.faces_sel),
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
            "recall_prompt": self.recall_prompt,
            "recall_preview": self.recall_preview,
            "genres": list(self.genres),
            "aff_meaning": self.aff_meaning,
            "aff_looks": self.aff_looks,
            "aff_sound": self.aff_sound,
        }

    def _apply_settings(self, d: dict) -> None:
        deck_names = {r["name"] for r in self.deck_rows}
        decks = d.get("decks")
        if not (isinstance(decks, (list, tuple)) and decks):
            decks = [d["deck"]] if isinstance(d.get("deck"), str) else []
        decks = [x for x in decks if x in deck_names]
        if kana.KANA_DECK in decks:
            decks = [kana.KANA_DECK]           # generative: exclusive
        if decks:
            self.decks = list(dict.fromkeys(decks))
        levels = [lv for lv in d.get("levels", []) if lv in LEVELS]
        if levels:
            self.levels = set(levels)
        if d.get("board_size") in SIZES:
            self.board_size = d["board_size"]
        from kanjire.game.menuconfig import FACES_BY_MODE, normalize_faces
        faces = normalize_faces(d.get("faces"))
        if faces is None and d.get("face_mode") in (2, 3, 4):
            faces = list(FACES_BY_MODE[int(d["face_mode"])])
        if faces is None and "faces3" in d:   # pre-4-card settings
            faces = list(FACES_BY_MODE[3 if d["faces3"] else 2])
        if faces is not None:
            self.faces_sel = faces
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
        from kanjire.game.menuconfig import KANA_SCRIPT_ALIASES
        ks = d.get("kana_script")
        ks = KANA_SCRIPT_ALIASES.get(ks, ks)
        if ks in {v for v, _ in KANA_SCRIPTS}:
            self.kana_script = ks
        if d.get("start_hearts") in HEARTS_OPTIONS:
            self.start_hearts = int(d["start_hearts"])
        if d.get("bounty_freq") in _BOUNTY_CHANCE:
            self.bounty_freq = d["bounty_freq"]
        if d.get("recall_prompt") in {v for v, _ in RECALL_PROMPT_OPTIONS}:
            self.recall_prompt = d["recall_prompt"]
        if "recall_preview" in d:
            self.recall_preview = bool(d["recall_preview"])
        if "genres" in d:
            self.genres = list(valid_genres(d.get("genres")))
        for key in ("aff_meaning", "aff_looks", "aff_sound"):
            if d.get(key) in LEARN_STEPS:
                setattr(self, key, int(d[key]))

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
        self.recall_prompt = cfg.get("recall_prompt", "mixed")
        self.genres = list(valid_genres(cfg.get("genres", ())))
        for key in ("aff_meaning", "aff_looks", "aff_sound"):
            setattr(self, key, int(cfg.get(key, 0) or 0))
        # Presets (built-in or saved) also restore deck / levels / faces /
        # board size when they carry them.
        if name not in PRESETS:
            names = {r["name"] for r in self.deck_rows}
            decks = [d for d in cfg.get("decks", ()) if d in names]
            if decks:
                self.decks = (
                    [kana.KANA_DECK] if kana.KANA_DECK in decks else decks)
            lv = list(cfg.get("levels") or ())
            if lv:
                self.levels = set(lv)
            from kanjire.game.menuconfig import normalize_faces
            self.faces_sel = (normalize_faces(cfg.get("faces"))
                              or list(DEFAULT_FACES))
            wpr = cfg.get("words_per_round")
            if wpr in SIZES:
                self.board_size = wpr

    def _resolve_mode(self, name: str) -> dict | None:
        if name in PRESETS:
            cfg = PRESETS[name]()
            return _config_to_dict(cfg)
        for p in self._all_presets:
            if p["name"] == name:
                return p
        return None

    def _ruleset(self) -> str:
        """The effective ruleset behind the selected mode/preset — a preset
        saved from Survival or Recall keeps that ruleset's option rows."""
        if self.mode in PRESETS:
            return self.mode
        cfg = self._resolve_mode(self.mode) or {}
        if cfg.get("recall_mode"):
            return "Recall"
        if cfg.get("lives_mode"):
            return "Survival"
        return "Zen"

    def _save_preset_dialog(self) -> None:
        def save(name: str) -> None:
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

        self.app.prompt(tr("PRESET_PROMPT"), save,
                        initial=f"My {_mode_label(self.mode)}")

    def _open_import(self) -> None:
        from kanjire.ui.scenes.import_text import open_file_dialog

        try:
            path = open_file_dialog()
        except ImportError:
            # tkinter is absent (e.g. a frozen Linux build): don't crash - the
            # file/paste dialogs are the one place we still need a native widget.
            self.app.confirm(tr("IMPORT_UNAVAILABLE"), lambda: None,
                             confirm_label=tr("DLG_OK"), cancel_label=" ")
            return
        if path is None:
            return
        self.app.go_import(path, path.stem)

    def _open_paste(self) -> None:
        from kanjire.ui.scenes.import_text import open_paste_dialog

        try:
            result = open_paste_dialog()
        except ImportError:
            self.app.confirm(tr("IMPORT_UNAVAILABLE"), lambda: None,
                             confirm_label=tr("DLG_OK"), cancel_label=" ")
            return
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
        is_jlpt = "jlpt" in self.decks
        kana_deck = kana.KANA_DECK in self.decks

        if quick:
            for m, b in self.mode_btns:
                b.set_selected(m == self.mode)
            # The red delete button belongs only to the player's own modes.
            self.delete_mode_btn.set_visible(
                self.mode in self._user_preset_names)
            for n, b in self.deck_btns:
                b.set_selected(n in self.decks)
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
            script_on = ({"hira", "kata"} if self.kana_script == "both"
                         else {self.kana_script})
            for v, b in self.kana_script_btns:
                b.set_visible(kana_deck)
                if kana_deck:
                    b.set_selected(v in script_on)
            self.lbl_kana_length.opacity = 255 if kana_deck else 0
            self.lbl_kana_script.opacity = 255 if kana_deck else 0
            self.lbl_level.opacity = 0 if kana_deck else 255
            # Hide corpus-import buttons in a play-only build (no jamdict).
            if not self.app.can_ingest:
                self.import_btn.set_visible(False)
                self.paste_btn.set_visible(False)
        else:
            # CARDS PER WORD is decided by KANA SCRIPT in Kana mode, so disable.
            for face, b in self.faces_btns:
                b.enabled = not kana_deck
                b.set_selected((face in self.faces_sel) and b.enabled)
            for val, b in self.font_btns:
                b.set_selected(val == self.random_fonts)
            for val, b in self.writing_btns:
                b.set_selected(val == self.vertical_writing)
            for n, b in self.repeat_btns:
                b.set_selected(n == self.repetitions)
            # Word-difficulty bucket selectors: shown for Learn AND Recall,
            # which both draw a tuned known/less-known/unknown mix.
            # Unified settings: the knowledge-mix dials shape word sampling
            # in EVERY mode (they always silently did — now they're visible
            # and steerable everywhere instead of hidden outside Learn).
            showing_learn = True
            # Clustering rows: dials, badges, and a label that spells out the
            # selection (the badges alone are pretty but not self-explaining).
            for key, btns in self.aff_btns.items():
                for n, b in btns:
                    b.set_selected(n == getattr(self, key))
            picked = set(self.genres)
            self.genre_clear_btn.enabled = bool(picked)
            if picked:
                names = [tr(g.tr) for g in GENRES if g.key in picked]
                shown = ", ".join(names[:3])
                if len(names) > 3:
                    shown += f" +{len(names) - 3}"
                self.lbl_genre.text = f"{tr('ROW_GENRE')}: {shown}"
            else:
                self.lbl_genre.text = f"{tr('ROW_GENRE')}: {tr('GENRE_ALL')}"
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
            showing_survival = self._ruleset() == "Survival"
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
            # Recall prompt-style selector: visible only in Recall mode.
            showing_recall = self._ruleset() == "Recall"
            for v, b in self.recall_prompt_btns:
                b.set_visible(showing_recall)
                if showing_recall:
                    b.set_selected(v == self.recall_prompt)
            self.lbl_recall_prompt.opacity = 255 if showing_recall else 0
            for v, b in self.recall_preview_btns:
                b.set_visible(showing_recall)
                if showing_recall:
                    b.set_selected(v == self.recall_preview)
            self.lbl_recall_preview.opacity = 255 if showing_recall else 0

        # availability count
        if kana.KANA_DECK in self.decks:
            # Kana mode is generative - always "available", and the count
            # really means how many distinct syllables can appear.
            n = len(kana.KANA_SOUNDS)
            self.avail_label.text = tr("AVAILABLE_KANA", n=n)
            self.play_btn.enabled = True
        else:
            levels = tuple(self.levels) if is_jlpt else None
            try:
                n = db.word_count(self.app.con, decks=list(self.decks),
                                  levels=levels, require_kanji=True)
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
        if kana.KANA_DECK in self.decks:
            # Kana mode: script picks which kana script(s) become cards.
            #   hira / kata  -> 2-face board (script + romaji)
            #   both         -> 3-face board (hira + kata + romaji)
            faces = (("kanji", "reading", "meaning") if self.kana_script == "both"
                     else ("kanji", "meaning"))
            levels = ()
        else:
            faces = tuple(self.faces_sel) or DEFAULT_FACES
            levels = tuple(sorted(self.levels)) if "jlpt" in self.decks else ()
        if self.mode in PRESETS:
            base = PRESETS[self.mode]()
        else:
            # Saved preset: rehydrate every preserved field.
            data = next((p for p in self._all_presets
                         if p["name"] == self.mode), None)
            base = GameConfig()
            if data:
                for f in _PRESET_FIELDS:
                    if f in data and hasattr(base, f):
                        v = data[f]
                        if f in ("decks", "levels", "faces") and isinstance(v, list):
                            v = tuple(v)
                        setattr(base, f, v)
        return base.with_(
            decks=tuple(self.decks), levels=levels, faces=faces,
            words_per_round=self.board_size,
            random_fonts=self.random_fonts,
            vertical_writing=self.vertical_writing,
            repetitions=self.repetitions,
            genres=tuple(self.genres),
            aff_meaning=self.aff_meaning,
            aff_looks=self.aff_looks,
            aff_sound=self.aff_sound,
            learn_known=self.learn_known,
            learn_less_known=self.learn_less_known,
            learn_unknown=self.learn_unknown,
            kana_length=self.kana_length,
            kana_script=self.kana_script,
            start_lives=self.start_hearts,
            max_lives=_HEARTS_MAX[self.start_hearts],
            heart_chance=_BOUNTY_CHANCE[self.bounty_freq],
            recall_prompt=self.recall_prompt,
            recall_preview=self.recall_preview,
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
            decks = (None if kana.KANA_DECK in self.decks
                     else list(self.decks))
            levels = sorted(self.levels) if "jlpt" in self.decks else None
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
        decks = [d for d in self.decks if d != kana.KANA_DECK] or ["jlpt"]
        cfg = GameConfig(
            name="Today",
            decks=tuple(decks),
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

    def _delete_current_mode(self) -> None:
        """The red button under a custom mode (Android has no right-click)."""
        if self.mode in self._user_preset_names:
            self._confirm_delete_preset(self.mode)

    def _confirm_delete_preset(self, name: str) -> None:
        def apply() -> None:
            self.app.state.delete_preset(name)
            if self.mode == name:
                self.mode = "Time Attack"
                self.app.state.set_last_mode(self.mode)
            self.app.go_menu()

        self.app.confirm(tr("DELETE_PRESET_MSG", name=name), apply, danger=True)

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
        budget = min(1080 * s, self.width - 80 * s)

        def fit(count, cap):
            return max(80 * s, min(cap, (budget - (count - 1) * 12 * s) / count))

        front = [*self.front_mode_btns, ("", self.new_mode_btn)]
        self._row(front, y, fit(len(front), 150 * s), 40 * s, gap=12 * s)
        if self.saved_mode_btns:
            y -= 42 * s
            self._row(self.saved_mode_btns,
                      y, fit(len(self.saved_mode_btns), 130 * s),
                      32 * s, gap=10 * s)
        if self.mode in self._user_preset_names:
            y -= 34 * s
            self.delete_mode_btn.set_rect(cx - 90 * s, y - 11 * s,
                                          180 * s, 24 * s)
        section(self.lbl_deck)
        y -= 30 * s
        self._row(self.deck_btns, y, 150 * s, 40 * s, gap=12 * s)
        y -= 34 * s
        bw, gp = 175 * s, 14 * s
        self.import_btn.set_rect(cx - bw - gp / 2, y - 13 * s, bw, 26 * s)
        self.paste_btn.set_rect(cx + gp / 2, y - 13 * s, bw, 26 * s)
        if kana.KANA_DECK in self.decks:
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

        learn_widgets = (
            self._flat(self.known_btns) + self._flat(self.less_known_btns)
            + self._flat(self.unknown_btns),
            [self.lbl_known, self.lbl_less_known, self.lbl_unknown],
        )
        survival_widgets = (
            self._flat(self.hearts_btns) + self._flat(self.bounty_btns),
            [self.lbl_hearts, self.lbl_bounty],
        )
        recall_widgets = (
            self._flat(self.recall_prompt_btns)
            + self._flat(self.recall_preview_btns),
            [self.lbl_recall_prompt, self.lbl_recall_preview],
        )
        board_widgets = (
            self._flat(self.faces_btns) + self._flat(self.font_btns)
            + self._flat(self.writing_btns) + self._flat(self.repeat_btns),
            [self.lbl_faces, self.lbl_fonts, self.lbl_writing, self.lbl_repeat],
        )

        # Recall is a typing drill: cards / fonts / writing / passes make no
        # sense, so hide them and show only what shapes a recall session - the
        # prompt style, and (shared with Learn) the word-difficulty mix.
        ruleset = self._ruleset()
        if ruleset == "Recall":
            self._set_group_visible(*board_widgets, False)
            self._set_group_visible(*survival_widgets, False)
            section(self.lbl_recall_prompt, dy=10)
            y -= 28 * s
            self._row(self.recall_prompt_btns, y, 118 * s, 32 * s, gap=8 * s)
            section(self.lbl_recall_preview, dy=44)
            y -= 28 * s
            self._row(self.recall_preview_btns, y, 90 * s, 30 * s, gap=8 * s)
            bw2, bh2, gap2 = 78 * s, 30 * s, 8 * s
            row_w = 4 * bw2 + 3 * gap2
            for lbl, btns in ((self.lbl_known, self.known_btns),
                              (self.lbl_less_known, self.less_known_btns),
                              (self.lbl_unknown, self.unknown_btns)):
                y -= 44 * s
                lbl.anchor_x = "right"
                lbl.x, lbl.y = cx - row_w / 2 - 16 * s, y
                x0 = cx - row_w / 2
                for i, (_v, b) in enumerate(btns):
                    b.set_rect(x0 + i * (bw2 + gap2), y - bh2 / 2, bw2, bh2)
            # Recall draws from the same clustered pools as the board modes.
            for key in ("aff_meaning", "aff_looks", "aff_sound"):
                y -= 38 * s
                lbl = self.lbl_aff_rows[key]
                lbl.anchor_x = "right"
                lbl.x, lbl.y = cx - row_w / 2 - 16 * s, y
                x0 = cx - row_w / 2
                for i, (_v, b) in enumerate(self.aff_btns[key]):
                    b.set_rect(x0 + i * (bw2 + gap2), y - bh2 / 2, bw2, bh2)
            self._layout_genres(cx, y, s)
            return

        self._set_group_visible(*recall_widgets, False)
        section(self.lbl_faces, dy=10)
        y -= 30 * s
        self._row(self.faces_btns, y, 150 * s, 40 * s, gap=10 * s)
        section(self.lbl_fonts)
        y -= 28 * s
        self._row(self.font_btns, y, 120 * s, 32 * s, gap=12 * s)
        section(self.lbl_writing)
        y -= 28 * s
        self._row(self.writing_btns, y, 100 * s, 32 * s, gap=12 * s)
        section(self.lbl_repeat)
        y -= 28 * s
        self._row(self.repeat_btns, y, 76 * s, 32 * s, gap=12 * s)
        # Survival's extra ruleset rows, inline (label left, buttons right)
        # to spend as little height as possible above the dials.
        if ruleset == "Survival":
            bwS, bhS, gapS = 74 * s, 30 * s, 10 * s
            for lbl, btns, bw in ((self.lbl_hearts, self.hearts_btns, 74 * s),
                                  (self.lbl_bounty, self.bounty_btns, 92 * s)):
                row_w = len(btns) * bw + (len(btns) - 1) * gapS
                y -= 40 * s
                lbl.anchor_x = "right"
                lbl.x, lbl.y = cx - row_w / 2 - 16 * s, y
                x0 = cx - row_w / 2
                for i, (_v, b) in enumerate(btns):
                    b.set_rect(x0 + i * (bw + gapS), y - bhS / 2, bw, bhS)
            del bwS
        else:
            self._set_group_visible(*survival_widgets, False)
        # Knowledge-mix dials: unified across every board mode. Compact
        # inline rows — the stacked version collided with the footer.
        bw2, bh2, gap2 = 78 * s, 30 * s, 8 * s
        row_w = 4 * bw2 + 3 * gap2
        for lbl, btns in ((self.lbl_known, self.known_btns),
                          (self.lbl_less_known, self.less_known_btns),
                          (self.lbl_unknown, self.unknown_btns),
                          (self.lbl_aff_rows["aff_meaning"],
                           self.aff_btns["aff_meaning"]),
                          (self.lbl_aff_rows["aff_looks"],
                           self.aff_btns["aff_looks"]),
                          (self.lbl_aff_rows["aff_sound"],
                           self.aff_btns["aff_sound"])):
            y -= 40 * s
            lbl.anchor_x = "right"
            lbl.x, lbl.y = cx - row_w / 2 - 16 * s, y
            x0 = cx - row_w / 2
            for i, (_v, b) in enumerate(btns):
                b.set_rect(x0 + i * (bw2 + gap2), y - bh2 / 2, bw2, bh2)
        self._layout_genres(cx, y, s)

    def _layout_genres(self, cx, y, s) -> float:
        """One compact line: what's selected, plus in and out."""
        y -= 36 * s
        self.lbl_genre.anchor_x = "right"
        self.lbl_genre.x, self.lbl_genre.y = cx - 8 * s, y
        bw, bh, gap = 108 * s, 24 * s, 8 * s
        self.genre_pick_btn.set_rect(cx + 16 * s, y - bh / 2, bw, bh)
        self.genre_clear_btn.set_rect(cx + 16 * s + bw + gap, y - bh / 2,
                                      70 * s, bh)
        return y

    def _layout_footer(self, cx, s) -> None:
        # Persistent footer, bottom-anchored so the buttons sit in the same
        # place on both sub-tabs. Today's Training and PLAY share one row (the
        # same vertical envelope as the old lone PLAY button, so the tab
        # content above never collides).
        #
        # The update banner is an app-level strip along the bottom, so the
        # footer rides up by its height while it's showing (it used to sit
        # straight on top of Multiplayer and the streak line).
        lift = self.app.banner.height()
        self.today_btn.set_rect(cx - 340 * s, 120 * s + lift, 330 * s, 56 * s)
        self.play_btn.set_rect(cx + 10 * s, 120 * s + lift, 330 * s, 56 * s)
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
