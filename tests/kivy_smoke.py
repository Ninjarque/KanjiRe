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

# DPI-unaware for dev captures: under Windows display scaling (125%…) SDL's
# per-monitor DPI mode makes Kivy's logical size diverge from the pixel
# framebuffer and Window.screenshot shears diagonally.
os.environ.setdefault("SDL_WINDOWS_DPI_AWARENESS", "unaware")
os.environ.setdefault("KIVY_METRICS_DENSITY", "1")

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

        # The script hops screens faster than the 0.12s fade: a transition
        # still in flight makes the ScreenManager swallow the window-level
        # touches below (and stacked same-frame transitions can wedge it).
        # Fades are cosmetic — run the smoke without them.
        from kivy.uix.screenmanager import NoTransition
        app.sm.transition = NoTransition()

        # 0. The progress fill on a button: a genre tile shows HOW FAR you
        # are, not just that you started (gold-for-started read as "done").
        from kanjire.kivyui.widgets import ThemedButton as _PB
        probe = _PB(text="x")
        probe.size = (200, 40)
        probe.set_progress(None)
        check("no progress bar by default", probe._prog_rect.size[0] == 0)
        probe.set_progress(0.5)
        check("progress bar fills half", abs(probe._prog_rect.size[0] - 100) < 1)
        probe.set_progress(2.0)          # clamped, never overflows the button
        check("progress bar clamps to full",
              abs(probe._prog_rect.size[0] - 200) < 1)
        probe.set_progress(0.0)
        check("zero progress draws nothing", probe._prog_rect.size[0] == 0)

        # 0b. Update banner: every child must sit INSIDE the bar. The
        # buttons were 52dp tall in a 46dp bar (ThemedButton's default
        # height beat their explicit size=) and stuck out of the top.
        from kivy.base import EventLoop as _EL
        app.banner.opacity, app.banner.disabled = 1, False
        for _ in range(6):
            _EL.idle()
        outside = [c for c in app.banner.children
                   if c.y < app.banner.y - 0.5 or c.top > app.banner.top + 0.5]
        check("update banner buttons stay inside the bar", not outside)
        app.banner.opacity, app.banner.disabled = 0, True

        # 1. Play tab renders.
        check("play tab is current", app.sm.current == "play")
        snap(app, "01-play")

        # 1b. WINDOW-LEVEL dispatch: taps must reach bottom-band buttons.
        # The hidden overlay toasts used to sit there full-size, and Kivy
        # CONSUMES any touch on a disabled widget — an invisible shield
        # that made the Multiplayer button (and bottom rows everywhere)
        # unclickable on devices. Direct widget calls can't catch that
        # class of bug; only a touch through the real window can.
        from kivy.tests.common import UnitTestTouch
        from kanjire.kivyui.widgets import ThemedButton as _TB

        def find_btn(w, needle):
            if isinstance(w, _TB) and needle in (w.text or ""):
                return w
            for c in w.children:
                r = find_btn(c, needle)
                if r is not None:
                    return r
            return None

        play_screen = app.sm.get_screen("play")
        mp_btn = find_btn(play_screen, "対戦")
        check("mp button found", mp_btn is not None)
        # Pump real frames until the button is actually laid out. Kivy sizes
        # widgets from a Clock trigger that cascades window -> manager ->
        # screen -> box, so on a loaded machine this script's 1.0s start can
        # beat it: the button is still at its default (0, 0, 100, 100), the
        # tap lands in the screen corner, and it fails looking exactly like
        # an overlay ate it. Calling do_layout() by hand is not enough (the
        # parents have no size yet) — only running frames resolves it.
        # That flake cost real debugging time; don't remove this.
        # Guarded so it is a no-op on a healthy run: pumping frames advances
        # the clock, and doing that unconditionally shifted the timeline
        # enough to break the deal-animation checkpoint further down.
        if mp_btn.width <= 100:
            from kivy.base import EventLoop
            for _pass in range(60):
                EventLoop.idle()
                if mp_btn.width > 100:
                    break
        check("play screen is laid out before the tap",
              mp_btn.width > 100 and mp_btn.center != [50.0, 50.0])
        wx, wy = mp_btn.to_window(*mp_btn.center)
        t = UnitTestTouch(wx, wy)
        t.touch_down()
        t.touch_up()
        check("window tap opens multiplayer (no ghost overlay eats it)",
              app.sm.current == "multiplayer")
        app.sm.get_screen("multiplayer").leave()
        check("left multiplayer", app.sm.current == "play")

        def later(delay, fn):
            Clock.schedule_once(lambda *_: fn(), delay)

        # 2. Start a default game. Launch is DEFERRED one tick behind the
        # loading spinner, so the assertions run in a delayed step.
        gs = None
        e = None
        app.go_game()
        check("loading spinner shows on launch", app.loading.opacity == 1)

        def tap(cid):
            gs._on_card(gs._cards[cid])

        def step_game_started():
            nonlocal gs, e
            check("game screen shown", app.sm.current == "game")
            gs = app.sm.get_screen("game")
            e = gs.engine
            check("engine playing",
                  e is not None and e.phase is Phase.PLAYING)
            check("board has cards", len(gs._cards) == len(e.board))
            check("loading spinner hidden after launch",
                  app.loading.opacity == 0)
            later(0.4, step_window_card)
            later(0.6, step_complete_group)

        # 2b. A window-level tap on the LOWEST card's lower half — the strip
        # the hidden sentence toast used to blanket — must select it.
        # Runs DELAYED: the ScreenManager blocks touches while its fade
        # transition is still in flight.
        def step_window_card():
            low = min(gs._cards.values(), key=lambda w: w.center_y)
            lx, ly = low.to_window(low.center_x,
                                   low.y + low.height * 0.25)
            t2 = UnitTestTouch(lx, ly)
            t2.touch_down()
            t2.touch_up()
            check("window tap selects a bottom card (toast is ghost)",
                  e.cards[low.card.id].selected)
            t3 = UnitTestTouch(lx, ly)   # toggle back off, state clean
            t3.touch_down()
            t3.touch_up()
            check("window tap deselects it again",
                  not e.cards[low.card.id].selected)

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
            # The full-dictionary browser: every word in every deck, played
            # or not — vastly more rows than the stats list.
            ss._set_view("dict")
            check("dictionary lists the whole vocab",
                  len(ss._rv.data) > 1000
                  and len(ss._rv.data) > len(ss._rows))
            check("dictionary rows carry meanings",
                  all(d["counts"] for d in ss._rv.data[:50]))
            check("dictionary rows are speakable",
                  all(d.get("say") for d in ss._rv.data[:50]))
            n_all = len(ss._rv.data)
            ss._search.text = ss._rv.data[0]["word"].split()[0]
            check("dictionary search filters",
                  0 < len(ss._rv.data) < n_all)
            ss._search.text = ""
            snap(app, "07b-dictionary")
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
            check("journey road recycled", len(js._rv.data)
                  == len(js.stations))
            # The freeze report: ~540 live buttons; recycled cells must keep
            # only a screenful of widgets alive however far the road goes.
            live = len(js._rv.layout_manager.children)
            check("journey keeps few live widgets", 0 < live < 80)
            js._rv.scroll_y = 0.0   # fling to the far end
            js._rv.scroll_y = 1.0
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
                                            recall_prompt="typed",
                                            recall_preview=False)
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
            # Advancing now waits for the FULL TTS playback + 0.5s.
            later(3.0, step_recall_second)

        def step_recall_second():
            rs = app.sm.get_screen("recall")
            if rs.word is not None:
                rs._input.text = rs.word.reading
                rs._submit()
            later(3.0, step_recall_done)

        def step_recall_done():
            rs = app.sm.get_screen("recall")
            check("recall results shown", rs._overlay is not None)
            check("recall all matched", rs.engine.matches == 2)
            snap(app, "13-recall-results")
            rs._quit()
            later(0.4, step_recall_v2)

        def step_recall_v2():
            # Study-first preview + multiple-choice prompt (friend feedback).
            from kanjire.game.config import PRESETS
            cfg = PRESETS["Recall"]().with_(words_per_round=2,
                                            recall_prompt="choice",
                                            recall_preview=True)
            app.go_game(cfg)
            later(0.6, step_recall_preview)

        def step_recall_preview():
            rs = app.sm.get_screen("recall")
            check("study-first preview shown", rs._overlay is not None)
            snap(app, "18-recall-preview")
            from kanjire.kivyui.widgets import ThemedButton as _TB
            start = [w for w in rs._overlay.walk()
                     if isinstance(w, _TB)][0]
            start.dispatch("on_release")
            later(0.4, step_recall_choice)

        def step_recall_choice():
            from kanjire.game.recall import is_correct_reading
            rs = app.sm.get_screen("recall")
            n = len(rs._choices.children)
            check("choice options offered", n >= 2)
            check("typing hidden in choice mode",
                  rs._input_row.opacity == 0)
            correct = [b for b in rs._choices.children
                       if is_correct_reading(b.text, rs.word.reading)]
            check("exactly one option is correct", len(correct) == 1)
            snap(app, "19-recall-choice")
            rs._choice_pick(correct[0].text, correct[0])
            check("choice pick scored", rs.engine.matches == 1)
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
            later(1.2, step_epilogue_win)

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
            # Back/Esc: in a game -> back to WHERE IT STARTED; on a tab ->
            # the previous screen; on the play tab -> confirm-exit modal
            # (unless opted out).
            app.switch_tab("play")
            app.go_game()
            later(1.2, step_back_in_game)

        def step_back_in_game():
            handled = app._on_hard_key(None, 27)
            check("back leaves the game", handled
                  and app.sm.current == "play")
            # A game started from another tab comes back to THAT tab — it
            # used to dump you on Play and lose your place on the road.
            app.switch_tab("journey")
            app.go_game()
            later(1.2, step_back_to_origin)

        def step_back_to_origin():
            handled = app._on_hard_key(None, 27)
            check("back from a journey game returns to journey",
                  handled and app.sm.current == "journey")
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
            later(1.2, step_grid_check)

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
            step_sync(gs)

        def step_sync(gs):
            # Device sync: pair the app with a headless second "device"
            # over an in-process broker, via the real Settings actions.
            import sqlite3

            from kanjire.data import db as _db
            from kanjire.net.syncsvc import SyncService
            from kanjire.net.transport import LoopbackBroker, LoopbackTransport
            gs._quit()
            broker = LoopbackBroker()
            app.sync.transport = LoopbackTransport(broker)

            class _PeerState:
                def __init__(self):
                    self._s = {}
                    self.data = {"high_scores": {"Zen": 7777},
                                 "settings": {}, "presets": []}

                def setting(self, k, d=""):
                    return self._s.get(k, d)

                def set_setting(self, k, v):
                    self._s[k] = v

                def save(self):
                    pass

            pcon = sqlite3.connect(":memory:")
            pcon.row_factory = sqlite3.Row
            pcon.executescript(_db.STATS_SCHEMA)
            peer = SyncService(_PeerState(), pcon,
                               transport=LoopbackTransport(broker))
            peer.connect()

            app.switch_tab("settings")
            later(0.5, lambda: step_sync_pair(peer))

        def step_sync_pair(peer):
            ss = app.sm.get_screen("settings")
            ss._sync_show_code()
            code = app.sync._pair_code
            check("pairing code shown", bool(code)
                  and code in ss._sync_code.text)
            check("peer joins with the code", peer.join(code) is None)
            for _ in range(8):
                app.sync.tick()
                peer.tick()
            check("devices linked", peer.linked
                  and peer.key == app.sync.key)
            check("peer progress merged in",
                  app.state.data.get("high_scores", {}).get("Zen") == 7777)
            snap(app, "17-sync-linked")
            # cleanup so reruns start unlinked
            app.sync.unlink()
            app.state.data.get("high_scores", {}).pop("Zen", None)
            app.state.save()
            step_sentence_modes_entry()

        def step_sentence_modes_entry():
            app.go_game()
            later(1.2, lambda: step_sentence_modes(
                app.sm.get_screen("game")))

        def step_sentence_modes(gs):
            from kanjire.kivyui.sentence_toast import BIG_SECONDS
            # 'big': centred card with large text, shown 50% longer.
            app.state.set_setting("sentence_display", "big")
            gs._sent.show("時間", "じかん")
            shown_big = gs._sent.opacity == 1
            centred = abs(gs._sent.center_y - gs.height * 0.5) < gs.height * 0.2
            check("big sentence toast shows centred",
                  shown_big and centred and BIG_SECONDS == 7.5)
            snap(app, "16-sentence-big")
            gs._sent.dismiss()
            # 'off': never shows.
            app.state.set_setting("sentence_display", "off")
            gs._sent.show("時間", "じかん")
            check("sentence toast honours off", gs._sent.opacity == 0)
            app.state.set_setting("sentence_display", "default")
            gs._quit()
            app.stop()

        # deferred launch: wait for the spinner tick, then the game steps
        later(1.2, step_game_started)

    # 2.0s, not 1.0: the Play tab builds a lot of rows now, and the
    # script must not start before the first layout pass lands.
    Clock.schedule_once(run_script, 2.0)
    app.run()

    print(f"\n[kivy_smoke] {len(CHECKS) - len(FAILED)}/{len(CHECKS)} checkpoints passed -> {OUT}")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
