"""Screenshot QA harness for the Kivy UI (the capture_all.py counterpart).

Boots the real app at several device geometries, walks the screens, and
writes PNGs for visual inspection. Exits non-zero on any exception, so it
doubles as a smoke test.

Usage:  python tests/kivy_capture.py [outdir]
Sizes:  phone portrait / phone landscape / Fold5 inner (square-ish) /
        Fold5 cover (tall) — one app process per size (Kivy can't rebuild
        its window cleanly in-process).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SIZES = {
    "phone-portrait": "396x880",
    "phone-landscape": "880x396",
    "fold-inner": "760x840",
    "fold-cover": "340x850",
}

#: (label, action) pairs the walker performs, screenshotting after each.
#: action is an app method name to call (None = already on that screen).
WALK = [("play", None), ("play-genres", "special"), ("journey", None),
        ("read-library", "special"), ("read-weave", "special"),
        ("journey-genres", "special"), ("journey-genre-levels", "special"),
        ("stats", None), ("settings", None),
        ("friends", None), ("game", "go_game"),
        ("recall-preview", "special"), ("recall-choice", "special")]


def _run_one(label: str, size: str, outdir: Path) -> None:
    env = dict(os.environ)
    env["KANJIRE_KIVY_SIZE"] = size
    # DPI-unaware: Windows display scaling shears Window.screenshot output.
    env.setdefault("SDL_WINDOWS_DPI_AWARENESS", "unaware")
    env.setdefault("KIVY_METRICS_DENSITY", "1")
    env["KANJIRE_KIVY_CAPTURE"] = str(outdir / label)
    env.setdefault("KANJIRE_USER_DIR", str(outdir / "_userstate"))
    code = (
        "import kanjire.kivyui.app as A\n"
        "from kivy.clock import Clock\n"
        "import os\n"
        "app = A.KanjiReApp()\n"
        "prefix = os.environ['KANJIRE_KIVY_CAPTURE']\n"
        "walk = %r\n"
        "TEXT = '私は大学の本を読みます。今日は天気がいいです。彼は毎日新聞を読む。'\n"
        "def special(label):\n"
        "    if label == 'read-library':\n"
        "        app.switch_tab('read')\n"
        "        rs = app.sm.get_screen('read')\n"
        "        rs._set_mode('library')\n"
        "        wv = rs._weave_view\n"
        "        if not wv.library.books():\n"
        "            wv.library.add('Chapter 1', TEXT)\n"
        "            wv.show_library()\n"
        "        return\n"
        "    if label == 'read-weave':\n"
        "        rs = app.sm.get_screen('read')\n"
        "        wv = rs._weave_view\n"
        "        books = wv.library.books()\n"
        "        if books:\n"
        "            wv.open_book(books[0]['id'])\n"
        "            toks = [w for w in wv.walk()\n"
        "                    if hasattr(w, 'token') and w.token.known_word]\n"
        "            for t in toks[:3]:\n"
        "                wv._weave.tapped(t.token, None)\n"
        "            wv._render()\n"
        "        return\n"
        "    if label == 'play-genres':\n"
        "        from kivy.uix.scrollview import ScrollView\n"
        "        for w in app.sm.get_screen('play').walk():\n"
        "            if isinstance(w, ScrollView):\n"
        "                w.scroll_y = 0.0\n"
        "                break\n"
        "        return\n"
        "    if label == 'journey-genres':\n"
        "        app.sm.get_screen('journey')._set_tab('genres')\n"
        "        return\n"
        "    if label == 'journey-genre-levels':\n"
        "        app.sm.get_screen('journey')._open_genre('food')\n"
        "        return\n"
        "    from kanjire.game.config import PRESETS\n"
        "    if label == 'recall-preview':\n"
        "        cfg = PRESETS['Recall']().with_(words_per_round=16,\n"
        "                                        recall_prompt='choice',\n"
        "                                        recall_preview=True)\n"
        "        app.go_game(cfg)\n"
        "    elif label == 'recall-choice':\n"
        "        rs = app.sm.get_screen('recall')\n"
        "        from kanjire.kivyui.widgets import ThemedButton as TB\n"
        "        if rs._overlay is not None:\n"
        "            btns = [w for w in rs._overlay.walk()\n"
        "                    if isinstance(w, TB)]\n"
        "            btns[0].dispatch('on_release')\n"
        "def step(i):\n"
        "    if i >= len(walk):\n"
        "        app.stop(); return\n"
        "    label, action = walk[i]\n"
        "    if action == 'special':\n"
        "        special(label)\n"
        "    elif action:\n"
        "        getattr(app, action)()\n"
        "    else:\n"
        "        app.switch_tab(label)\n"
        "    def snap(_dt):\n"
        "        app.screenshot(prefix + '-' + label + '.png')\n"
        "        step(i + 1)\n"
        "    Clock.schedule_once(snap, 1.2)\n"  # outlast the deal-in stagger
        "Clock.schedule_once(lambda dt: step(0), 0.6)\n"
        "app.run()\n" % (WALK,)
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=90,
                       cwd=str(Path(__file__).resolve().parent.parent))
    if r.returncode != 0:
        print(r.stdout[-2000:], file=sys.stderr)
        print(r.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"{label} ({size}) failed: rc={r.returncode}")
    shots = list(outdir.glob(f"{label}-*.png"))
    if not shots:
        raise SystemExit(f"{label}: no screenshots written")
    print(f"  {label:16s} {size:9s} -> {len(shots)} shot(s)")


def main() -> None:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(tempfile.gettempdir()) / "kanjire_kivy_shots"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[kivy_capture] -> {outdir}")
    for label, size in SIZES.items():
        _run_one(label, size, outdir)
    print("[kivy_capture] OK")


if __name__ == "__main__":
    main()
