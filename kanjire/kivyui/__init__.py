"""Kivy front-end for KanjiRe.

This is the cross-platform UI (Android + desktop). It reuses everything below
the UI layer unchanged — ``kanjire.game`` (engine), ``kanjire.model``,
``kanjire.data`` (SQLite), ``kanjire.net`` (multiplayer/friends relay),
``kanjire.i18n``, ``kanjire.userstate`` — and shares the colour palettes in
``kanjire.ui.theme`` (that module is toolkit-agnostic; only the rest of
``kanjire/ui`` is pyglet-bound).

Run on desktop with ``python -m kanjire.kivyui``. Android builds are produced
by buildozer from ``android/buildozer.spec``.
"""
