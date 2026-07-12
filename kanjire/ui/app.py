"""Application shell: the window, the frame clock, and scene switching."""
from __future__ import annotations

import pyglet

from kanjire.data import db
from kanjire.data.stats import StatsRecorder
from kanjire import i18n
from kanjire.ui import theme
from kanjire.ui.audio import Audio
from kanjire.ui.scene import Scene
from kanjire.userstate import UserState


def _set_clear_color(color: tuple[int, int, int]) -> None:
    """Push the active palette's BG into pyglet's glClearColor so
    ``window.clear()`` paints the whole window in one flat colour
    (no more banded gradients)."""
    from pyglet.gl import glClearColor
    r, g, b = color
    glClearColor(r / 255.0, g / 255.0, b / 255.0, 1.0)


def _initial_window_size(want_w: int, want_h: int) -> tuple[int, int]:
    """Cap the requested window size to what actually fits on screen, leaving
    room for the OS title bar / taskbar. Critical on small displays (e.g.
    1600x900) where the historical 1020px-tall default didn't fit at all."""
    try:
        screen = pyglet.canvas.get_display().get_default_screen()
        sw, sh = screen.width, screen.height
    except Exception:
        return want_w, want_h
    w = min(want_w, max(760, sw - 80))
    h = min(want_h, max(600, sh - 120))
    return w, h


