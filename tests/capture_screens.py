"""Render menu / game / results to PNGs for visual inspection."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyglet
from pyglet.window import mouse

from kanjire.ui.app import GameApp

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT, exist_ok=True)


def main() -> int:
    app = GameApp()
    # Start each capture run from a clean slate: locale=en and no remembered
    # per-mode toggles. Otherwise a previous FR run leaks into the EN shots.
    from kanjire import i18n
    app.state.data["last_per_mode"] = {}
    app.state.set_locale("en")
    i18n.set_locale("en")
    app.go_menu()
    win = app.window
    # Keep the window visible: a hidden window's back-buffer is undefined on
    # Windows and captures as garbage.

    def render(n=8, dt=1 / 60.0):
        for _ in range(n):
            win.dispatch_events()
            app._tick(dt)
            win.switch_to()
            win.clear()
            app.scene.draw()
            win.flip()

    def shot(name):
        win.dispatch_events()
        win.switch_to()
        win.clear()
        app.scene.draw()
        win.flip()
        buf = pyglet.image.get_buffer_manager().get_color_buffer()
        path = os.path.join(OUT, name)
        buf.save(path)
        print("saved", path)

    render(12)
    shot("menu.png")
    # Advanced sub-tab: cards / fonts / writing / passes.
    app.scene._set_subtab("advanced")
    render(8)
    shot("menu_advanced.png")
    app.scene._set_subtab("quick")
    render(4)

    # Selecting Familiarize auto-syncs the toggles (Random fonts, Mix writing, 3×).
    app.scene._set_mode("Familiarize")
    render(8)
    shot("menu_familiarize.png")
    # Learn mode reveals the bucket-mix selectors (Known / Less known / Unknown)
    # on the Advanced sub-tab.
    app.scene._set_mode("Learn")
    render(8)
    shot("menu_learn.png")
    app.scene._set_subtab("advanced")
    render(8)
    shot("menu_learn_advanced.png")
    app.scene._set_subtab("quick")
    app.scene._set_mode("Time Attack")
    # Kana deck swaps JLPT LEVEL / CARDS PER WORD for KANA LENGTH / KANA SCRIPT.
    app.scene._set_deck("kana")
    render(8)
    shot("menu_kana.png")
    app.scene._set_deck("jlpt")
    render(6)

    app.scene._play()
    render(80)  # let entrance animation fully settle (stagger + back-ease)
    shot("game.png")

    # select a couple of cards to show selection glow, then a full group
    game = app.scene
    g0 = list(game.engine.group_cards[0])
    game.on_mouse_press(game.cards[g0[0]].cx, game.cards[g0[0]].cy, mouse.LEFT, 0)
    render(8)
    shot("game_selected.png")
    for cid in g0[1:]:
        cv = game.cards[cid]
        game.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
    render(6)
    shot("game_match.png")

    game.engine.time_left = 0.01
    render(80)
    shot("results.png")

    # Survival mode with the Wikipedia corpus deck (verify hearts + corpus words)
    from kanjire.data import db
    from kanjire.game.config import PRESETS
    decks = [r["name"] for r in db.list_decks(app.con)]
    corpus = next((d for d in decks if d.startswith("corpus:")), "jlpt")
    cfg = PRESETS["Survival"]().with_(decks=(corpus,), levels=(), words_per_round=6)
    app.go_game(cfg)
    render(80)
    shot("game_survival.png")

    # Familiarization mode: random fonts + random vertical writing
    cfg = PRESETS["Familiarize"]().with_(decks=("jlpt",), levels=(5,),
                                          words_per_round=5)
    app.go_game(cfg)
    render(80)
    shot("game_familiarize.png")

    # Batch-review board: 24 words × 2 faces = 48 cards on the board.
    cfg = PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5, 4),
                                 faces=("kanji", "meaning"),
                                 words_per_round=24)
    app.go_game(cfg)
    render(120)
    shot("game_24words.png")

    # Kana training: length=2, script=both → hira+kata+romaji cards.
    cfg = PRESETS["Zen"]().with_(
        decks=("kana",), levels=(),
        faces=("kanji", "reading", "meaning"),
        words_per_round=5, kana_length=2, kana_script="both",
    )
    app.go_game(cfg)
    render(80)
    shot("game_kana.png")

    # Generate a few sample stats by playing a quick round, then capture both
    # tabs of the StatsScene.
    app.stats.reset_all()
    cfg = PRESETS["Zen"]().with_(
        decks=("jlpt",), levels=(5,), words_per_round=8,
    )
    app.go_game(cfg)
    render(40)
    gs = app.scene
    for g in range(4):  # solve four groups
        for cid in list(gs.engine.group_cards[g]):
            cv = gs.cards[cid]
            gs.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
    # Make two deliberate mismatches on the meaning face.
    for (a, b) in ((4, 5), (6, 7)):
        ca = next(c for c in gs.cards.values()
                  if not c.model.matched and c.model.group == a
                  and c.model.face == "kanji")
        cb = next(c for c in gs.cards.values()
                  if not c.model.matched and c.model.group == b
                  and c.model.face == "meaning")
        gs.on_mouse_press(ca.cx, ca.cy, mouse.LEFT, 0)
        gs.on_mouse_press(cb.cx, cb.cy, mouse.LEFT, 0)
    render(20)
    app.go_stats()
    render(10)
    shot("stats_overview.png")
    app.scene._set_tab("Words")
    render(10)
    shot("stats_words.png")
    app.scene._set_tab("Kanji")
    render(10)
    shot("stats_kanji.png")
    app.stats.reset_all()

    # Settings scene (now includes the THEME palette picker)
    app.go_settings()
    render(10)
    shot("settings.png")

    # --- Palette sweep: verify contrast on the new light / monochrome / etc.
    #     themes (background flat, text readable, card faces distinguishable).
    from kanjire.ui import theme as _theme
    from kanjire.ui.app import _set_clear_color

    def set_palette(name):
        app.state.set_palette(name)
        _theme.apply_palette(name)
        _set_clear_color(_theme.BG)

    for pal, tag in (("Paper", "paper"), ("Monochrome", "mono"),
                     ("High Contrast", "contrast"), ("Vivid", "vivid")):
        set_palette(pal)
        app.go_menu()
        render(10)
        shot(f"menu_{tag}.png")
    # Card legibility under a light theme and a hue-free monochrome theme.
    pal_cfg = PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5,), words_per_round=6)
    set_palette("Paper")
    app.go_game(pal_cfg); render(80); shot("game_paper.png")
    set_palette("Monochrome")
    app.go_game(pal_cfg); render(80); shot("game_mono.png")
    # Settings + theme picker under the light theme.
    set_palette("Paper"); app.go_settings(); render(10); shot("settings_paper.png")
    set_palette("Charcoal")  # restore default before the FR section

    # --- Survival (gamified): 新 / ♥ / ¥ stickers + hearts HUD + the
    #     STARTING HEARTS / HEART BOUNTIES difficulty rows in the menu. ----- #
    app.stats.reset_all()
    _con = db.connect(read_only=True)
    try:
        seed_pool = db.load_words(_con, decks=["jlpt"], levels=[5],
                                  require_kanji=True)
    finally:
        _con.close()
    # Seed some learned-but-hard words so a bounty candidate can appear.
    for w in seed_pool[:8]:
        app.stats.saw(w)
        app.stats.matched(w)
        app.stats.confused(w, w, "reading")
    # Menu: Survival selected, Advanced sub-tab → difficulty rows visible.
    app.go_menu()
    app.scene._set_mode("Survival")
    app.scene._set_subtab("advanced")
    render(8)
    shot("menu_survival.png")
    # A Survival board started low on hearts so a heart bounty can spawn.
    cfg = PRESETS["Survival"]().with_(
        decks=("jlpt",), levels=(5,), words_per_round=6,
        start_lives=1, max_lives=4, heart_chance=1.0,
    )
    app.go_game(cfg)
    render(80)
    shot("game_survival_stickers.png")
    app.stats.reset_all()

    # French locale: refresh scenes to pick up the translated strings AND the
    # French meanings on the cards.
    from kanjire import i18n
    app.state.set_locale("fr")
    i18n.set_locale("fr")
    app.go_menu()
    render(10)
    shot("menu_fr.png")
    app.go_settings()
    render(10)
    shot("settings_fr.png")
    # A French game board to verify meaning cards show French glosses.
    cfg = PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5,), words_per_round=6)
    app.go_game(cfg)
    render(80)
    shot("game_fr.png")
    # restore
    app.state.set_locale("en")
    i18n.set_locale("en")

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
