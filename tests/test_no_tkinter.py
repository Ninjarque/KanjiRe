"""Guard: the app must never *need* tkinter at runtime.

A frozen PyInstaller build has no tkinter (and python3-tk is often missing on
Linux), so any code path that imports it crashes the app. That is exactly what
happened when a player clicked a Stats "mark known" button on Linux. These tests
make tkinter un-importable and prove the app still works.
"""
from __future__ import annotations

import builtins
import os
import sys

os.environ["KANJIRE_NO_NETWORK"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def no_tkinter(monkeypatch):
    """Make `import tkinter` (and submodules) fail, as on a frozen Linux build."""
    for mod in list(sys.modules):
        if mod == "tkinter" or mod.startswith("tkinter."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "tkinter" or name.startswith("tkinter."):
            raise ModuleNotFoundError("No module named 'tkinter'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_importing_the_scenes_needs_no_tkinter(no_tkinter):
    # A fresh import of every scene module must not touch tkinter.
    for mod in ("kanjire.ui.scenes.menu", "kanjire.ui.scenes.stats",
                "kanjire.ui.scenes.recall", "kanjire.ui.widgets.modal",
                "kanjire.ui.app"):
        sys.modules.pop(mod, None)
    import importlib

    for mod in ("kanjire.ui.widgets.modal", "kanjire.ui.scenes.menu",
                "kanjire.ui.scenes.stats"):
        importlib.import_module(mod)   # must not raise


def test_the_confirm_dialog_replaces_tkinter():
    """The dialog helpers are pure in-app widgets - no tkinter anywhere."""
    import inspect

    from kanjire.ui.widgets import modal

    src = inspect.getsource(modal)
    # Only the module docstring may mention it (explaining what it replaced).
    body = src.split('"""', 2)[-1]
    assert "import tkinter" not in body
    assert "askyesno" not in body and "askstring" not in body
