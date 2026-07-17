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
LEARN_STEPS = (0, 1, 2, 3)
HEARTS_OPTIONS = (2, 3, 5)
HEARTS_MAX = {2: 4, 3: 5, 5: 6}
BOUNTY_OPTIONS = (("none", "BOUNTY_NONE"), ("low", "BOUNTY_LOW"),
                  ("med", "BOUNTY_MED"), ("high", "BOUNTY_HIGH"))
BOUNTY_CHANCE = {"none": 0.0, "low": 0.35, "med": 0.6, "high": 0.9}

#: Stable preset keys → translation keys for their displayed labels.
MODE_TR = {
    "Time Attack": "MODE_TIME",
    "Survival": "MODE_SURVIVAL",
    "Zen": "MODE_ZEN",
    "Familiarize": "MODE_FAMILIAR",
    "Learn": "MODE_LEARN",
    "Recall": "MODE_RECALL",
}

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
DEFAULT_SETTINGS = {
    "deck": "jlpt",
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
    "kana_script": "hiragana",
    "start_hearts": 3,
    "bounty_freq": "med",
    "recall_prompt": "mixed",
    "recall_preview": True,
}


def normalized_settings(d: dict | None) -> dict:
    """A full settings dict: persisted values where valid, defaults elsewhere."""
    s = dict(DEFAULT_SETTINGS)
    d = d or {}
    if isinstance(d.get("deck"), str):
        s["deck"] = d["deck"]
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
    if d.get("kana_script") in ("hiragana", "katakana", "mixed"):
        s["kana_script"] = d["kana_script"]
    if d.get("start_hearts") in HEARTS_MAX:
        s["start_hearts"] = int(d["start_hearts"])
    if d.get("bounty_freq") in BOUNTY_CHANCE:
        s["bounty_freq"] = d["bounty_freq"]
    if d.get("recall_prompt") in ("typed", "listen", "both", "choice",
                                  "mixed"):
        s["recall_prompt"] = d["recall_prompt"]
    if "recall_preview" in d:
        s["recall_preview"] = bool(d["recall_preview"])
    return s


def config_for(mode: str, settings: dict, *,
               user_presets: list[dict] | None = None) -> GameConfig:
    """Build the GameConfig for *mode* with the (normalized) *settings*.

    *mode* is a PRESETS key or the name of a saved preset in *user_presets*.
    """
    s = normalized_settings(settings)
    faces = tuple(s["faces"]) or DEFAULT_FACES
    levels = tuple(s["levels"]) if s["deck"] == "jlpt" else ()

    if mode in PRESETS:
        base = PRESETS[mode]()
    else:
        data = next((p for p in (user_presets or []) if p.get("name") == mode),
                    None)
        base = GameConfig()
        if data:
            for f in PRESET_FIELDS:
                if f in data and hasattr(base, f):
                    v = data[f]
                    if f in ("decks", "levels", "faces") and isinstance(v, list):
                        v = tuple(v)
                    setattr(base, f, v)

    return base.with_(
        decks=(s["deck"],), levels=levels, faces=faces,
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
        name=mode,
    )
