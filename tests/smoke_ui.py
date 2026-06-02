"""Drive the whole UI without a human: menu -> game -> solve/mismatch -> results.

Renders real frames into an (immediately hidden) pyglet window so any drawing,
layout, animation or scene-transition error surfaces as a non-zero exit code.
Run from the repo root:  python tests/smoke_ui.py
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run() -> int:
    import random
    import pyglet
    from pyglet.window import mouse

    from kanjire.game.config import PRESETS
    from kanjire.game.engine import Phase
    from kanjire.ui.app import GameApp
    from kanjire.ui.scenes.game import GameScene
    from kanjire.ui.scenes.menu import MenuScene
    from kanjire.ui.scenes.results import ResultsScene
    from kanjire.ui.scenes.stats import StatsScene

    app = GameApp()
    win = app.window
    win.set_visible(False)

    def frames(n: int, dt: float = 1 / 60.0) -> None:
        for _ in range(n):
            app._tick(dt)
            win.switch_to()
            win.clear()
            app.scene.draw()
            win.flip()

    def click(cx: float, cy: float) -> None:
        app.scene.on_mouse_press(cx, cy, mouse.LEFT, 0)

    # 1) Menu renders
    assert isinstance(app.scene, MenuScene), "did not start on menu"
    frames(10)
    print("PASS menu renders")

    # 2) Launch a game (default deck=jlpt, level N5). Force a timed mode so the
    #    timer-expiry → results transition in step 5 is deterministic no matter
    #    what the player's persisted last_mode is (an untimed Learn/Zen session
    #    would otherwise leave the timer trick a no-op).
    app.scene._set_mode("Time Attack")
    app.scene._play()
    assert isinstance(app.scene, GameScene), "play did not enter game"
    game = app.scene
    assert game.error is None, f"game reported error: {game.error}"
    assert game.cards, "no cards on board"
    frames(20)
    print(f"PASS game started ({len(game.cards)} cards, "
          f"{len(game.engine.round_words)} words)")

    # 3) Solve one group via simulated clicks at card centres
    g0 = list(game.engine.group_cards[0])
    for cid in g0:
        cv = game.cards[cid]
        click(cv.cx, cv.cy)
    frames(15)
    assert game.engine.matches >= 1, "group did not complete"
    assert game.engine.score > 0, "no score awarded"
    print(f"PASS group matched (score={game.engine.score}, combo={game.engine.best_combo})")

    # 4) Force a mismatch (two cards from different unmatched groups)
    a = next(c for c in game.cards.values() if not c.model.matched and c.model.group == 1)
    b = next(c for c in game.cards.values() if not c.model.matched and c.model.group == 2)
    click(a.cx, a.cy)
    click(b.cx, b.cy)
    frames(15)
    assert game.engine.mistakes >= 1, "mismatch not registered"
    print(f"PASS mismatch handled (mistakes={game.engine.mistakes})")

    # 5) End the game (run the timer out) and transition to results
    game.engine.time_left = 0.01
    frames(80)
    assert isinstance(app.scene, ResultsScene), f"did not reach results: {type(app.scene)}"
    frames(15)
    print("PASS results screen renders")

    # 6) Play again from results
    app.scene._again()
    assert isinstance(app.scene, GameScene), "play-again failed"
    frames(10)
    print("PASS play-again works")

    # 7) Import a tiny text file -> ingest -> back to menu
    import tempfile, time
    from pathlib import Path
    from kanjire.ui.scenes.import_text import ImportTextScene
    app.go_menu()
    frames(5)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt",
                                     encoding="utf-8") as fh:
        fh.write("私は新しい本を読みます。日本の音楽が好きです。")
        tiny = Path(fh.name)
    try:
        app.go_import(tiny, "smoke-tiny")
        assert isinstance(app.scene, ImportTextScene)
        deadline = time.time() + 30
        while time.time() < deadline and not isinstance(app.scene, MenuScene):
            frames(2)
        assert isinstance(app.scene, MenuScene), \
            f"import did not return to menu (scene={type(app.scene).__name__})"
        new_decks = {n for n, _ in app.scene.deck_btns}
        assert any(d.startswith("corpus:smoke-tiny") for d in new_decks), \
            f"new deck missing; have {new_decks}"
        print("PASS file import flow completes (deck added)")

        # 8) Same thing but via the pasted-text code path
        app.go_import_pasted(
            "私は新しい本を毎日読みます。日本の音楽が好きです。", "smoke-tiny-paste",
        )
        assert isinstance(app.scene, ImportTextScene)
        deadline = time.time() + 30
        while time.time() < deadline and not isinstance(app.scene, MenuScene):
            frames(2)
        assert isinstance(app.scene, MenuScene), "paste import did not return"
        new_decks = {n for n, _ in app.scene.deck_btns}
        assert any(d.startswith("corpus:smoke-tiny-paste") for d in new_decks), \
            f"paste deck missing; have {new_decks}"
        print("PASS paste import flow completes (deck added)")

        # 9) Save, load, then delete a custom preset
        menu = app.scene
        menu.random_fonts = True
        menu.vertical_writing = "all"
        menu.repetitions = 3
        cfg_dict = {
            **__import__("kanjire.ui.scenes.menu", fromlist=["_config_to_dict"])
                ._config_to_dict(menu._current_config()),
            "name": "smoke-preset",
        }
        app.state.save_preset(cfg_dict)
        app.go_menu()  # rebuilds menu with the saved preset visible
        names = {n for n, _ in app.scene.mode_btns}
        assert "smoke-preset" in names, f"preset not in mode row: {names}"
        # selecting it should restore toggle state
        app.scene._set_mode("smoke-preset")
        assert app.scene.random_fonts is True
        assert app.scene.vertical_writing == "all"
        assert app.scene.repetitions == 3
        assert app.state.delete_preset("smoke-preset")
        print("PASS preset save / select / delete")

        # 10) Per-mode persistence: a tweak on one mode survives a round-trip.
        app.go_menu()
        m = app.scene
        m._set_mode("Survival")
        m._set_size(24)             # batch review size
        m._set_repeat(5)            # 5× passes
        m._set_random_fonts(True)
        assert app.state.last_for_mode("Survival")["board_size"] == 24
        m._set_mode("Time Attack")
        assert m.board_size != 24 or m.repetitions != 5  # different mode
        m._set_mode("Survival")
        assert m.board_size == 24, f"Survival board_size lost: {m.board_size}"
        assert m.repetitions == 5, f"Survival repetitions lost: {m.repetitions}"
        assert m.random_fonts is True
        # Survive a fresh GameApp - simulates relaunching the program.
        app.audio.shutdown()
        win.close()
        del app
        app2 = GameApp()
        app2.window.set_visible(False)
        assert isinstance(app2.scene, MenuScene)
        # Last-active mode persists too: we just set Survival above, so a fresh
        # GameApp should open the menu on Survival, not on Time Attack.
        assert app2.scene.mode == "Survival", \
            f"last_mode not persisted (mode={app2.scene.mode!r})"
        assert app2.scene.board_size == 24
        assert app2.scene.repetitions == 5
        # Clean up persisted state for these modes so the test is idempotent.
        for mode in ("Time Attack", "Survival"):
            app2.state.data.get("last_per_mode", {}).pop(mode, None)
        app2.state.data.get("settings", {}).pop("last_mode", None)
        app2.state.save()
        # Keep app2 alive through the remaining tests so they have a window.
        print("PASS per-mode persistence across app restart")

        # 11) 12 and 24 words boards: engine deals correctly, layout doesn't crash.
        from kanjire.game.config import GameConfig as _GC
        from kanjire.game.engine import GameEngine as _GE
        from kanjire.data import db as _db2
        con = _db2.connect(read_only=True)
        try:
            pool = _db2.load_words(con, decks=["jlpt"], levels=[5, 4],
                                    require_kanji=True)
        finally:
            con.close()
        for n in (12, 24):
            eng = _GE(_GC(decks=("jlpt",), levels=(5, 4),
                          faces=("kanji", "reading", "meaning"),
                          words_per_round=n, duration=None,
                          max_mistakes=None), pool)
            eng.start()
            assert len(eng.round_words) == n
            assert len(eng.board_cards) == n * 3
        print("PASS engine handles 12 and 24 words")

        # 12) Stats actually get recorded during play, then visible in StatsScene.
        # Wipe the stats DB first so this run's numbers are deterministic.
        app2.stats.reset_all()
        # Local frame loop bound to the *new* app instance (the original
        # 'frames' helper closes over the now-deleted first app).
        def frames2(n=1, dt=1/60.0):
            for _ in range(n):
                app2.window.dispatch_events()
                app2._tick(dt)
                app2.window.switch_to()
                app2.window.clear()
                app2.scene.draw()
                app2.window.flip()

        app2.go_game(PRESETS["Survival"]().with_(
            decks=("jlpt",), levels=(5,), words_per_round=4,
            duration=None, max_mistakes=10,
        ))
        gs = app2.scene
        assert isinstance(gs, GameScene)
        # Solve group 0 successfully
        for cid in list(gs.engine.group_cards[0]):
            gs.on_mouse_press(gs.cards[cid].cx, gs.cards[cid].cy, mouse.LEFT, 0)
        # Cause a mismatch between groups 1 and 2 on the meaning face
        m1 = next(c for c in gs.cards.values()
                  if not c.model.matched and c.model.group == 1
                  and c.model.face == "kanji")
        m2 = next(c for c in gs.cards.values()
                  if not c.model.matched and c.model.group == 2
                  and c.model.face == "meaning")
        gs.on_mouse_press(m1.cx, m1.cy, mouse.LEFT, 0)
        gs.on_mouse_press(m2.cx, m2.cy, mouse.LEFT, 0)
        # Check the DB: the target (group 1) and offending (group 2) words got
        # mistakes_meaning incremented, and group 0 has 1 match.
        w0 = gs.engine.round_words[0]
        w1 = gs.engine.round_words[1]
        w2 = gs.engine.round_words[2]
        s0 = app2.stats.get_for(w0.expression, w0.reading)
        s1 = app2.stats.get_for(w1.expression, w1.reading)
        s2 = app2.stats.get_for(w2.expression, w2.reading)
        assert s0 and s0["matches"] == 1, f"matched not recorded: {s0}"
        assert s1 and s1["mistakes_meaning"] == 1, f"target not flagged: {s1}"
        assert s2 and s2["mistakes_meaning"] == 1, f"offender not flagged: {s2}"
        # All four round words got at least one 'seen' event.
        for w in gs.engine.round_words:
            row = app2.stats.get_for(w.expression, w.reading)
            assert row and row["seen"] >= 1, f"not seen: {w.expression}"
        print("PASS stats recorded from gameplay")

        # 13) Stats scene renders both tabs.
        app2.go_stats()
        assert isinstance(app2.scene, StatsScene)
        frames2(8)
        # switch to Words tab
        app2.scene._set_tab("Words")
        frames2(8)
        assert len(app2.scene._filtered["Words"]) > 0
        # Search filters
        app2.scene._on_search("Words", "あ")
        assert all("あ" in (r["reading"] or "")
                    for r in app2.scene._filtered["Words"]
                    if r.get("reading"))
        app2.scene._on_search("Words", "")
        # Kanji tab works
        app2.scene._set_tab("Kanji")
        frames2(4)
        assert len(app2.scene._filtered["Kanji"]) > 0
        app2.scene._set_tab("Overview")
        frames2(4)
        print("PASS stats scene Overview + Words render")

        # 14) Learn mode picks from the right buckets when we have stats data.
        # Seed: 6 different words seen + matched but also flubbed (=> genuinely
        # "less_known": a clean single match now classifies as "known", so we
        # add a mistake to keep them in the struggle bucket) and 6 words never
        # seen ("unknown"). Pool is the union of both.
        app2.stats.reset_all()
        from kanjire.data import db as _db3
        con3 = _db3.connect(read_only=True)
        try:
            n5_pool = _db3.load_words(con3, decks=["jlpt"], levels=[5],
                                       require_kanji=True)
        finally:
            con3.close()
        for w in n5_pool[:6]:
            app2.stats.saw(w)
            app2.stats.matched(w)
            app2.stats.confused(w, w, "reading")  # a mistake -> less_known
        from kanjire.model.sampling import learn_sample_words
        buckets = app2.stats.classify_words(n5_pool)
        # All-known mix should yield only less_known/known words from our seed.
        round_words = learn_sample_words(
            n5_pool, 4,
            buckets=buckets,
            weights={"known": 0, "less_known": 6, "unknown": 0},
            rng=random.Random(42),
        )
        assert len(round_words) == 4
        seen_keys = {(w.expression, w.reading) for w in n5_pool[:6]}
        assert all((w.expression, w.reading) in seen_keys for w in round_words), \
            "less_known weight should pull from already-seen words"
        # Unknown-only mix should avoid the seeded words.
        round_words = learn_sample_words(
            n5_pool, 4,
            buckets=buckets,
            weights={"known": 0, "less_known": 0, "unknown": 6},
            rng=random.Random(42),
        )
        assert all((w.expression, w.reading) not in seen_keys for w in round_words), \
            "unknown weight should avoid already-seen words"
        print("PASS learn sampler respects bucket mix")

        # 15) Kana training mode: synthetic words, no DB, all three scripts work.
        from kanjire import kana
        # Sampling honours length and script
        for length in (1, 2, 3):
            words = kana.sample(6, length=length, script="both",
                                 rng=random.Random(12 + length))
            assert len(words) == 6
            for w in words:
                assert len(w.meaning.split()) == length, \
                    f"length {length}: got romaji {w.meaning!r}"
                # In "both" script, expression is hiragana and reading is katakana.
                assert any(0x3040 <= ord(ch) <= 0x309F for ch in w.expression), \
                    f"expression not hiragana: {w.expression!r}"
                assert any(0x30A0 <= ord(ch) <= 0x30FF for ch in w.reading), \
                    f"reading not katakana: {w.reading!r}"
            # All distinct
            assert len({w.expression for w in words}) == 6
        # Plays one round end-to-end via GameApp using the Kana deck.
        cfg = PRESETS["Zen"]().with_(
            decks=("kana",), levels=(), faces=("kanji", "reading", "meaning"),
            words_per_round=6, kana_length=2, kana_script="both",
        )
        app2.go_game(cfg)
        gs2 = app2.scene
        assert isinstance(gs2, GameScene)
        assert len(gs2.engine.round_words) == 6
        assert all(w.deck == kana.KANA_DECK for w in gs2.engine.round_words)
        # Solve the first group to confirm engine + matching work on synthetic words.
        for cid in list(gs2.engine.group_cards[0]):
            cv = gs2.cards[cid]
            gs2.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
        assert gs2.engine.matches >= 1
        print("PASS kana mode sampling + matching")

        # Don't leave the test's stats behind in the shipped DB.
        app2.stats.reset_all()
        app2.audio.shutdown()
        app2.window.close()
        return 0  # finished with these checks; final cleanup below not needed
    finally:
        try:
            tiny.unlink()
        except OSError:
            pass
        # Don't pollute the shipped database with our test decks.
        from kanjire.data import db as _db

        con = _db.connect()
        try:
            for pat in ("corpus:smoke-tiny%", "corpus:smoke-tiny-paste%"):
                con.execute(f"DELETE FROM words WHERE deck LIKE '{pat}'")
                con.execute(f"DELETE FROM kanji WHERE deck LIKE '{pat}'")
                con.execute(f"DELETE FROM decks WHERE name LIKE '{pat}'")
            con.commit()
        finally:
            con.close()

    win.close()
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
