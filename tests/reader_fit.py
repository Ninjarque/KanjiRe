"""Does the reader's page actually FIT? Numbers, at every geometry.

A page is *measured* before it is drawn, so any mismatch between what the
paginator assumes and what the renderer really does shows up as text over the
edge — and the only way to know is to render and compare boxes.

For each device geometry x text size x orientation this opens a real chapter,
lays it out, and reports the worst overflow of any word past the page box.
Exits non-zero if anything is out of bounds, so it is a gate, not a report.

Usage:  python tests/reader_fit.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SIZES = {
    "phone-portrait": "396x880",
    "phone-landscape": "880x396",
    "fold-inner": "760x840",
    "fold-cover": "340x850",
    "tablet": "1200x800",
}

#: Real prose: long sentences, short sentences, and enough of both to page.
TEXT = (
    "私は大学の本を読みます。今日は天気がいいですから、公園で新聞を読むつもりです。"
    "彼は毎日会社に行って、夜遅くまで働いています。"
    "子供が公園で遊んでいる。"
    "先生は学生に難しい質問をしましたが、誰も答えられませんでした。"
    "水を飲む。"
    "来年の春、家族と一緒に日本へ旅行に行きたいと思っています。"
) * 6


def _script() -> str:
    return r'''
import os
from kivy.base import EventLoop
from kivy.clock import Clock
from kivy.metrics import dp
import kanjire.kivyui.app as A

TEXT = %r
app = A.KanjiReApp()
FAIL = []


def pump(n=8):
    for _ in range(n):
        EventLoop.idle()


def report(tag, wv):
    """Worst overflow of any word past the page box, in pixels."""
    from kanjire.kivyui.screens.weaveread import WeaveWord
    body = getattr(wv, "_body", None)
    words = [w for w in wv.walk() if isinstance(w, WeaveWord)]
    if body is None or not words:
        print("%%-34s NO BODY/WORDS" %% tag)
        FAIL.append(tag + ": nothing rendered")
        return
    bx, by = body.to_window(body.x, body.y)
    bw, bh = body.width, body.height
    over_r = over_l = over_b = over_t = 0.0
    for w in words:
        wx, wy = w.to_window(w.x, w.y)
        over_r = max(over_r, (wx + w.width) - (bx + bw))
        over_l = max(over_l, bx - wx)
        over_b = max(over_b, by - wy)
        over_t = max(over_t, (wy + w.height) - (by + bh))
    worst = max(over_r, over_l, over_b, over_t)
    print("%%-34s box=%%4dx%%-4d words=%%3d  R%%+5.0f L%%+5.0f B%%+5.0f T%%+5.0f  %%s"
          %% (tag, bw, bh, len(words), over_r, over_l, over_b, over_t,
             "OK" if worst <= 1.0 else "OVERFLOW"))
    if worst > 1.0:
        FAIL.append("%%s: %%.0fpx" %% (tag, worst))


def go(_dt):
    from kanjire.data.weave import FONT_SIZES
    rs = app.sm.get_screen("read")
    rs._set_mode("library")
    pump()
    wv = rs._weave_view
    for b in wv.library.books():
        wv.library.delete(b["id"])
    bid = wv.library.add("Fit", TEXT)
    wv.open_book(bid)
    pump()
    for orient in ("horizontal", "vertical"):
        for size in FONT_SIZES:
            wv._app.state.set_setting("read_orientation", orient)
            wv._set_display("read_font_size", str(size))
            pump(6)
            report("%%s %%s/%%d" %% (os.environ["FIT_TAG"], orient[:4], size), wv)
    # The options panel steals height: the page must shrink, not overflow.
    wv._app.state.set_setting("read_orientation", "horizontal")
    wv._set_display("read_font_size", "19")
    wv._show_opts = True
    wv._render()
    pump(6)
    report("%%s opts-open" %% os.environ["FIT_TAG"], wv)
    wv._show_opts = False
    wv.library.delete(bid)
    app.stop()


Clock.schedule_once(go, 1.8)
app.run()
if FAIL:
    print("FAIL " + "; ".join(FAIL))
    raise SystemExit(1)
''' % (TEXT,)


DESKTOP_SIZES = [(760, 600), (1180, 960), (1920, 1080), (1280, 500)]


def _desktop() -> list[str]:
    """The same check for the pyglet reader, which draws at absolute coords.

    Parity is the point: a chapter that fits on the phone must fit here too,
    and the desktop reader measures with a different text engine.
    """
    import pyglet

    from kanjire.data.weave import FONT_SIZES
    from kanjire.ui.app import GameApp

    app = GameApp()
    win = app.window
    bad: list[str] = []
    app.go_weave()
    wv = app.scene
    for existing in wv.library.books():
        wv.library.delete(existing["id"])
    bid = wv.library.add("Fit", TEXT)
    wv.open_book(bid)

    def frames(n=3, dt=1 / 60.0):
        for _ in range(n):
            win.dispatch_events()
            app._tick(dt)
            win.switch_to()
            win.clear()
            app.scene.draw()
            win.flip()

    for w, h in DESKTOP_SIZES:
        win.set_size(w, h)
        frames(2)
        for orient in ("horizontal", "vertical"):
            for size in FONT_SIZES:
                app.state.set_setting("read_orientation", orient)
                wv._set_display("read_font_size", str(size))
                frames(2)
                s = wv._s
                aw, ah = wv._page_area(s)
                x0, right = win.width / 2 - aw / 2, win.width / 2 + aw / 2
                top, bottom = win.height - 226 * s, win.height - 226 * s - ah
                worst = 0.0
                for _t, _l, tx, ty, tw, th in wv._tokens:
                    worst = max(worst, x0 - tx, (tx + tw) - right,
                                bottom - ty, (ty + th) - top)
                tag = f"desktop {w}x{h} {orient[:4]}/{size}"
                print(f"{tag:34s} box={aw:4.0f}x{ah:<4.0f} "
                      f"words={len(wv._tokens):3d}  worst{worst:+6.0f}  "
                      f"{'OK' if worst <= 1.0 else 'OVERFLOW'}")
                if worst > 1.0:
                    bad.append(f"{tag}: {worst:.0f}px")
    app.state.set_setting("read_orientation", "horizontal")
    wv._set_display("read_font_size", "19")
    wv.library.delete(bid)
    win.close()
    return bad


def main() -> None:
    out = Path(tempfile.gettempdir()) / "kanjire_reader_fit"
    out.mkdir(parents=True, exist_ok=True)
    bad = []
    if "--desktop" in sys.argv:
        bad = _desktop()
        if bad:
            raise SystemExit("[reader_fit] desktop overflows: "
                             + "; ".join(bad))
        print("[reader_fit] desktop OK - every page fits its box")
        return
    for tag, size in SIZES.items():
        env = dict(os.environ)
        env["KANJIRE_KIVY_SIZE"] = size
        env["FIT_TAG"] = f"{tag:15s}"
        env.setdefault("KANJIRE_USER_DIR", str(out / "_userstate"))
        env.setdefault("SDL_WINDOWS_DPI_AWARENESS", "unaware")
        env.setdefault("KIVY_METRICS_DENSITY", "1")
        env["KIVY_NO_ARGS"] = "1"
        env["KIVY_LOG_LEVEL"] = "error"
        env.setdefault("KANJIRE_NO_NETWORK", "1")
        r = subprocess.run([sys.executable, "-c", _script()], env=env,
                           capture_output=True, text=True, timeout=180,
                           cwd=str(Path(__file__).resolve().parent.parent))
        for line in r.stdout.splitlines():
            if "OK" in line or "OVERFLOW" in line or line.startswith("FAIL"):
                print(line)
        if r.returncode != 0:
            bad.append(f"{tag} ({size})")
            print(r.stdout[-1200:], file=sys.stderr)
            print(r.stderr[-2500:], file=sys.stderr)
    if bad:
        raise SystemExit(f"[reader_fit] page overflows at: {', '.join(bad)}")
    print("[reader_fit] OK - every page fits its box")


if __name__ == "__main__":
    main()
