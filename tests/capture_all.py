"""Render EVERY scene at several window sizes for overlap/clipping review.

The layout-QA harness: seeds realistic data (stats, SRS dues, history,
streak), then walks every screen of the app at each size in SIZES and saves
PNGs to ``tests/_shots/all/`` named ``<scene>_<WxH>.png``. Review the shots
after any layout-affecting change; nothing here asserts — eyes do the QA.

    python tests/capture_all.py            # all sizes
    python tests/capture_all.py 1920x1080  # one size
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Never let a test announce itself to the real world: the friends service would
# otherwise connect to the public relay just because a name is saved.
os.environ["KANJIRE_NO_NETWORK"] = "1"

import pyglet
from pyglet.window import mouse

from kanjire.data import db
from kanjire.game.config import PRESETS, GameConfig
from kanjire.ui.app import GameApp

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots", "all")
os.makedirs(OUT, exist_ok=True)

SIZES = [(760, 600), (1180, 960), (1920, 1080), (2560, 1440)]


def main() -> int:
    only = None
    if len(sys.argv) > 1:
        w, h = sys.argv[1].lower().split("x")
        only = (int(w), int(h))

    app = GameApp()
    from kanjire import i18n
    app.state.set_locale("en")
    i18n.set_locale("en")
    win = app.window

    def _draw():
        win.switch_to()
        win.clear()
        app.scene.draw()
        for ov in win.overlays:      # update banner / invite toast
            ov.draw()

    def render(n=8, dt=1 / 60.0):
        for _ in range(n):
            win.dispatch_events()
            app._tick(dt)
            _draw()
            win.flip()

    def shot(name, size):
        win.dispatch_events()
        _draw()
        win.flip()
        path = os.path.join(OUT, f"{name}_{size[0]}x{size[1]}.png")
        pyglet.image.get_buffer_manager().get_color_buffer().save(path)
        print("saved", os.path.basename(path))

    # ---- seed realistic data ---- #
    app.stats.reset_all()
    con = db.connect(read_only=True)
    try:
        pool = db.load_words(con, decks=["jlpt"], levels=[5],
                             require_kanji=True)
    finally:
        con.close()
    for w in pool[:30]:
        app.stats.saw(w)
        app.stats.matched(w)
    for w in pool[5:12]:
        app.stats.confused(w, pool[0], "reading")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    for w in pool[30:38]:
        app.stats.srs.update(w.expression, w.reading, 3, past)
    for i in range(6):
        app.stats.log_game(["Time Attack", "Zen", "Survival"][i % 3],
                           3200 + i * 700, 12 + i, i,
                           [(w.expression, w.reading) for w in pool[i:i + 9]])
    settings = app.state.data.setdefault("settings", {})
    streak_backup = {k: settings.get(k) for k in
                     ("streak_count", "streak_freezes", "streak_day")}
    settings["streak_count"] = 11
    settings["streak_freezes"] = 1
    settings["streak_day"] = (datetime.now().astimezone().date()
                              - timedelta(days=1)).isoformat()

    for size in SIZES:
        if only and size != only:
            continue
        win.set_size(*size)
        render(6)

        # menus
        app.go_menu()
        render(8)
        shot("menu_quick", size)
        app.scene._set_subtab("advanced")
        render(6)
        shot("menu_advanced", size)
        app.scene._set_mode("Learn")
        render(6)
        shot("menu_adv_learn", size)
        app.scene._set_mode("Survival")
        render(6)
        shot("menu_adv_survival", size)
        app.scene._set_mode("Time Attack")
        app.scene._set_subtab("quick")
        app.scene._set_deck("kana")
        render(6)
        shot("menu_kana", size)
        app.scene._set_deck("jlpt")

        # game boards
        app.go_game(PRESETS["Zen"]().with_(decks=("jlpt",), levels=(5,),
                                           words_per_round=6))
        render(70)
        shot("game_3face", size)
        app.go_game(PRESETS["Zen"]().with_(
            decks=("jlpt",), levels=(5,),
            faces=("kanji", "reading", "romaji", "meaning"),
            words_per_round=5))
        render(70)
        shot("game_4face", size)
        app.go_game(PRESETS["Zen"]().with_(
            decks=("jlpt",), levels=(5, 4), faces=("kanji", "meaning"),
            words_per_round=24))
        render(90)
        shot("game_24w", size)

        # results (finish a tiny game)
        app.go_game(PRESETS["Time Attack"]().with_(decks=("jlpt",),
                                                   levels=(5,),
                                                   words_per_round=4))
        g = app.scene
        for grp in (0, 1):
            for cid in list(g.engine.group_cards[grp]):
                cv = g.cards[cid]
                g.on_mouse_press(cv.cx, cv.cy, mouse.LEFT, 0)
        ca = next(c for c in g.cards.values()
                  if not c.model.matched and c.model.group == 2)
        cb = next(c for c in g.cards.values()
                  if not c.model.matched and c.model.group == 3)
        g.on_mouse_press(ca.cx, ca.cy, mouse.LEFT, 0)
        g.on_mouse_press(cb.cx, cb.cy, mouse.LEFT, 0)
        g.engine.time_left = 0.01
        render(90)
        shot("results", size)

        # stats tabs
        app.go_stats()
        render(8)
        shot("stats_overview", size)
        app.scene._set_tab("Words")
        render(6)
        shot("stats_words", size)
        app.scene._open_detail(app.scene._filtered["Words"][0])
        render(6)
        shot("stats_detail", size)
        app.scene._close_detail()
        app.scene._set_tab("History")
        render(6)
        shot("stats_history", size)

        # menu WITH the update banner: it's a bottom strip, and it used to sit
        # right on top of Multiplayer / Save-as-preset / the streak line.
        app.go_menu()
        render(6)
        from kanjire.update import controller as _uc
        from kanjire.update.checker import UpdateInfo
        _saved = (app.updater.status, app.updater.info)
        app.updater.status = _uc.READY
        app.updater.info = UpdateInfo(
            version="9.9.9", url="https://example.invalid/x.zip",
            sha256="0" * 64, size=1,
            notes="- Fixed the missing characters on Linux\n- Search box works")
        app.updater.staged = None
        render(8)
        shot("menu_update_banner", size)
        app.updater.status, app.updater.info = _saved
        app.go_menu()
        render(4)

        # multiplayer lobby, host AND guest view (a guest's settings buttons
        # are read-only but must still show what the host picked). No network:
        # the scene renders whatever snapshot it was last handed.
        from kanjire.net.server import DEFAULT_SETTINGS
        from kanjire.ui.scenes.multiplayer import MultiplayerScene
        snap = {
            "players": ["hina", "kenji", "you"],
            "scores": [0, 0, 0], "combos": [0, 0, 0],
            "connected": [True, True, True],
            "started": False, "finished": False, "paused": False,
            "settings": dict(DEFAULT_SETTINGS),
        }
        # Seed a couple of friends with live presence so the panel has content.
        from kanjire.net.friends import LOBBY, ONLINE
        app.state.add_friend("KEN00001", "kenji")
        app.state.add_friend("SARA0002", "sara")
        app.state.add_friend("YUKI0003", "yuki")
        import time as _time
        _now = _time.monotonic()
        app.friends.presence.update({
            "KEN00001": {"name": "kenji", "status": LOBBY, "room": "ABCDE",
                         "seen": _now},
            "SARA0002": {"name": "sara", "status": ONLINE, "room": "",
                         "seen": _now},
        })

        for who, me in (("host", 0), ("guest", 2)):
            mp = MultiplayerScene(app)
            app.set_scene(mp)
            mp.me = me
            mp.room = "KANJI"
            mp.client = None          # no polling; we drive the state by hand
            mp._on_state(snap, None)
            render(8)
            shot(f"mp_lobby_{who}", size)

        # Multiplayer connect screen, with the friends panel + an invite toast.
        mp = MultiplayerScene(app)
        app.set_scene(mp)
        render(8)
        app.invites.push({"type": "invite", "from": "KEN00001",
                          "name": "kenji", "room": "ABCDE"})
        render(6)
        shot("mp_connect_friends", size)
        app.invites._decline()
        render(2)

        # Multiplayer mid-reveal: a completed group held up for everyone.
        mp = MultiplayerScene(app)
        app.set_scene(mp)
        mp.me = 1                      # a GUEST watching someone else score
        mp.room = "KANJI"
        mp.client = None
        board = []
        cid = 0
        for g in range(4):
            for face, text in (("kanji", "食"), ("reading", "たべる"),
                               ("meaning", "to eat")):
                board.append({"id": cid, "group": g, "face": face,
                              "text": f"{text}{g}"})
                cid += 1
        held = [c["id"] for c in board if c["group"] == 0]
        for c in board:
            if c["id"] in held:
                c["matched"] = True
                c["selected"] = True
        playing = dict(snap, started=True, turn=0, turns_used=3,
                       turns_total=30, turns_left=27, board=board,
                       revealing=held, scores=[900, 400, 100], combos=[2, 0, 0])
        mp._on_state(playing, {"type": "complete", "player": 0, "points": 200,
                               "combo": 2, "cards": held,
                               "word": {"kanji": "食0"}})
        render(30)
        shot("mp_reveal", size)

        # Multiplayer results: the host gets Play-again next to Lobby.
        done = dict(snap, started=True, finished=True,
                    scores=[4200, 3100, 900], combos=[3, 1, 0],
                    turn=0, turns_used=30, turns_total=30, turns_left=0,
                    board=[])
        mp = MultiplayerScene(app)
        app.set_scene(mp)
        mp.me = 0
        mp.room = "KANJI"
        mp.client = None
        mp._on_state(done, None)
        render(8)
        shot("mp_results", size)

        # settings / reading / journey / recall
        app.go_settings()
        render(8)
        shot("settings", size)
        app.go_reading()
        render(8)
        shot("reading", size)
        app.go_journey()
        render(8)
        shot("journey", size)
        app.go_recall(pool[:4], None,
                      GameConfig(name="Today", session_mode=True,
                                 duration=None))
        for ch in "tabe":
            app.scene.on_text(ch)
        render(6)
        shot("recall", size)

    # ---- restore ---- #
    # The seeded friends went into the REAL save file (this harness runs against
    # the user's state) - take them back out.
    for code in ("KEN00001", "SARA0002", "YUKI0003"):
        app.state.remove_friend(code)
    for k, v in streak_backup.items():
        if v is None:
            settings.pop(k, None)
        else:
            settings[k] = v
    app.state.save()
    app.stats.reset_all()
    app.audio.shutdown()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
