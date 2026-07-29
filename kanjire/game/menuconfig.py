"""Toolkit-free menu model: mode/option constants + settings→GameConfig.

Mirrors the pyglet menu's conversion of a per-mode settings dict (the shape
persisted by ``UserState.set_last_for_mode``) into a ready
:class:`~kanjire.game.config.GameConfig`, so the Kivy UI launches byte-
identical game configs from the same saved state.
"""
from __future__ import annotations

from kanjire.game.config import DEFAULT_FACES, GameConfig, PRESETS

LEVELS = (5, 4, 3, 2, 1)
SIZES = (4, 6, 8, 12, 24)
WRITING_OPTIONS = (("off", "WRITE_HORIZ"), ("random", "WRITE_MIX"),
                   ("all", "WRITE_VERT"))
REPEAT_OPTIONS = (1, 2, 3, 5)
KANA_LENGTHS = (1, 2, 3)
#: Kana deck scripts — the values kana.sample() speaks natively.
#: "hira" = hiragana-only pairs, "kata" = katakana-only, "both" = one word
#: shown in hiragana AND katakana (match them across scripts).
KANA_SCRIPTS = (("hira", "KANA_SCRIPT_HIRA"),
                ("kata", "KANA_SCRIPT_KATA"),
                ("both", "KANA_SCRIPT_BOTH"))
#: The Kivy UI shipped long names that kana.sample() never understood (it
#: silently fell back to "both") — translate them on read forever.
KANA_SCRIPT_ALIASES = {"hiragana": "hira", "katakana": "kata",
                       "mixed": "both"}
LEARN_STEPS = (0, 1, 2, 3)
#: The clustering affinity dials share the learn dials' 0-3 scale, so both
#: UIs can reuse the same four-way selector widget and label set.
AFFINITY_STEPS_UI = (0, 1, 2, 3)
#: settings key -> GameConfig field, for the three clustering dials.
AFFINITY_KEYS = {"aff_meaning": "AFF_MEANING", "aff_looks": "AFF_LOOKS",
                 "aff_sound": "AFF_SOUND"}
HEARTS_OPTIONS = (2, 3, 5)
HEARTS_MAX = {2: 4, 3: 5, 5: 6}
BOUNTY_OPTIONS = (("none", "BOUNTY_NONE"), ("low", "BOUNTY_LOW"),
                  ("med", "BOUNTY_MED"), ("high", "BOUNTY_HIGH"))
BOUNTY_CHANCE = {"none": 0.0, "low": 0.35, "med": 0.6, "high": 0.9}

#: Stable mode keys → translation keys for their displayed labels.
MODE_TR = {
    "Time Attack": "MODE_TIME",
    "Survival": "MODE_SURVIVAL",
    "Zen": "MODE_ZEN",
    "Recall": "MODE_RECALL",
}

#: Labels for the built-in presets (still localised like modes were).
PRESET_TR = {
    "Familiarize": "MODE_FAMILIAR",
    "Learn": "MODE_LEARN",
}

#: The three modes on the front row of the Play tab, plus the ``+`` that makes
#: a custom one. Six equal buttons was too many to choose between, so the row
#: now shows the three people actually start from; everything else moved one
#: row down as a *factory mode* rather than being removed. Nothing became
#: unreachable and no saved state changed meaning.
FRONT_MODES = ("Time Attack", "Survival", "Learn")
#: Shipped configurations listed beside the player's own custom modes. Two are
#: rulesets (config.PRESETS keys) and one is a built-in preset — the menu
#: doesn't care, because ``config_for`` resolves either kind by name.
FACTORY_MODES = ("Zen", "Recall", "Familiarize")


def second_row_modes(state) -> list[str]:
    """Factory modes then the player's own, as displayed under the front row."""
    try:
        user = [p["name"] for p in state.presets if p.get("name")]
    except Exception:
        user = []
    seen = set(FACTORY_MODES)
    return list(FACTORY_MODES) + [n for n in user if n not in seen]

#: The former Familiarize/Learn modes, reborn honestly: one-tap
#: configurations of the Zen ruleset, listed beside user-saved presets.
#: Same dict shape as a saved preset (PRESET_FIELDS subset + name).
BUILTIN_PRESETS: list[dict] = [
    {
        "name": "Familiarize",
        "duration": None, "max_mistakes": None, "mismatch_penalty": 0,
        "words_per_round": 5, "repetitions": 3,
        "random_fonts": True, "vertical_writing": "random",
        "learn_known": 0, "learn_less_known": 0, "learn_unknown": 0,
    },
    {
        "name": "Learn",
        "duration": None, "max_mistakes": None, "mismatch_penalty": 0,
        "words_per_round": 6,
        "learn_known": 1, "learn_less_known": 2, "learn_unknown": 3,
    },
]


