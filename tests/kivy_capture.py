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
WALK = [("play", None), ("stats", None), ("settings", None),
        ("friends", None), ("game", "go_game")]


def _run_one(label: str, size: str, outdir: Path) -> None:
    env = dict(os.environ)
    env["KANJIRE_KIVY_SIZE"] = size
    env["KANJIRE_KIVY_CAPTURE"] = str(outdir / label)
    env.setdefault("KANJIRE_USER_DIR", str(outdir / "_userstate"))
    code = (
        "import kanjire.kivyui.app as A\n"
        "from kivy.clock import Clock\n"
        "import os\n"
        "app = A.KanjiReApp()\n"
        "prefix = os.environ['KANJIRE_KIVY_CAPTURE']\n"
        "walk = %r\n"
        "def step(i):\n"
        "    if i >= len(walk):\n"
        "        app.stop(); return\n"
        "    label, action = walk[i]\n"
        "    if action:\n"
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
