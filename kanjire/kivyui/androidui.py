"""Android system-UI helpers (no-ops everywhere else).

Immersive fullscreen is applied at RUNTIME through the platform API so the
player decides — buildozer's fullscreen flag stays 0 (they like their
system bars, and baked-in fullscreen made the old touch offset worse).
"""
from __future__ import annotations

import os

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ


def set_immersive(on: bool) -> bool:
    """Hide (or restore) the system bars. Returns True if applied."""
    if not IS_ANDROID:
        return False
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        View = autoclass("android.view.View")
        activity = PythonActivity.mActivity

        @run_on_ui_thread
        def _apply():
            decor = activity.getWindow().getDecorView()
            if on:
                decor.setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY)
            else:
                decor.setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE)

        _apply()
        return True
    except Exception:
        return False
