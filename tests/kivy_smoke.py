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
            # Live theme swap must rebuild every tab without error.
            from kanjire.ui import theme as _theme
            app.sm.get_screen("settings")._set_palette("Paper")
            later(0.4, lambda: step_theme(_theme))

        def step_theme(_theme):
            check("palette applied", _theme.current_palette() == "Paper")
            check("kept on settings tab", app.sm.current == "settings")
            snap(app, "08-settings-paper")
            app.sm.get_screen("settings")._set_palette("Charcoal")
            later(0.3, lambda: app.stop())

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
