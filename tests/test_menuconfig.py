"""The shared menu model must produce the same configs the pyglet menu does."""
from kanjire.game.config import PRESETS
from kanjire.game.menuconfig import (
    DEFAULT_SETTINGS,
    config_for,
    normalized_settings,
)


def test_defaults_time_attack():
    cfg = config_for("Time Attack", {})
    assert cfg.timed and cfg.duration == PRESETS["Time Attack"]().duration
    assert cfg.decks == ("jlpt",)
    assert cfg.levels == (5,)
    assert cfg.faces == ("kanji", "reading", "romaji", "meaning")
    assert cfg.words_per_round == DEFAULT_SETTINGS["board_size"]
    assert cfg.name == "Time Attack"


def test_survival_hearts_and_bounty():
    cfg = config_for("Survival", {"start_hearts": 5, "bounty_freq": "high"})
    assert cfg.lives_mode
    assert cfg.start_lives == 5 and cfg.max_lives == 6
    assert cfg.heart_chance == 0.9


def test_levels_only_apply_to_jlpt_deck():
    cfg = config_for("Zen", {"deck": "kana", "levels": [5, 4]})
    assert cfg.levels == ()
    cfg = config_for("Zen", {"deck": "jlpt", "levels": [4, 5]})
    assert cfg.levels == (4, 5)


def test_legacy_faces3_key():
    s = normalized_settings({"faces3": True})
    assert s["faces"] == ["kanji", "reading", "meaning"]
    s = normalized_settings({"faces3": False})
    assert s["faces"] == ["kanji", "meaning"]


def test_legacy_face_mode_maps_to_faces():
    s = normalized_settings({"face_mode": 2})
    assert s["faces"] == ["kanji", "meaning"]
    s = normalized_settings({"face_mode": 4})
    assert s["faces"] == ["kanji", "reading", "romaji", "meaning"]


def test_faces_subset_and_minimum():
    # Arbitrary subsets are allowed (reading↔meaning without kanji!),
    # order is canonicalised, and fewer than 2 falls back to the default.
    s = normalized_settings({"faces": ["meaning", "reading"]})
    assert s["faces"] == ["reading", "meaning"]
    cfg = config_for("Zen", {"faces": ["romaji", "kanji"]})
    assert cfg.faces == ("kanji", "romaji")
    s = normalized_settings({"faces": ["kanji"]})
    assert len(s["faces"]) == 4     # default kept
    s = normalized_settings({"faces": ["kanji", "bogus", "meaning"]})
    assert s["faces"] == ["kanji", "meaning"]


def test_invalid_values_fall_back_to_defaults():
    s = normalized_settings({"board_size": 999, "levels": [99],
                             "repetitions": 42, "start_hearts": 1})
    assert s["board_size"] == DEFAULT_SETTINGS["board_size"]
    assert s["levels"] == DEFAULT_SETTINGS["levels"]
    assert s["repetitions"] == DEFAULT_SETTINGS["repetitions"]
    assert s["start_hearts"] == DEFAULT_SETTINGS["start_hearts"]


def test_saved_preset_rehydrates():
    preset = {"name": "My Drill", "duration": 45.0, "max_mistakes": 2,
              "decks": ["jlpt"], "faces": ["kanji", "meaning"]}
    cfg = config_for("My Drill", {"face_mode": 2, "board_size": 4},
                     user_presets=[preset])
    assert cfg.duration == 45.0
    assert cfg.max_mistakes == 2
    assert cfg.words_per_round == 4
    assert cfg.name == "My Drill"


def test_recall_prompt_carries():
    cfg = config_for("Recall", {"recall_prompt": "listen"})
    assert cfg.recall_mode and cfg.recall_prompt == "listen"
