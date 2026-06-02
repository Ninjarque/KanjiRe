"""Pure, GUI-free game rules: configuration and the match engine."""

from kanjire.game.config import GameConfig, PRESETS
from kanjire.game.engine import Card, GameEngine, Phase, SelectResult

__all__ = [
    "GameConfig",
    "PRESETS",
    "Card",
    "GameEngine",
    "Phase",
    "SelectResult",
]
