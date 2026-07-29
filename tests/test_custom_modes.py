"""Custom modes: save, list, resolve, delete — the ＋ button's whole life.

The Play tab's ＋ turns the settings on screen into a named mode, and a red
button deletes it again. Both UIs drive that through UserState + menuconfig,
so the contract is testable without a toolkit.
"""
from __future__ import annotations

import json

import pytest

from kanjire.game import menuconfig as mc
from kanjire.game.config import PRESETS
from kanjire.userstate import UserState


@pytest.fixture
def state(tmp_path):
    return UserState(tmp_path / "user_state.json")


def test_a_saved_mode_round_trips_its_clustering(state):
    """Saving then re-selecting a mode must bring its clustering back.

    Settings are the source of truth and a preset *seeds* them (that's what
    ``preset_overlay`` is for, and what both UIs' ``_settings_for`` does on
    first selection) — so the round trip has to go through the overlay, not
    straight into config_for, or the saved values are silently replaced by
    the defaults.
    """
    cfg = mc.config_for("Zen", {"genres": ["food"], "aff_looks": 3,
                                "aff_sound": 1, "board_size": 8})
    state.save_preset(mc.preset_from_config(cfg, "Lookalikes"))

    saved = next(p for p in state.presets if p["name"] == "Lookalikes")
    seeded = mc.preset_overlay(saved)
    assert seeded["genres"] == ["food"], "the overlay dropped the genre filter"

    back = mc.config_for("Lookalikes", seeded, user_presets=state.presets)
    assert back.genres == ("food",)
    assert back.aff_looks == 3
    assert back.aff_sound == 1
    assert back.name == "Lookalikes"
    assert back.words_per_round == 8


def test_a_saved_mode_keeps_its_ruleset(state):
    """A mode saved from Survival must still play by Survival's rules."""
    cfg = mc.config_for("Survival", {})
    state.save_preset(mc.preset_from_config(cfg, "My Survival"))
    back = mc.config_for("My Survival", {}, user_presets=state.presets)
    assert back.lives_mode is True

    cfg = mc.config_for("Recall", {})
    state.save_preset(mc.preset_from_config(cfg, "My Recall"))
    back = mc.config_for("My Recall", {}, user_presets=state.presets)
    assert back.recall_mode is True
    # recall_preview used to be dropped: menu.py had its own stale field list.
    assert hasattr(back, "recall_preview")


def test_preset_from_config_covers_every_preserved_field():
    cfg = mc.config_for("Zen", {})
    data = mc.preset_from_config(cfg, "X")
    for field in mc.PRESET_FIELDS:
        assert field in data, f"{field} would be lost when saving a mode"
    # And it must survive a trip through user_state.json.
    assert json.loads(json.dumps(data))["name"] == "X"


def test_second_row_lists_factory_modes_then_the_users(state):
    state.save_preset({"name": "Mine"})
    rows = mc.second_row_modes(state)
    assert rows[:len(mc.FACTORY_MODES)] == list(mc.FACTORY_MODES)
    assert rows[-1] == "Mine"


def test_front_and_factory_modes_are_all_resolvable(state):
    for name in (*mc.FRONT_MODES, *mc.FACTORY_MODES):
        cfg = mc.config_for(name, {}, user_presets=state.presets)
        assert cfg.name == name


def test_deleting_a_mode_removes_it(state):
    state.save_preset({"name": "Temp"})
    assert "Temp" in mc.second_row_modes(state)
    state.delete_preset("Temp")
    assert "Temp" not in mc.second_row_modes(state)


def test_a_custom_mode_cannot_shadow_a_built_in(state):
    """config_for resolves built-ins first, so a same-named custom mode would
    be unreachable — both UIs refuse the name, and this pins why."""
    state.save_preset({"name": "Zen", "words_per_round": 24})
    cfg = mc.config_for("Zen", {}, user_presets=state.presets)
    assert cfg.duration is None          # the real Zen ruleset, not the copy
    assert "Zen" in PRESETS


def test_second_row_never_duplicates_a_factory_name(state):
    state.save_preset({"name": "Zen"})
    rows = mc.second_row_modes(state)
    assert rows.count("Zen") == 1


def test_second_row_survives_a_broken_state():
    class Broken:
        @property
        def presets(self):
            raise RuntimeError("corrupt")

    assert mc.second_row_modes(Broken()) == list(mc.FACTORY_MODES)