class _Window(pyglet.window.Window):
    """A window that forwards every event to the app's current scene."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.current_scene: Scene | None = None

    def on_draw(self) -> None:
        self.clear()
        if self.current_scene:
            self.current_scene.draw()

    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.current_scene:
            self.current_scene.on_mouse_press(x, y, button, modifiers)

    def on_mouse_release(self, x, y, button, modifiers) -> None:
        if self.current_scene:
            self.current_scene.on_mouse_release(x, y, button, modifiers)

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        if self.current_scene:
            self.current_scene.on_mouse_motion(x, y, dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        if self.current_scene:
            self.current_scene.on_mouse_scroll(x, y, scroll_x, scroll_y)

    def on_text(self, text) -> None:
        if self.current_scene:
            self.current_scene.on_text(text)

    def on_text_motion(self, motion) -> None:
        if self.current_scene:
            self.current_scene.on_text_motion(motion)

    def on_text_motion_select(self, motion) -> None:
        if self.current_scene:
            self.current_scene.on_text_motion_select(motion)

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        # Global fullscreen toggle: handled before the scene sees it.
        if symbol == key.F11:
            self.set_fullscreen(not self.fullscreen)
            return
        if self.current_scene:
            self.current_scene.on_key_press(symbol, modifiers)

    def on_resize(self, width, height) -> None:
        super().on_resize(width, height)
        if self.current_scene:
            self.current_scene.on_resize(width, height)


class GameApp:
    def __init__(self, db_path=None) -> None:
        self.con = (
            db.connect(db_path, read_only=True)
            if db_path
            else db.connect(read_only=True)
        )
        # Stats live in a separate per-user SQLite so a vocab DB rebuild (or a
        # release update) never wipes the player's progress.
        from kanjire.paths import STATS_DB_PATH
        self._stats_con = db.connect(STATS_DB_PATH)
        self.stats = StatsRecorder(self._stats_con)
        self.state = UserState()
        # Apply persisted locale to the i18n module before any scene builds.
        i18n.set_locale(self.state.locale)
        # Apply the persisted palette (pure Python — rebinds theme constants)
        # before the window exists; the glClearColor call must wait until the
        # window has created its GL context (see below).
        theme.apply_palette(self.state.palette)
        self.audio = Audio(muted=self.state.muted)
        # Background self-updater (no-op in dev runs / when no signing key is
        # baked in). Scenes poll ``self.updater`` to show the update banner.
        from kanjire.update.controller import UpdateController
        self.updater = UpdateController(self.state)
        win_w, win_h = _initial_window_size(1180, 1020)
        self.window = _Window(
            width=win_w,
            height=win_h,
            caption="KanjiRe 漢字",
            resizable=True,
        )
        # Low min so the window stays usable on small laptops; the scale factor
        # (kanjire.ui.metrics) keeps content readable and overlap-free down here.
        self.window.minimum_size = (760, 600)
        # Flat background via glClearColor (no more gradient banding). Needs the
        # window's GL context, so it runs after _Window(...).
        _set_clear_color(theme.BG)
        self.scene: Scene | None = None
        self.go_menu()

    # -- scene management ----------------------------------------------- #
    def set_scene(self, scene: Scene) -> None:
        if self.scene:
            self.scene.on_exit()
        self.scene = scene
        self.window.current_scene = scene
        scene.on_enter()
        scene.on_resize(self.window.width, self.window.height)

    def go_menu(self) -> None:
        from kanjire.ui.scenes.menu import MenuScene

        self.set_scene(MenuScene(self))

    def go_game(self, config, pool=None, recall_words=None) -> None:
        from kanjire.ui.scenes.game import GameScene

        self.set_scene(GameScene(self, config, pool=pool,
                                 recall_words=recall_words))

    def go_recall(self, words, engine, config, session=None) -> None:
        from kanjire.ui.scenes.recall import RecallScene

        self.set_scene(RecallScene(self, words, engine, config,
                                   session=session))

    def go_results(self, engine, config, session=None) -> None:
        from kanjire.ui.scenes.results import ResultsScene

        self.set_scene(ResultsScene(self, engine, config, session=session))

    def go_import(self, path, display_name: str) -> None:
        from kanjire.ui.scenes.import_text import ImportTextScene

        self.set_scene(ImportTextScene(
            self, path=path, display_name=display_name,
        ))

    def go_import_pasted(self, text: str, display_name: str) -> None:
        from kanjire.ui.scenes.import_text import ImportTextScene

        self.set_scene(ImportTextScene(
            self, raw_text=text, display_name=display_name,
        ))

    def go_stats(self) -> None:
        from kanjire.ui.scenes.stats import StatsScene

        self.set_scene(StatsScene(self))

    def go_settings(self) -> None:
        from kanjire.ui.scenes.settings import SettingsScene

        self.set_scene(SettingsScene(self))

    # -- main loop ------------------------------------------------------- #
    def _tick(self, dt: float) -> None:
        if self.scene:
            self.scene.update(dt)

    def toggle_mute(self) -> bool:
        muted = self.audio.toggle_mute()
        self.state.set_muted(muted)
        return muted

    def apply_palette(self, name: str) -> None:
        """Switch theme palette: persist it, rebind the live colour constants,
        repaint the window background, and rebuild the current scene so
        already-constructed widgets pick up the new colours."""
        self.state.set_palette(name)
        theme.apply_palette(name)
        _set_clear_color(theme.BG)
        # Palette switching happens from Settings, so rebuild there. (Mirrors
        # the language-switch rebuild pattern.)
        self.go_settings()

    @property
    def can_ingest(self) -> bool:
        """True when corpus-import (fugashi + jamdict + dict DB) actually works.

        Frozen / play-only release builds intentionally don't ship the NLP
        stack, so the import buttons hide themselves cleanly."""
        cached = getattr(self, "_can_ingest", None)
        if cached is not None:
            return cached
        ok = False
        try:
            from pathlib import Path
            if (Path.home() / ".jamdict" / "data" / "jamdict.db").exists():
                import fugashi  # noqa: F401
                import jamdict  # noqa: F401
                ok = True
        except Exception:
            ok = False
        self._can_ingest = ok
        return ok

    def run(self) -> None:
        pyglet.clock.schedule_interval(self._tick, 1 / 60.0)
        # Fire a one-shot check shortly after launch so the window paints first.
        self.updater.maybe_start()
        try:
            pyglet.app.run()
        finally:
            self.audio.shutdown()
            self.stats.close()
            self.con.close()