def preset_from_config(cfg, name: str) -> dict:
    """A saved-preset dict from a :class:`GameConfig`, under a new *name*.

    Both UIs serialise presets through here so a mode saved on the phone and
    one saved on the computer carry exactly the same fields.
    """
    out: dict = {}
    for f in PRESET_FIELDS:
        v = getattr(cfg, f, None)
        if isinstance(v, tuple):
            v = list(v)
        out[f] = v
    out["name"] = name
    return out


def all_presets(state) -> list[dict]:
    """Built-in presets first, then the user's saved ones."""
    try:
        user = list(state.presets)
    except Exception:
        user = []
    return [dict(p) for p in BUILTIN_PRESETS] + user


def preset_overlay(preset: dict) -> dict:
    """The per-mode settings-dict overlay a preset implies (for seeding the
    option rows when a preset is first selected)."""
    out: dict = {}
    for key in ("board_size", "repetitions", "random_fonts",
                "vertical_writing", "learn_known", "learn_less_known",
                "learn_unknown", "kana_length", "kana_script",
                "recall_prompt", "recall_preview",
                "aff_meaning", "aff_looks", "aff_sound"):
        if key in preset:
            out[key] = preset[key]
    if "words_per_round" in preset:
        out["board_size"] = preset["words_per_round"]
    if isinstance(preset.get("faces"), (list, tuple)):
        faces = normalize_faces(preset["faces"])
        if faces:
            out["faces"] = faces
    if isinstance(preset.get("decks"), (list, tuple)) and preset["decks"]:
        out["decks"] = list(preset["decks"])
    if isinstance(preset.get("levels"), (list, tuple)) and preset["levels"]:
        out["levels"] = list(preset["levels"])
    if isinstance(preset.get("genres"), (list, tuple)):
        out["genres"] = list(preset["genres"])
    return out

#: Canonical face order; selections are stored as ordered subsets of this.
FACE_ORDER = ("kanji", "reading", "romaji", "meaning")

#: (face, translation key, FACE_COLORS key) for the toggle buttons.
FACE_OPTIONS = (("kanji", "FACE_BTN_KANJI"),
                ("reading", "FACE_BTN_READING"),
                ("romaji", "FACE_BTN_ROMAJI"),
                ("meaning", "FACE_BTN_MEANING"))

#: Legacy 2/3/4 "cards per word" → face subsets (pre-toggle settings).
FACES_BY_MODE = {
    2: ("kanji", "meaning"),
    3: ("kanji", "reading", "meaning"),
    4: ("kanji", "reading", "romaji", "meaning"),
}


def normalize_faces(value) -> list[str] | None:
    """An ordered, valid faces subset (>= 2 faces), or None if unusable."""
    if not isinstance(value, (list, tuple)):
        return None
    picked = [f for f in FACE_ORDER if f in value]
    return picked if len(picked) >= 2 else None

#: GameConfig fields a saved user preset preserves.
PRESET_FIELDS = (
    "decks", "levels", "faces", "words_per_round", "frequency_bias",
    "duration", "max_mistakes", "base_points", "mismatch_penalty",
    "round_bonus", "repetitions", "random_fonts", "vertical_writing",
    "learn_known", "learn_less_known", "learn_unknown",
    "lives_mode", "start_lives", "max_lives", "heart_chance",
    "recall_mode", "recall_prompt", "recall_preview",
    "name",
)

#: The per-mode settings dict, with defaults (UserState.last_for_mode shape).
#: "decks" is an ordered multi-select (union word pool); the kana deck is
#: generative and therefore exclusive — it never mixes with DB decks.
#: The legacy single "deck" key is kept as a mirror (= decks[0]) so older
#: clients on the same sync account still read a sensible value.
DEFAULT_SETTINGS = {
    "decks": ["jlpt"],
    "levels": [5],
    "board_size": 6,
    "faces": ["kanji", "reading", "romaji", "meaning"],
    "random_fonts": False,
    "vertical_writing": "off",
    "repetitions": 1,
    "learn_known": 1,
    "learn_less_known": 2,
    "learn_unknown": 1,
    "kana_length": 1,
    "kana_script": "both",
    "start_hearts": 3,
    "bounty_freq": "med",
    "recall_prompt": "mixed",
    "recall_preview": True,
    # Clustering: no genre filter, no affinity, unless the player asks.
    "genres": [],
    "aff_meaning": 0,
    "aff_looks": 0,
    "aff_sound": 0,
}


