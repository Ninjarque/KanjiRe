"""Scripted end-to-end smoke test of the Kivy UI (boots the real app).

Plays an actual game: taps PLAY, completes a group card-by-card, forces a
mismatch, clears a full round, then forces time-out to reach the results
overlay — screenshotting each state. Exits non-zero on any failed checkpoint.

Usage: python tests/kivy_smoke.py [outdir]
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(tempfile.gettempdir()) / "kanjire_kivy_smoke"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("KANJIRE_KIVY_SIZE", "420x900")
os.environ.setdefault("KANJIRE_USER_DIR", str(OUT / "_userstate"))

import kanjire.kivyui.app as A  # noqa: E402
from kivy.clock import Clock  # noqa: E402

CHECKS: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(("  ok  " if ok else " FAIL ") + name)
    if not ok:
        FAILED.append(name)


def snap(app, name: str) -> None:
    app.screenshot(str(OUT / f"{name}.png"))


def main() -> None:
    app = A.KanjiReApp()

    def run_script(_dt):
        try:
            script(app)
        except Exception:
            traceback.print_exc()
            FAILED.append("script raised")
            app.stop()

    def script(app):
        from kanjire.game.engine import Phase

        # 1. Play tab renders.
        check("play tab is current", app.sm.current == "play")
        snap(app, "01-play")

        # 2. Start a default game.
        app.go_game()
        check("game screen shown", app.sm.current == "game")
        gs = app.sm.get_screen("game")
        e = gs.engine
        check("engine playing", e is not None and e.phase is Phase.PLAYING)
        check("board has cards", len(gs._cards) == len(e.board))

        def tap(cid):
            gs._on_card(gs._cards[cid])

        def later(delay, fn):
            Clock.schedule_once(lambda *_: fn(), delay)

        # 3. Complete group 0 card by card.
        def step_complete_group():
            snap(app, "02-board")
            ids = list(e.group_cards[0])
            for cid in ids:
                tap(cid)
            done = all(e.cards[cid].matched for cid in ids)
            check("group 0 completes", done)
            check("score positive", e.score > 0)
            snap(app, "03-group-complete")
            later(0.3, step_mismatch)

        # 4. Force a mismatch: one card of group 1 + one of group 2.
        def step_mismatch():
            before = e.mistakes
            tap(e.group_cards[1][0])
            tap(e.group_cards[2][0])
            check("mismatch registered", e.mistakes == before + 1)
            snap(app, "04-mismatch")
            later(0.4, step_clear_round)

        # 5. Clear the rest of the round -> next round deals.
        def step_clear_round():
            rounds_before = e.rounds_completed
            for g, ids in enumerate(e.group_cards):
                for cid in ids:
                    if not e.cards[cid].matched:
                        tap(cid)
            check("round completed", e.rounds_completed == rounds_before + 1)
            # _next_round is scheduled 0.55s out; give it time then verify.
            later(1.0, step_after_deal)

        def step_after_deal():
            check("new board dealt", len(gs._cards) == len(e.board)
                  and all(not c.matched for c in e.board_cards))
            snap(app, "05-next-round")
            step_timeout()

        # 6. Force time-out -> results overlay.
        def step_timeout():
            e.time_left = 0.05
            later(0.6, step_results)

        def step_results():
            check("game over", e.phase is Phase.GAME_OVER)
            check("overlay shown", gs._overlay is not None)
            snap(app, "06-results")
            later(0.3, finish)

        def finish():
            gs._quit()
            check("back on play tab", app.sm.current == "play")
            check("nav visible again", app.nav.height > 0)
            check("game recorded in history",
                  len(app.stats.game_history()) >= 1)
            app.switch_tab("stats")
            later(0.4, step_stats)

        def step_stats():
            check("stats tab shown", app.sm.current == "stats")
            snap(app, "07-stats")
            ss = app.sm.get_screen("stats")
            ss._set_view("history")
            check("history rows listed", len(ss._rv.data) >= 1)
            check("history rows tappable",
                  all(d.get("row_id") for d in ss._rv.data))
            ss._set_view("words")
            # Live theme swap must rebuild every tab without error.
            from kanjire.ui import theme as _theme
            app.sm.get_screen("settings")._set_palette("Paper")
            later(0.4, lambda: step_theme(_theme))

        def step_theme(_theme):
            check("palette applied", _theme.current_palette() == "Paper")
            check("kept on settings tab", app.sm.current == "settings")
            snap(app, "08-settings-paper")
            app.sm.get_screen("settings")._set_palette("Charcoal")
            later(0.3, step_friends)

        def step_friends():
            # Offline friends tab: inject a pending request + a live friend.
            fr = app.friends
            fr.pending_in["ABC123"] = "Testo"
            app.state.add_friend("DEF456", "Amigo")
            fr.presence["DEF456"] = {"name": "Amigo", "status": "lobby",
                                     "room": "XYZAB", "seen": fr._now or 1}
            app.switch_tab("friends")
            later(0.5, step_friends_check)

        def step_friends_check():
            fs = app.sm.get_screen("friends")
            rows = len(fs._body.children)
            check("friends tab shows rows", rows >= 3)
            listed = app.friends.friends()
            check("friend presence merged",
                  any(f["code"] == "DEF456" and f["status"] == "lobby"
                      for f in listed))
            snap(app, "09-friends")
            app.state.remove_friend("DEF456")
            app.friends.pending_in.clear()
            app.switch_tab("journey")
            later(0.5, step_journey)

        def step_journey():
            js = app.sm.get_screen("journey")
            check("journey stations built", len(js.stations) > 100)
            check("journey grid rendered", len(js._grid.children)
                  == len(js.stations))
            snap(app, "10-journey")
            app.switch_tab("read")
            later(0.6, step_reading)

        def step_reading():
            rs = app.sm.get_screen("read")
            check("reading session built", rs.session is not None)
            # With this test profile's small known set the room may be empty
            # or serving +1 sentences — either way the screen must render.
            has_sentence = rs.session.current is not None
            check("reading renders (sentence or empty state)",
                  has_sentence or rs._empty.text != "")
            if has_sentence:
                before = rs.session.current["id"]
                rs._next()
                after = rs.session.current["id"] if rs.session.current else None
                check("reading advances", after != before)
            else:
                check("reading advances", True)  # nothing to advance through
            snap(app, "11-reading")
            step_recall()

        def step_recall():
            from kanjire.game.config import PRESETS
            cfg = PRESETS["Recall"]().with_(words_per_round=2,
                                            recall_prompt="typed")
            app.go_game(cfg)
            later(0.6, step_recall_answer)

        def step_recall_answer():
            check("recall screen shown", app.sm.current == "recall")
            rs = app.sm.get_screen("recall")
            check("recall sampled words", len(rs.words) == 2)
            snap(app, "12-recall")
            # Answer both words correctly (kana passes through the converter).
            rs._input.text = rs.word.reading
            rs._submit()
            check("first word scored", rs.engine.score > 0)
            later(1.0, step_recall_second)

        def step_recall_second():
            rs = app.sm.get_screen("recall")
            if rs.word is not None:
                rs._input.text = rs.word.reading
                rs._submit()
            later(1.2, step_recall_done)

        def step_recall_done():
            rs = app.sm.get_screen("recall")
            check("recall results shown", rs._overlay is not None)
            check("recall all matched", rs.engine.matches == 2)
            snap(app, "13-recall-results")
            rs._quit()
            later(0.4, step_epilogue)

        def step_epilogue():
            # A *won* session with recall_words must chain into the
            # typed-recall epilogue (the Journey/Today flow).
            from kanjire.data import db as _db
            from kanjire.game.config import GameConfig
            pool = _db.load_words(app.con, decks=["jlpt"], levels=[5],
                                  require_kanji=True)[:2]
            cfg = GameConfig(name="Journey 1", decks=("jlpt",), levels=(),
                             words_per_round=2, duration=None,
                             max_mistakes=None, mismatch_penalty=0,
                             repetitions=1, session_mode=True)
            app.go_game(cfg, pool=pool, recall_words=pool[:1])
            later(0.6, step_epilogue_win)

        def step_epilogue_win():
            gs = app.sm.get_screen("game")
            e = gs.engine
            for ids in list(e.group_cards):
                for cid in ids:
                    if not e.cards[cid].matched:
                        gs._on_card(gs._cards[cid])
            later(1.2, step_epilogue_check)

        def step_epilogue_check():
            check("session win chains to recall epilogue",
                  app.sm.current == "recall")
            rs = app.sm.get_screen("recall")
            check("epilogue drills the queued word", len(rs.words) == 1)
            snap(app, "14-epilogue")
            rs._quit()
            later(0.4, step_back_key)

        def step_back_key():
            # Back/Esc: in a game -> previous menu; on a tab -> play tab;
            # on the play tab -> confirm-exit modal (unless opted out).
            app.go_game()
            later(0.5, step_back_in_game)

        def step_back_in_game():
            handled = app._on_hard_key(None, 27)
            check("back leaves the game", handled
                  and app.sm.current == "play")
            app.switch_tab("stats")
            handled = app._on_hard_key(None, 27)
            check("back returns to play tab", handled
                  and app.sm.current == "play")
            app.state.set_setting("back_confirm", "off")
            check("back exits directly when opted out",
                  app._on_hard_key(None, 27) is False)
            app.state.set_setting("back_confirm", "on")
            check("back asks before exiting",
                  app._on_hard_key(None, 27) is True)
            later(0.4, step_grid_stability)

        def step_grid_stability():
            snap(app, "15-exit-confirm")
            # Matching a group (which shows a sentence toast) must NOT move
            # any other card: the strip is an overlay, not a layout sibling.
            from kivy.uix.modalview import ModalView
            for w in list(app.root_window.children):
                if isinstance(w, ModalView):
                    w.dismiss()
            app.go_game()
            later(0.6, step_grid_check)

        def step_grid_check():
            gs = app.sm.get_screen("game")
            e = gs.engine
            others = {cid: tuple(w.pos) for cid, w in gs._cards.items()
                      if w.card.group != 0}
            n = len(gs._cards)
            cols = choose_grid_cols(n, gs)
            check("solo grid is a full rectangle", n % cols == 0)
            for cid in e.group_cards[0]:
                gs._on_card(gs._cards[cid])
            later(0.6, lambda: step_grid_after(gs, others))

        def choose_grid_cols(n, gs):
            from kivy.metrics import dp

            from kanjire.ui.layout import choose_grid
            cols, _r, _w, _h = choose_grid(n, gs.board.width,
                                           gs.board.height, gap=dp(10),
                                           prefer_exact=True)
            return cols

        def step_grid_after(gs, others):
            moved = [cid for cid, pos in others.items()
                     if tuple(gs._cards[cid].pos) != pos]
            check("matching never moves other cards", not moved)
            gs._quit()
            app.stop()

        later(0.4, step_complete_group)  # let the fade transition finish

    Clock.schedule_once(run_script, 1.0)
    app.run()

    print(f"\n[kivy_smoke] {len(CHECKS) - len(FAILED)}/{len(CHECKS)} checkpoints passed -> {OUT}")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
