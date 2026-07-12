"""Regression guard for the v0.11.0 Linux startup crash.

pyglet registers media codecs when ``pyglet.media`` is imported and guards
each one with ``except ImportError`` only. Its Linux GStreamer codec does
``import gi`` and then initialises Gst - and when the system GStreamer
resolves GLib symbols against a different libglib (an older one bundled
beside a frozen app), that init raises ``GLib.GError``, which is NOT an
ImportError, escapes, and kills the process before the window ever opens
("undefined symbol: g_sort_array").

kanjire.ui.audio therefore blocks ``gi`` before importing pyglet.media, so
the codec is skipped the clean way. These tests fail loudly if that guard is
ever removed - it is invisible on Windows and only bites on other people's
Linux distros, which is the worst possible place to find out.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kanjire.ui.audio as audio  # noqa: F401  - importing installs the guard


def test_gi_is_blocked_as_a_clean_importerror():
    try:
        import gi  # noqa: F401
    except ImportError:
        return
    raise AssertionError(
        "gi is importable: pyglet's gstreamer codec can now run its Gst init "
        "and crash the app on distros with a newer system GLib"
    )


def test_gstreamer_codec_never_loads():
    import pyglet.media  # noqa: F401  - triggers codec registration
    loaded = [m for m in sys.modules if "gstreamer" in m]
    assert not loaded, f"gstreamer codec was registered: {loaded}"


def test_synthesized_sfx_still_work_without_any_codec():
    a = audio.Audio(muted=True)
    try:
        assert a.sfx._sources, "no SFX were synthesized"
        for name in ("select", "match", "mismatch", "heart", "coin", "damage"):
            assert name in a.sfx._sources, f"missing sfx: {name}"
        a.sfx.play("match")          # must not raise even when muted
    finally:
        a.shutdown()


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