def normalized_settings(d: dict | None) -> dict:
    """A full settings dict: persisted values where valid, defaults elsewhere."""
    s = dict(DEFAULT_SETTINGS)
    d = d or {}
    decks = d.get("decks")
    if not (isinstance(decks, (list, tuple)) and decks):
        decks = [d["deck"]] if isinstance(d.get("deck"), str) else []
    decks = [x for x in decks if isinstance(x, str) and x]
    from kanjire.kana import KANA_DECK
    if KANA_DECK in decks:
        decks = [KANA_DECK]          # generative: never mixes with DB decks
    if decks:
        s["decks"] = list(dict.fromkeys(decks))
    s["deck"] = s["decks"][0]        # legacy mirror for older clients
    levels = [lv for lv in d.get("levels", []) if lv in LEVELS]
    if levels:
        s["levels"] = sorted(levels)
    if d.get("board_size") in SIZES:
        s["board_size"] = int(d["board_size"])
    faces = normalize_faces(d.get("faces"))
    if faces is None and d.get("face_mode") in (2, 3, 4):
        faces = list(FACES_BY_MODE[int(d["face_mode"])])   # legacy 2/3/4
    if faces is None and "faces3" in d:  # even older: pre-4-card settings
        faces = list(FACES_BY_MODE[3 if d["faces3"] else 2])
    if faces is not None:
        s["faces"] = faces
    if "random_fonts" in d:
        s["random_fonts"] = bool(d["random_fonts"])
    if d.get("vertical_writing") in {v for v, _ in WRITING_OPTIONS}:
        s["vertical_writing"] = d["vertical_writing"]
    if d.get("repetitions") in REPEAT_OPTIONS:
        s["repetitions"] = int(d["repetitions"])
    for k in ("learn_known", "learn_less_known", "learn_unknown"):
        if d.get(k) in LEARN_STEPS:
            s[k] = int(d[k])
    if d.get("kana_length") in KANA_LENGTHS:
        s["kana_length"] = int(d["kana_length"])
    ks = d.get("kana_script")
    ks = KANA_SCRIPT_ALIASES.get(ks, ks)
    if ks in {v for v, _ in KANA_SCRIPTS}:
        s["kana_script"] = ks
    if d.get("start_hearts") in HEARTS_MAX:
        s["start_hearts"] = int(d["start_hearts"])
    if d.get("bounty_freq") in BOUNTY_CHANCE:
        s["bounty_freq"] = d["bounty_freq"]
    if d.get("recall_prompt") in ("typed", "listen", "both", "choice",
                                  "mixed"):
        s["recall_prompt"] = d["recall_prompt"]
    if "recall_preview" in d:
        s["recall_preview"] = bool(d["recall_preview"])
    from kanjire.data.genres import valid_genres
    s["genres"] = list(valid_genres(d.get("genres")))
    for key in AFFINITY_KEYS:
        if d.get(key) in AFFINITY_STEPS_UI:
            s[key] = int(d[key])
    return s


def config_for(mode: str, settings: dict, *,
               user_presets: list[dict] | None = None) -> GameConfig:
    """Build the GameConfig for *mode* with the (normalized) *settings*.

    *mode* is a PRESETS key or the name of a saved preset in *user_presets*.
    """
    s = normalized_settings(settings)
    from kanjire.kana import KANA_DECK
    if KANA_DECK in s["decks"]:
        # Kana mode decides its own faces from the script choice:
        #   hira / kata -> 2-face board (script + romaji)
        #   both        -> 3-face board (hira + kata + romaji)
        faces = (("kanji", "reading", "meaning")
                 if s["kana_script"] == "both" else ("kanji", "meaning"))
    else:
        faces = tuple(s["faces"]) or DEFAULT_FACES
    levels = tuple(s["levels"]) if "jlpt" in s["decks"] else ()

    if mode in PRESETS:
        base = PRESETS[mode]()
    else:
        # Built-in presets first (they can't be shadowed), then the user's.
        pool = BUILTIN_PRESETS + list(user_presets or [])
        data = next((p for p in pool if p.get("name") == mode), None)
        base = GameConfig()
        if data:
            for f in PRESET_FIELDS:
                if f in data and hasattr(base, f):
                    v = data[f]
                    if f in ("decks", "levels", "faces") and isinstance(v, list):
                        v = tuple(v)
                    setattr(base, f, v)

    return base.with_(
        decks=tuple(s["decks"]), levels=levels, faces=faces,
        words_per_round=s["board_size"],
        random_fonts=s["random_fonts"],
        vertical_writing=s["vertical_writing"],
        repetitions=s["repetitions"],
        learn_known=s["learn_known"],
        learn_less_known=s["learn_less_known"],
        learn_unknown=s["learn_unknown"],
        kana_length=s["kana_length"],
        kana_script=s["kana_script"],
        start_lives=s["start_hearts"],
        max_lives=HEARTS_MAX[s["start_hearts"]],
        heart_chance=BOUNTY_CHANCE[s["bounty_freq"]],
        recall_prompt=s["recall_prompt"],
        recall_preview=s["recall_preview"],
        genres=tuple(s["genres"]),
        aff_meaning=s["aff_meaning"],
        aff_looks=s["aff_looks"],
        aff_sound=s["aff_sound"],
        name=mode,
    )
