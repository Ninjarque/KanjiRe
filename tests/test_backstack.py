"""The Android back stack: back returns where you came from.

Pure logic over a fake ScreenManager, so it runs without booting Kivy. The
rule under test: leaving a screen goes back to the previous one, and never
into a game or a drill.
"""
from __future__ import annotations

import pytest

from kanjire.kivyui.app import KanjiReApp


class _FakeSM:
    def __init__(self, screens, current="play"):
        self._screens = set(screens)
        self.current = current

    def has_screen(self, name):
        return name in self._screens


class _Nav:
    def __init__(self):
        self.active = None

    def set_active(self, name):
        self.active = name


@pytest.fixture
def app():
    """A bare app object with only what the nav verbs touch."""
    a = KanjiReApp.__new__(KanjiReApp)
    a._back_stack = []
    a.sm = _FakeSM({"play", "journey", "read", "stats", "friends",
                    "settings", "game", "recall", "multiplayer"})
    a.nav = _Nav()
    a._show_nav = lambda on: None
    return a


def test_back_returns_to_the_previous_screen(app):
    app.switch_tab("journey")
    assert app.sm.current == "journey"
    app.go_home()
    assert app.sm.current == "play"


def test_a_game_started_from_journey_returns_to_journey(app):
    app.switch_tab("journey")
    app._remember()                 # what go_game does
    app.sm.current = "game"
    app.go_home()
    assert app.sm.current == "journey", "a station dumped us on Play"


def test_back_never_lands_in_a_game(app):
    """A game must never be a back target, however we got here."""
    app.switch_tab("journey")
    app._remember()                 # go_game(): pushes "journey"
    app.sm.current = "game"
    app._remember()                 # a game trying to be remembered
    app.sm.current = "recall"
    app._remember()                 # so is a drill
    app.sm.current = "stats"
    app.go_home()
    assert app.sm.current == "journey"


def test_the_stack_unwinds_more_than_one_level(app):
    app.switch_tab("journey")
    app.switch_tab("stats")
    app.go_home()
    assert app.sm.current == "journey"
    app.go_home()
    assert app.sm.current == "play"


def test_an_empty_stack_falls_back_to_play(app):
    app.sm.current = "settings"
    app.go_home()
    assert app.sm.current == "play"


def test_back_never_returns_to_the_screen_we_are_on(app):
    app.switch_tab("journey")
    app.switch_tab("journey")       # a repeat tap must not stack a duplicate
    app.sm.current = "journey"
    app.go_home()
    assert app.sm.current == "play"


def test_a_missing_screen_is_skipped(app):
    app._back_stack = ["never_added", "journey"]
    app.sm.current = "stats"
    app.go_home()
    assert app.sm.current == "journey"
    app._back_stack = ["never_added"]
    app.sm.current = "stats"
    app.go_home()
    assert app.sm.current == "play"


def test_the_stack_is_bounded(app):
    for i in range(50):
        app.sm.current = "journey" if i % 2 else "stats"
        app._remember()
    assert len(app._back_stack) <= 12


def test_nav_highlight_follows_the_back_target(app):
    app.switch_tab("journey")
    app._remember()                 # go_game()
    app.sm.current = "game"
    app.go_home()
    assert app.sm.current == "journey"
    assert app.nav.active == "journey", "the nav bar still highlighted Play"
