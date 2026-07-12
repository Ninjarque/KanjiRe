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
    from pyglet.window import key as pkey, mouse

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
        # v0.7: imports also capture sentences for the Reading Room.
        n_sent = app.con.execute(
            "SELECT COUNT(*) FROM corpus_sentences WHERE deck LIKE "
            "'corpus:smoke-tiny%'").fetchone()[0]
        assert n_sent >= 1, "import captured no Reading Room sentences"
        print("PASS file import flow completes (deck + sentences added)")

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

        # 13b) Activity heatmap has cells and today registers step 12's events
        # (1 match + 2 confuse rows); the word-detail overlay opens and closes.
        sc = app2.scene
        assert len(sc._heat_cells) >= 7, "heatmap cells missing"
        assert app2.stats.reviews_today() >= 3, \
            f"review_log did not record gameplay: {app2.stats.reviews_today()}"
        sc._set_tab("Words")
        frames2(4)
        sc._open_detail(sc._filtered["Words"][0])
        assert sc._detail_open and len(sc._detail_widgets) > 5
        frames2(6)
        sc.on_key_press(pkey.ESCAPE, 0)
        assert not sc._detail_open, "ESC did not close detail overlay"
        frames2(2)
        print("PASS heatmap + word detail overlay")

        # 13c) The search box as a PLAYER uses it: click it, type, hit Enter.
        # Three shipped bugs live here - Enter was inserted into the query as a
        # literal character (an empty box on Linux), the filtered rows were
        # created but never positioned (so nothing changed until you resized the
        # window), and the text didn't scale with the box.
        sc._set_tab("Words")
        frames2(4)
        box = sc._search["Words"]
        # Type a character we KNOW is in the data (a hard-coded あ silently
        # matched nothing and made the assertions vacuous).
        needle = next(r["reading"][0] for r in sc._filtered["Words"]
                      if r.get("reading"))
        box.focus()
        sc.on_text(needle)
        frames2(2)
        assert box.text == needle, f"typing didn't reach the search box: {box.text!r}"
        assert sc._query["Words"] == needle, "typing didn't run the filter"
        assert sc._filtered["Words"], f"search for {needle!r} matched nothing"
        assert sc._row_labels, "no row labels after searching"
        assert any(lbl.y > 0 for lbl in sc._row_labels), \
            "filtered rows were never laid out (the 'resize the window' bug)"
        sc.on_text("\r")
        sc.on_text("\n")
        frames2(2)
        assert box.text == needle, \
            f"Enter/newline leaked into the query as text: {box.text!r}"
        box.set_scale(2.0)
        box.set_rect(40, 100, 360, 40)
        assert box._fs > box._base_fs, "search text does not scale with the UI"
        box.set_text("")
        box.unfocus()
        sc._set_tab("Overview")
        frames2(2)
        print("PASS stats search box (typing, Enter, live relayout, scaling)")

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

        # 15b) Four-face boards: the yellow romaji card joins each group and
        # carries the converter's output; a 4-card group still matches.
        cfg4 = PRESETS["Zen"]().with_(
            decks=("jlpt",), levels=(5,),
            faces=("kanji", "reading", "romaji", "meaning"),
            words_per_round=4,
        )
        app2.go_game(cfg4)
        g4 = app2.scene
        assert isinstance(g4, GameScene)
        assert len(g4.engine.board_cards) == 16, len(g4.engine.board_cards)
        rc4 = [c for c in g4.engine.board_cards if c.face == "romaji"]
        assert len(rc4) == 4
        w0 = g4.engine.round_words[rc4[0].group]
        assert rc4[0].text == kana.hira_to_romaji(w0.reading), rc4[0].text
        for cid in list(g4.engine.group_cards[0]):
            cv = g4.cards[cid]
            g4.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
        assert g4.engine.matches >= 1, "4-card group did not match"
        print("PASS four-face romaji board")

        # 16) Results "practice tricky words": mismatches during a game surface
        # as struggled words, and the practice button replays exactly them.
        app2.stats.reset_all()
        cfg3 = PRESETS["Time Attack"]().with_(decks=("jlpt",), levels=(5,),
                                              words_per_round=4)
        app2.go_game(cfg3)
        gp = app2.scene
        assert isinstance(gp, GameScene)

        def _mismatch(ga: int, gb: int) -> None:
            ca = next(c for c in gp.cards.values()
                      if not c.model.matched and c.model.group == ga)
            cb = next(c for c in gp.cards.values()
                      if not c.model.matched and c.model.group == gb)
            gp.on_mouse_press(ca.cx, ca.cy, mouse.LEFT, 0)
            gp.on_mouse_press(cb.cx, cb.cy, mouse.LEFT, 0)

        _mismatch(0, 1)
        _mismatch(2, 3)
        assert len(gp.tally.struggled()) >= 4, gp.tally.struggled()
        gp.engine.time_left = 0.01
        frames2(90)
        rs = app2.scene
        assert isinstance(rs, ResultsScene), f"no results: {type(rs).__name__}"
        assert rs.practice_btn is not None, "practice button missing"
        struggled_keys = {(w.expression, w.reading) for w in rs.struggled}
        rs._practice()
        gpr = app2.scene
        assert isinstance(gpr, GameScene), "practice did not start a game"
        assert gpr.engine.round_words, "practice round empty"
        assert all((w.expression, w.reading) in struggled_keys
                   for w in gpr.engine.round_words), \
            "practice round contains non-struggled words"
        frames2(10)
        print("PASS practice-tricky-words rematch")

        # 16b) Game history: the finished game above was logged; the History
        # tab lists it and clicking a row replays exactly its words.
        hist = app2.stats.game_history()
        assert hist and hist[0]["mode"] == "Time Attack", hist[:1]
        assert hist[0]["n_words"] >= 2
        app2.go_stats()
        sh = app2.scene
        sh._set_tab("History")
        frames2(6)
        assert sh._filtered["History"], "history tab empty"
        row0 = sh._filtered["History"][0]
        sh._replay_game(row0)
        gr = app2.scene
        assert isinstance(gr, GameScene) and gr.config.session_mode
        replay_keys = {(w.expression, w.reading) for w in gr.engine.pool}
        assert replay_keys == set(row0["word_keys"]) & replay_keys
        assert all((w.expression, w.reading) in set(row0["word_keys"])
                   for w in gr.engine.round_words)
        frames2(6)
        print("PASS game history + replay")

        # 17) Today's Training end-to-end: seeded due reviews + new words form
        # the plan, the session completes and stamps the streak.
        app2.stats.reset_all()
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        con4 = _db3.connect(read_only=True)
        try:
            n5b = _db3.load_words(con4, decks=["jlpt"], levels=[5],
                                  require_kanji=True)
        finally:
            con4.close()
        past = _dt.now(_tz.utc) - _td(days=2)
        for w in n5b[:3]:
            app2.stats.srs.update(w.expression, w.reading, 3, past)
        assert app2.stats.srs.due_count() >= 3, "seeded words not due"
        app2.go_menu()
        menu2 = app2.scene
        plan = menu2._get_today_plan()
        assert len(plan.reviews) >= 3, f"plan reviews: {len(plan.reviews)}"
        assert plan.new_words, "plan should offer new words"
        assert menu2.today_btn.enabled
        menu2._play_today()
        gts = app2.scene
        assert isinstance(gts, GameScene) and gts.config.session_mode
        assert gts.engine.session_left == len(plan.pool)
        rounds_guard = 0
        while isinstance(app2.scene, GameScene) and rounds_guard < 25:
            g = app2.scene
            for grp in range(len(g.engine.round_words)):
                for cid in list(g.engine.group_cards[grp]):
                    cv = g.cards.get(cid)
                    if cv is not None and not cv.model.matched:
                        g.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
            frames2(90)   # round-clear animation + next round / recall
            rounds_guard += 1
        # A won session with reviews flows into the typed-recall epilogue.
        from kanjire.ui.scenes.recall import RecallScene
        rc = app2.scene
        assert isinstance(rc, RecallScene), f"expected recall: {type(rc).__name__}"
        w0 = rc.word
        rc.input.set_text(w0.reading)      # kana input passes straight through
        rc._submit()
        frames2(55)                        # 0.7s auto-advance
        w1 = rc.word
        assert w1 is not None and w1.expression != w0.expression
        rc.input.set_text("zzz")
        rc._submit()                       # wrong once -> retry offered
        assert rc.attempts == 1 and isinstance(app2.scene, RecallScene)
        rc.input.set_text("zzz")
        rc._submit()                       # wrong twice -> answer shown
        frames2(110)                       # 1.6s auto-advance
        rc.on_key_press(pkey.ESCAPE, 0)    # finish the epilogue early
        rs2 = app2.scene
        assert isinstance(rs2, ResultsScene), f"stuck in {type(rs2).__name__}"
        assert rs2.session_won, "session did not register as won"
        recall_rows = [dict(r) for r in app2.stats.con.execute(
            "SELECT rating FROM review_log WHERE event='recall'")]
        assert any(r["rating"] == 4 for r in recall_rows), \
            f"typed recall not graded Easy: {recall_rows}"
        st = app2.state.streak_status()
        assert st["count"] >= 1 and st["done_today"], f"streak not stamped: {st}"
        app2.go_menu()
        frames2(4)
        assert "○" in app2.scene.today_btn.text, \
            "menu button did not flip to done/bonus state"
        # Clean the streak stamp back out of the persisted state.
        for k in ("streak_count", "streak_freezes", "streak_day"):
            app2.state.data.get("settings", {}).pop(k, None)
        app2.state.save()
        print("PASS today session end-to-end (plan, session, streak)")

        # 18) Reading Room: with a known N5 vocabulary, i+1 sentences appear,
        # translation toggles, reading is logged, chips open the word popup.
        from kanjire.data import kanjidata as _kd
        if _kd.SENTENCES_PATH.exists():
            app2.stats.reset_all()
            con5 = _db3.connect(read_only=True)
            try:
                n5all = _db3.load_words(con5, decks=["jlpt"], levels=[5, 4],
                                        require_kanji=True)
            finally:
                con5.close()
            app2.stats.mark_known(n5all)
            app2.go_reading()
            rd = app2.scene
            frames2(8)
            assert rd.current is not None, "no readable sentence found"
            assert rd.current["unknown"] <= 2
            assert rd.chips, "no word chips"
            rd._toggle_translation()
            assert rd.translation.text, "translation did not show"
            before = app2.stats.reading_totals()["sentences"]
            first_id = rd.current["id"]
            rd._next()
            frames2(4)
            assert app2.stats.reading_totals()["sentences"] == before + 1
            assert rd.current is None or rd.current["id"] != first_id
            # chip popup opens and closes
            rd.chips[0][0].click()
            assert rd._pop_open
            frames2(4)
            rd.on_key_press(pkey.ESCAPE, 0)
            assert not rd._pop_open
            # Corpus source: the smoke-tiny import (all-N5 words, known by
            # the mark_known above) should serve its own sentences.
            corpus_src = next((k for k, _n in rd.sources
                               if k.startswith("corpus:smoke-tiny")), None)
            assert corpus_src, f"import source missing: {rd.sources}"
            rd._set_source(corpus_src)
            frames2(4)
            assert rd.current is not None, "no readable corpus sentence"
            assert not rd.trans_btn.enabled, "own text has no translation"
            rd._set_source("tanaka")
            print("PASS reading room (i+1 feed, translation, log, popup, corpus)")
        else:
            print("SKIP reading room (sentences.db not built)")

        # 19) Journey map: stations built from the frequency road, frontier
        # detected, a station session launches over exactly its words.
        from kanjire.ui.scenes.journey import JourneyScene
        app2.go_journey()
        jn = app2.scene
        frames2(8)
        assert isinstance(jn, JourneyScene)
        assert len(jn.stations) > 100, f"stations: {len(jn.stations)}"
        # Step 18 marked N5+N4 known, so plenty of road words are known
        # (stations mix levels, so the frontier itself may still be early).
        assert sum(jn._known_counts) > 400, sum(jn._known_counts)
        assert 0 <= jn.frontier < len(jn.stations)
        assert jn._node_buttons, "no station nodes"
        target = jn.frontier
        station_keys = {(w.expression, w.reading)
                        for w in jn.stations[target]}
        jn._play_station(target)
        gj = app2.scene
        assert isinstance(gj, GameScene) and gj.config.session_mode
        assert all((w.expression, w.reading) in station_keys
                   for w in gj.engine.round_words)
        frames2(6)
        print("PASS journey map + station session")

        # 20) Leech hunt appears on Stats once words lapse repeatedly.
        app2.stats.reset_all()
        for w in n5all[:6]:
            for _ in range(5):
                app2.stats.confused(w, w, "reading")
        app2.go_stats()
        sc2 = app2.scene
        frames2(6)
        assert sc2.leech_btn is not None, "leech hunt button missing"
        sc2._start_leech_hunt()
        gl = app2.scene
        assert isinstance(gl, GameScene)
        assert gl.config.lives_mode and gl.config.session_mode
        leech_keys = {(w.expression, w.reading) for w in sc2._leech_words}
        assert all((w.expression, w.reading) in leech_keys
                   for w in gl.engine.round_words)
        frames2(6)
        print("PASS leech bounty hunt")

        # 21) Multiplayer end-to-end: the app hosts (in-process server), a
        # second player joins over a raw socket, and a shared-board turn
        # cycle runs: host completes a group, friend mismatches.
        import json as _json
        import socket as _sock
        from kanjire.net.server import DEFAULT_PORT, PROTOCOL
        from kanjire.ui.scenes.multiplayer import MultiplayerScene

        app2.go_multiplayer()
        mp = app2.scene
        assert isinstance(mp, MultiplayerScene)
        frames2(4)
        mp.in_name.set_text("hosty")
        mp.in_addr.set_text("127.0.0.1")   # advanced: direct-server path
        mp._set_turns(5)
        mp._host()
        guard = 0
        while mp.phase != "lobby" and guard < 150:
            frames2(2)
            guard += 1
        assert mp.phase == "lobby", f"host stuck: {mp.status!r}"
        assert len(mp.room) == 5

        fs = _sock.create_connection(("127.0.0.1", DEFAULT_PORT), timeout=5)
        fs.settimeout(5)
        ff = fs.makefile("rb")

        def fsend(obj):
            fs.sendall((_json.dumps(obj) + "\n").encode("utf-8"))

        def frecv_state():
            while True:
                m = _json.loads(ff.readline())
                if m["t"] == "state":
                    return m["state"]
                assert m["t"] != "error", m

        fsend({"t": "hello", "name": "friend", "proto": PROTOCOL})
        assert _json.loads(ff.readline())["t"] == "welcome"
        fsend({"t": "join", "room": mp.room})
        assert _json.loads(ff.readline())["player"] == 1
        frecv_state()
        guard = 0
        while (not mp.state or len(mp.state.get("players", [])) < 2) \
                and guard < 100:
            frames2(2)
            guard += 1
        assert mp.state["players"] == ["hosty", "friend"]

        mp._start()
        fst = frecv_state()          # start broadcast reaches the friend
        # board = words x cards-per-word; both come from the room settings, so
        # derive it (romaji-on-by-default already broke a hard-coded 18 here).
        expect = (int(fst["settings"]["board_size"])
                  * int(fst["settings"]["cards"]))
        assert fst["started"] and len(fst["board"]) == expect, \
            f"{len(fst['board'])} cards, expected {expect}"
        guard = 0
        while mp.phase != "play" and guard < 100:
            frames2(2)
            guard += 1
        assert mp.phase == "play" and len(mp.cards) == expect

        # Host's turn: click one full group through the real scene input.
        g0 = mp.state["board"][0]["group"]
        ids = [c["id"] for c in mp.state["board"] if c["group"] == g0]
        for cid in ids:
            cv = mp.cards[cid]
            mp.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
            frames2(4)
        for _ in range(len(ids)):
            fst = frecv_state()
        guard = 0
        while (mp.state["scores"][0] != 100) and guard < 100:
            frames2(2)
            guard += 1
        assert mp.state["scores"][0] == 100, mp.state["scores"]
        assert mp.state["combos"][0] == 1
        assert mp.state["turn"] == 1
        assert len(mp.state["board"]) == expect   # refilled from the pool

        # Friend's turn: a mismatch resets their combo and passes the turn.
        s_now = fst
        ga = s_now["board"][0]["group"]
        a_id = next(c["id"] for c in s_now["board"] if c["group"] == ga)
        b_id = next(c["id"] for c in s_now["board"] if c["group"] != ga)
        fsend({"t": "select", "card": a_id})
        frecv_state()
        fsend({"t": "select", "card": b_id})
        fst = frecv_state()
        assert fst["turn"] == 0 and fst["turns_used"] == 2
        guard = 0
        while mp.state["turn"] != 0 and guard < 100:
            frames2(2)
            guard += 1
        assert mp.state["turn"] == 0, "host did not get the turn back"

        for closer in (ff.close, fs.close):
            try:
                closer()
            except OSError:
                pass
        mp._leave()
        assert isinstance(app2.scene, MenuScene)
        frames2(4)
        print("PASS multiplayer host + join + turn cycle (direct server)")

        # 22) Code-only multiplayer through the UI: the scene creates a room
        # over the relay (loopback broker here) and a friend joins with the
        # 5-letter code ALONE - no address anywhere - then plays a turn.
        from kanjire.net.room_client import RoomClient
        from kanjire.net.transport import LoopbackBroker, LoopbackTransport

        broker = LoopbackBroker()
        app2.go_multiplayer()
        mp2 = app2.scene
        mp2.in_name.set_text("hosty")
        mp2.in_addr.set_text("")          # code-only path
        orig_make = mp2._make_client

        def _relay_client(addr):
            c = RoomClient(transport=LoopbackTransport(broker))
            assert c.connect(mp2._my_name()) is None
            mp2.client = c
            return c
        mp2._make_client = _relay_client
        mp2._host()
        frames2(6)
        assert mp2.phase == "lobby" and len(mp2.room) == 5, mp2.room
        code = mp2.room

        friend = RoomClient(transport=LoopbackTransport(broker))
        assert friend.connect("friend") is None
        friend.send({"t": "join", "room": code.lower()})   # code only!
        assert any(m.get("t") == "welcome" and m.get("player") == 1
                   for m in friend.poll())
        frames2(6)
        assert mp2.state["players"] == ["hosty", "friend"]

        # Lobby settings: the host tunes the game and the friend sees it live.
        mp2._set_setting("cards", 4)
        mp2._set_setting("board_size", 4)
        frames2(4)
        fset = [m["state"] for m in friend.poll()
                if m.get("t") == "state"][-1]["settings"]
        assert fset["cards"] == 4 and fset["board_size"] == 4, fset
        assert mp2._settings()["cards"] == 4
        # ...and the settings buttons everyone sees reflect it.
        assert next(b for n, b in mp2.cards_btns if n == 4).selected
        assert not next(b for n, b in mp2.cards_btns if n == 3).selected

        # Presentation settings (writing direction + fonts), shared with the
        # single-player Advanced tab. Every row must SHOW its selection - a row
        # whose buttons are all unhighlighted looks broken (and was).
        mp2._set_setting("writing", "all")
        mp2._set_setting("fonts", "random")
        frames2(4)
        fset = [m["state"] for m in friend.poll()
                if m.get("t") == "state"][-1]["settings"]
        assert fset["writing"] == "all" and fset["fonts"] == "random", fset
        for _lbl, btns in (("writing", mp2.writing_btns),
                           ("fonts", mp2.fonts_btns),
                           ("cards", mp2.cards_btns),
                           ("turns", mp2.lturns_btns)):
            assert any(b.selected for _v, b in btns), \
                f"the {_lbl} row shows no selection at all"
        assert next(b for v, b in mp2.writing_btns if v == "all").selected
        assert next(b for v, b in mp2.fonts_btns if v == "random").selected
        # Everyone must see the SAME board, so the look can't be rolled locally.
        mp2.room = "TESTS"
        style = mp2._card_style({"id": 7, "face": "kanji"})
        assert style == mp2._card_style({"id": 7, "face": "kanji"}), \
            "card styling is not deterministic - players would see different boards"
        assert style[1] == "vertical", style
        mp2._set_setting("writing", "off")
        mp2._set_setting("fonts", "fixed")
        frames2(4)

        mp2._start()
        frames2(6)
        # 4 words x 4 faces (romaji card included) = 16 cards for everyone.
        assert mp2.phase == "play" and len(mp2.cards) == 16, len(mp2.cards)
        assert any(c.face == "romaji" for c in mp2.cards.values())
        fstates = [m["state"] for m in friend.poll() if m.get("t") == "state"]
        assert fstates and fstates[-1]["started"]
        assert [c["id"] for c in fstates[-1]["board"]] == \
            [c["id"] for c in mp2.state["board"]], "boards differ!"

        # Host takes its turn through real UI clicks.
        g0 = mp2.state["board"][0]["group"]
        for cid in [c["id"] for c in mp2.state["board"] if c["group"] == g0]:
            cv = mp2.cards[cid]
            mp2.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
            frames2(3)
        assert mp2.state["scores"][0] == 100 and mp2.state["turn"] == 1
        fs2 = [m["state"] for m in friend.poll() if m.get("t") == "state"][-1]
        assert fs2["scores"] == [100, 0] and fs2["turn"] == 1
        assert len(fs2["board"]) == 16, "board did not refill for everyone"

        # Friend's turn: their match lands on the host's board too.
        gf = fs2["board"][0]["group"]
        for cid in [c["id"] for c in fs2["board"] if c["group"] == gf]:
            friend.send({"t": "select", "card": cid})
        friend.poll()
        frames2(6)
        assert mp2.state["scores"][1] == 100, mp2.state["scores"]
        assert mp2.state["turn"] == 0

        # Host pauses: clicks are dead for everyone, and the friend is told.
        mp2._pause()
        frames2(4)
        assert mp2.state["paused"] is True
        fs3 = [m["state"] for m in friend.poll() if m.get("t") == "state"][-1]
        assert fs3["paused"] is True
        score_before = mp2.state["scores"][0]
        turn_owner = mp2.state["turn"]
        if turn_owner == 0:
            cv = next(iter(mp2.cards.values()))
            mp2.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
            frames2(4)
            assert not any(c.model.selected for c in mp2.cards.values()), \
                "a paused board accepted a click"
        assert mp2.state["scores"][0] == score_before

        # Back to the room settings: lobby again, scores cleared, players kept.
        mp2._to_lobby()
        frames2(6)
        assert mp2.phase == "lobby", mp2.phase
        assert mp2.state["scores"] == [0, 0]
        assert mp2.state["players"] == ["hosty", "friend"]
        # Settings are editable again and a new game uses them.
        mp2._set_setting("cards", 2)
        frames2(4)
        mp2._start()
        frames2(6)
        assert mp2.phase == "play"
        assert all(c.face in ("kanji", "meaning") for c in mp2.cards.values())

        friend.close()
        mp2._make_client = orig_make
        mp2._leave()
        frames2(4)
        print("PASS multiplayer code-only + live lobby settings + pause")

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
                con.execute(
                    "DELETE FROM corpus_sentence_words WHERE sentence_id IN "
                    f"(SELECT id FROM corpus_sentences WHERE deck LIKE '{pat}')")
                con.execute(
                    f"DELETE FROM corpus_sentences WHERE deck LIKE '{pat}'")
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
