"""Render key scenes at small (1600x900) and large (2560x1440) window sizes to
verify the resolution scaling has no overlap / cropping and a sensible size."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyglet

from kanjire import i18n
from kanjire.game.config import PRESETS
from kanjire.ui.app import GameApp

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT, exist_ok=True)


def main() -> int:
    app = GameApp()
    app.state.data["last_per_mode"] = {}
    app.state.set_locale("en")
    i18n.set_locale("en")
    win = app.window

    def render(n: int = 10, dt: float = 1 / 60.0) -> None:
        for _ in range(n):
            win.dispatch_events()
            app._tick(dt)
            win.switch_to()
            win.clear()
            app.scene.draw()
            win.flip()

    def shot(name: str) -> None:
        win.dispatch_events()
        win.switch_to()
        win.clear()
        app.scene.draw()
        win.flip()
        buf = pyglet.image.get_buffer_manager().get_color_buffer()
        path = os.path.join(OUT, name)
        buf.save(path)
        print("saved", path, f"({win.width}x{win.height})")

    for (w, h), tag in (((1600, 900), "1600x900"), ((2560, 1440), "2560x1440")):
        win.set_size(w, h)
        render(6)
        app.go_menu()
        render(10)
        shot(f"res_{tag}_menu.png")
        app.scene._set_mode("Learn")
        app.scene._set_subtab("advanced")
        render(8)
        shot(f"res_{tag}_menu_adv_learn.png")
        app.scene._set_subtab("quick")
        app.scene._set_mode("Time Attack")

        # dense 24-word board (smallest cards) + a normal board
        cfg = PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5, 4),
                                     faces=("kanji", "meaning"), words_per_round=24)
        app.go_game(cfg)
        render(70)
        shot(f"res_{tag}_game24.png")
        cfg2 = PRESETS["Time Attack"]().with_(decks=("jlpt",), levels=(5,),
                                              words_per_round=6)
        app.go_game(cfg2)
        render(50)
        shot(f"res_{tag}_game.png")

        app.go_settings()
        render(10)
        shot(f"res_{tag}_settings.png")

        # seed a little stats data so the table tab has rows
        app.stats.reset_all()
        cfg3 = PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5,), words_per_round=8)
        app.go_game(cfg3)
        render(30)
        gs = app.scene
        from pyglet.window import mouse
        for g in range(3):
            for cid in list(gs.engine.group_cards[g]):
                cv = gs.cards[cid]
                gs.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
        render(15)
        app.go_stats()
        render(10)
        shot(f"res_{tag}_stats_overview.png")
        app.scene._set_tab("Words")
        render(10)
        shot(f"res_{tag}_stats_words.png")
        app.stats.reset_all()

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
