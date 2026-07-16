#!/usr/bin/env python
"""Convenience launcher (``python main.py``) and the Android entry point.

python-for-android hardcodes ``main.py`` as the app entry, so on Android this
starts the Kivy UI. On desktop it keeps launching the pyglet app as before
(run ``python -m kanjire.kivyui`` to try the Kivy UI on desktop).
"""
import os

if "ANDROID_ARGUMENT" in os.environ:          # running inside a p4a bundle
    from kanjire.kivyui.app import run
    run()
elif __name__ == "__main__":
    from kanjire.__main__ import main
    raise SystemExit(main())
