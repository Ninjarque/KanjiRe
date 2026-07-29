"""Options belong to the player, not to the mode.

Timer, hearts and the typing drill used to be welded to Time Attack,
Survival and Recall. They are settings now, so every mode (and every custom
one) can use them — without breaking the settings people already saved.
"""
from __future__ import annotations

import pytest

from kanjire.data.genres import GENRES, search
from kanjire.game import menuconfig as mc
from kanjire.userstate import UserState


@pytest.fixture
def state(tmp_path):
    return UserState(tmp_path / "user_state.json")


# --------------------------------------------------------------------------- #
# The three ruleset switches
# --------------------------------------------------------------------------- #
def test_untouched_settings_keep_the_modes_own_ruleset():
    """The None sentinel: settings saved before these options existed (and a
    player who never touches them) must play exactly the mode they picked."""
    assert mc.config_for("Time Attack", {}).duration == 120.0
    assert mc.config_for("Zen", {}).duration is None
    assert mc.config_for("Survival", {}).lives_mode is True
    assert mc.config_for("Zen", {}).lives_mode is False
    assert mc.config_for("Recall", {}).recall_mode is True
    assert mc.config_for("Zen", {}).recall_mode is False


@pytest.mark.parametrize("mode", ["Time Attack", "Survival", "Zen", "Learn"])
def test_any_mode_can_be_timed(mode):
    assert mc.config_for(mode, {"timer": 180}).duration == 180.0
    assert mc.config_for(mode, {"timer": 0}).duration is None


@pytest.mark.parametrize("mode", ["Time Attack", "Zen", "Learn", "Recall"])
def test_any_mode_can_have_hearts(mode):
    assert mc.config_for(mode, {"lives_mode": True}).lives_mode is True
    assert mc.config_for(mode, {"lives_mode": False}).lives_mode is False


@pytest.mark.parametrize("mode", ["Time Attack", "Survival", "Zen", "Learn"])
def test_any_mode_can_be_a_typing_drill(mode):
    assert mc.config_for(mode, {"recall_mode": True}).recall_mode is True


def test_time_attack_can_be_made_untimed():
    """The inverse matters too — the switch is not one-way."""
    assert mc.config_for("Time Attack", {"timer": 0}).duration is None


def test_bad_ruleset_values_fall_back_to_the_mode():
    for junk in ({"timer": 99}, {"timer": "long"}, {"lives_mode": "yes"},
                 {"recall_mode": 1}):
        cfg = mc.config_for("Time Attack", junk)
        assert cfg.duration == 120.0
        assert cfg.lives_mode is False
        assert cfg.recall_mode is False


def test_a_saved_mode_carries_its_ruleset_back(state):
    cfg = mc.config_for("Zen", {"timer": 60, "lives_mode": True,
                                "recall_mode": False})
    state.save_preset(mc.preset_from_config(cfg, "Blitz Zen"))
    saved = next(p for p in state.presets if p["name"] == "Blitz Zen")
    seeded = mc.preset_overlay(saved)
    assert seeded["timer"] == 60
    assert seeded["lives_mode"] is True
    back = mc.config_for("Blitz Zen", seeded, user_presets=state.presets)
    assert back.duration == 60.0 and back.lives_mode is True


# --------------------------------------------------------------------------- #
# Hiding and restoring modes
# --------------------------------------------------------------------------- #
def test_a_builtin_mode_can_be_hidden_and_restored(state):
    assert "Zen" in mc.second_row_modes(state)
    state.hide_mode("Zen")
    assert "Zen" not in mc.second_row_modes(state)
    assert state.restore_modes() is True
    assert "Zen" in mc.second_row_modes(state)


def test_hiding_a_front_mode_removes_it_from_the_front_row(state):
    state.hide_mode("Survival")
    assert "Survival" not in mc.visible_front_modes(state)
    assert "Time Attack" in mc.visible_front_modes(state)


def test_the_last_mode_standing_refuses_to_go(state):
    for name in (*mc.FRONT_MODES, *mc.FACTORY_MODES):
        if mc.can_hide(state, name):
            state.hide_mode(name)
    left = set(mc.visible_front_modes(state)) | set(mc.second_row_modes(state))
    assert left, "hiding everything left nothing to play"
    only = next(iter(left))
    assert mc.can_hide(state, only) is False


def test_restore_reports_nothing_to_do(state):
    assert state.restore_modes() is False


def test_hidden_modes_survive_a_reload(state, tmp_path):
    state.hide_mode("Recall")
    reloaded = UserState(tmp_path / "user_state.json")
    assert "Recall" in reloaded.hidden_modes


# --------------------------------------------------------------------------- #
# Genre search
# --------------------------------------------------------------------------- #
def test_empty_query_returns_every_genre():
    assert len(search("")) == len(GENRES)
    assert len(search("   ")) == len(GENRES)


def test_search_matches_key_label_and_icon():
    assert [g.key for g in search("food")] == ["food"]
    assert [g.key for g in search("食")] == ["food"]
    labels = {g.key: "Food & Drink" if g.key == "food" else g.key
              for g in GENRES}
    assert [g.key for g in search("drink", label_of=lambda g: labels[g.key])] \
        == ["food"]


def test_search_is_case_insensitive_and_can_miss():
    assert [g.key for g in search("FOOD")] == ["food"]
    assert search("zzzz") == []


def test_search_keeps_taxonomy_order():
    got = [g.key for g in search("a")]
    assert got == [g.key for g in GENRES if g.key in set(got)]
