"""Multiplayer smoke test for the Kivy UI — fully offline.

KANJIRE_MP_LOOPBACK wires the screen's RoomClient to an in-process broker;
a headless guest RoomClient joins the room the UI hosts. Exercises:
connect stage → host → lobby (roster shows both) → host edits a setting
(guest sees it) → start → play stage board → host taps a full group →
score registers → guest leaves → app leaves cleanly.

Usage: python tests/kivy_mp_smoke.py [outdir]
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(tempfile.gettempdir()) / "kanjire_kivy_mp"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("KANJIRE_KIVY_SIZE", "420x900")
os.environ["KANJIRE_USER_DIR"] = str(OUT / "_userstate")
os.environ["KANJIRE_MP_LOOPBACK"] = "1"
os.environ["KANJIRE_NO_NETWORK"] = "1"   # friends service stays offline

import kanjire.kivyui.app as A  # noqa: E402
from kivy.clock import Clock  # noqa: E402

from kanjire.net.room_client import RoomClient  # noqa: E402

CHECKS: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(("  ok  " if ok else " FAIL ") + name)
    if not ok:
        FAILED.append(name)


def main() -> None:
    app = A.KanjiReApp()
    guest = {"client": None}
    passes = {"words": set(), "slots": 0, "left": 0}

    def run_script(_dt):
        try:
            script(app)
        except Exception:
            traceback.print_exc()
            FAILED.append("script raised")
            app.stop()

    def later(delay, fn):
        def wrapped(*_):
            try:
                fn()
            except Exception:
                traceback.print_exc()
                FAILED.append("step raised")
                app.stop()
        Clock.schedule_once(wrapped, delay)

    def pump_guest():
        c = guest["client"]
        if c is not None:
            c.tick()
            return c.poll()
        return []

    def script(app):
        app.go_multiplayer()
        mp = app.sm.get_screen("multiplayer")
        check("connect stage", mp.stage == "connect")
        check("nav hidden in multiplayer", app.nav.height == 0)
        app.screenshot(str(OUT / "01-connect.png"))
        mp.in_name.text = "Hosty"
        mp._host()
        later(0.8, lambda: step_lobby(mp))

    def step_lobby(mp):
        check("lobby stage", mp.stage == "lobby")
        check("room code assigned", len(mp.room) == 5)
        check("host is player 0", mp.me == 0)
        app.screenshot(str(OUT / "02-lobby.png"))

        # A guest joins through the same loopback broker.
        from kanjire.kivyui.screens import multiplayer as mpmod
        g = RoomClient(transport=mpmod._make_transport())
        err = g.connect("Guesty", "GUESTCODE")
        check("guest connected", err is None)
        g.send({"t": "join", "room": mp.room})
        guest["client"] = g
        later(0.8, lambda: step_roster(mp))

    def step_roster(mp):
        pump_guest()
        players = (mp.state or {}).get("players") or []
        check("roster shows both", players == ["Hosty", "Guesty"])
        app.screenshot(str(OUT / "03-roster.png"))
        # Host edits a setting; the room state must carry it to everyone.
        mp._set_setting("board_size", 4)
        later(0.6, lambda: step_setting(mp))

    def step_setting(mp):
        pump_guest()
        check("setting broadcast", mp._settings().get("board_size") == 4)
        mp._start()
        later(1.0, lambda: step_play(mp))

    def step_play(mp):
        pump_guest()
        check("play stage", mp.stage == "play")
        if os.environ.get("KANJIRE_MP_SMOKE_DEBUG"):
            print("    window:", A.Window.size, "sm:", app.sm.pos,
                  app.sm.size, "screen:", mp.pos, mp.size,
                  "root_box:", mp.root_box.pos, mp.root_box.size,
                  "nav:", app.nav.pos, app.nav.size)
        st = mp.state or {}
        board = st.get("board") or []
        check("board on screen", len(mp.cards) == len(board) > 0)
        app.screenshot(str(OUT / "04-play.png"))
        turn = st.get("turn")
        check("someone's turn", isinstance(turn, int))
        if turn == mp.me:
            complete_group_via_ui(mp)
            later(1.0, lambda: step_scored(mp, mp.me))
        else:
            complete_group_via_guest(mp)
            later(1.0, lambda: step_scored(mp, turn))

    def complete_group_via_ui(mp):
        board = (mp.state or {}).get("board") or []
        target = next(d["group"] for d in board
                      if d and not d.get("matched"))
        for d in board:
            if d and d["group"] == target:
                mp.card_tapped(d["id"])

    def complete_group_via_guest(mp):
        g = guest["client"]
        board = (mp.state or {}).get("board") or []
        target = next(d["group"] for d in board
                      if d and not d.get("matched"))
        for d in board:
            if d and d["group"] == target:
                g.send({"t": "select", "card": d["id"]})

    def complete_any_group(mp):
        """Whoever's turn it is clears one group."""
        if (mp.state or {}).get("turn") == mp.me:
            complete_group_via_ui(mp)
        else:
            complete_group_via_guest(mp)

    def step_scored(mp, who):
        pump_guest()
        st = mp.state or {}
        scores = st.get("scores") or []
        check("group scored", len(scores) > who and scores[who] > 0)
        check("reveal holds the group", bool(st.get("revealing"))
              or all(not d.get("matched") for d in st.get("board") or []))
        app.screenshot(str(OUT / "05-scored.png"))
        # ---- passes: same words twice, blanks instead of refills -------- #
        mp._replay()                      # back to lobby, players stay
        later(0.8, lambda: step_passes_lobby(mp))

    def step_passes_lobby(mp):
        pump_guest()
        check("back in lobby for passes", mp.stage == "lobby")
        mp._set_setting("passes", 2)
        later(0.6, lambda: step_passes_start(mp))

    def step_passes_start(mp):
        pump_guest()
        check("passes setting broadcast",
              int(mp._settings().get("passes") or 1) == 2)
        mp._start()
        later(1.0, lambda: step_passes_play(mp))

    def step_passes_play(mp):
        pump_guest()
        st = mp.state or {}
        board = st.get("board") or []
        passes["words"] = {d["text"] for d in board
                           if d and d["face"] == "kanji"}
        passes["slots"] = len(board)
        check("passes game started",
              mp.stage == "play" and st.get("passes") == 2
              and len(board) > 0)
        complete_any_group(mp)
        later(3.0, lambda: step_passes_blanks(mp))   # past the 2s reveal

    def step_passes_blanks(mp):
        pump_guest()
        st = mp.state or {}
        board = st.get("board") or []
        faces_n = len(st.get("faces") or [])
        check("cleared group left blanks (no refill)",
              len(board) == passes["slots"]
              and board.count(None) == faces_n)
        check("client renders only live cards",
              len(mp.cards) == len(board) - board.count(None))
        check("pass HUD shown", "1/2" in (mp._hud_turn.text or ""))
        app.screenshot(str(OUT / "06-passes-blanks.png"))
        passes["left"] = len({d["group"] for d in board if d})
        step_passes_grind(mp)

    def step_passes_grind(mp):
        """Clear the remaining groups of pass 1, one reveal at a time."""
        if passes["left"] <= 0:
            later(0.5, lambda: step_passes_redeal(mp))
            return
        passes["left"] -= 1
        complete_any_group(mp)
        later(3.0, lambda: (pump_guest(), step_passes_grind(mp)))

    def step_passes_redeal(mp):
        pump_guest()
        st = mp.state or {}
        board = st.get("board") or []
        words = {d["text"] for d in board if d and d["face"] == "kanji"}
        check("pass 2 re-deals the SAME words",
              st.get("pass_no") == 2 and board.count(None) == 0
              and words == passes["words"] and not st.get("finished"))
        check("pass HUD advanced", "2/2" in (mp._hud_turn.text or ""))
        app.screenshot(str(OUT / "07-passes-redeal.png"))
        # Guest walks away; host survives.
        guest["client"].close()
        guest["client"] = None
        later(0.5, lambda: step_leave(mp))

    def step_leave(mp):
        mp.leave()
        check("back on play tab", app.sm.current == "play")
        check("nav restored", app.nav.height > 0)
        app.stop()

    Clock.schedule_once(run_script, 1.0)
    app.run()

    print(f"\n[kivy_mp_smoke] {len(CHECKS) - len(FAILED)}/{len(CHECKS)} "
          f"checkpoints passed -> {OUT}")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
