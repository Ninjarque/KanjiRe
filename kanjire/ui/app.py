"""Application shell: the window, the frame clock, and scene switching."""
from __future__ import annotations

import os
import sys
import threading

import pyglet

from kanjire.data import db
from kanjire.data.stats import StatsRecorder
from kanjire import i18n
from kanjire.ui import theme
from kanjire.ui.audio import Audio
from kanjire.ui.scene import Scene
from kanjire.userstate import UserState


def _enable_dpi_awareness() -> None:
    """Opt into real pixels on Windows.

    pyglet 1.5 apps aren't DPI-aware, so at 125-175% display scaling Windows
    reports a *virtual* window size (a 1920x1080 fullscreen window can claim
    to be ~1165x653) and bitmap-stretches the result - blurry text AND
    layouts squeezed into a size the window doesn't really have. Declaring
    per-monitor DPI awareness makes width/height physical and text crisp;
    scale_for() then sizes the UI from true dimensions."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # Vista fallback
        except Exception:
            pass


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
        #: App-level overlays drawn on top of every scene (update banner,
        #: friend invites). They get first refusal on clicks.
        self.overlays: list = []

    def on_draw(self) -> None:
        self.clear()
        if self.current_scene:
            self.current_scene.draw()
        for ov in self.overlays:
            ov.draw()

    def on_mouse_press(self, x, y, button, modifiers) -> None:
        # Overlays sit on top, so they get first refusal: a click on the update
        # strip (or an invite) must not fall through to whatever is underneath.
        for ov in self.overlays:
            if ov.on_mouse_press(x, y, button, modifiers):
                return
        if self.current_scene:
            self.current_scene.on_mouse_press(x, y, button, modifiers)

    def on_mouse_release(self, x, y, button, modifiers) -> None:
        if self.current_scene:
            self.current_scene.on_mouse_release(x, y, button, modifiers)

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for ov in self.overlays:
            ov.on_mouse_motion(x, y, dx, dy)
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
        for ov in self.overlays:
            ov.layout()
        if self.current_scene:
            self.current_scene.on_resize(width, height)


class GameApp:
    def __init__(self, db_path=None) -> None:
        _enable_dpi_awareness()   # must run before the window is created
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
        self._update_selftest = bool(os.environ.get("KANJIRE_UPDATE_SELFTEST"))
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
        # The "update ready" strip belongs to the app, not to the menu: it used
        # to live in MenuScene, so a player sitting in Stats or the Reading Room
        # was never told an update was waiting. Drawn over every scene, and it
        # needs the window (and its GL context), so it comes after _Window(...).
        from kanjire.ui.widgets.update_banner import UpdateBanner

        self.banner = UpdateBanner(self)
        # Friends: presence + invites. Lives at app level because a friend must
        # be able to reach you wherever you are, not only on the multiplayer
        # screen. It connects in the background and stays inert (never touching
        # the network) for a player who has never used multiplayer.
        from kanjire.net.friends import FriendService
        from kanjire.ui.widgets.invite_toast import InviteToast

        self.friends = FriendService(self.state)
        self.invites = InviteToast(self)
        self.window.overlays = [self.banner, self.invites]
        self._maybe_go_online()
        self.scene: Scene | None = None
        self.go_menu()

    def _maybe_go_online(self) -> None:
        """Announce ourselves to friends, if we have any reason to.

        A player who has never touched multiplayer gets no network connection at
        all - going online is not something to do to someone silently.
        """
        if os.environ.get("KANJIRE_NO_NETWORK"):
            return
        if not (self.state.friends or self.state.setting("mp_name", "")):
            return
        threading.Thread(target=self.friends.connect, daemon=True,
                         name="kanjire-friends").start()

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

    def go_journey(self) -> None:
        from kanjire.ui.scenes.journey import JourneyScene

        self.set_scene(JourneyScene(self))

    def go_friends(self) -> None:
        from kanjire.ui.scenes.friends import FriendsScene

        self.set_scene(FriendsScene(self))

    def go_multiplayer(self, join_room: str = "") -> None:
        """Open multiplayer; with *join_room*, walk straight into that room
        (accepting a friend's invite must be one click, not "now type ABCDE")."""
        from kanjire.ui.scenes.multiplayer import MultiplayerScene

        self.set_scene(MultiplayerScene(self, join_room=join_room))

    def current_room_code(self) -> str:
        """The room we're hosting right now, if any (for answering a request)."""
        scene = self.scene
        code = getattr(scene, "room", "")
        if code and getattr(scene, "me", -1) == 0:
            return code
        return ""

    def go_reading(self) -> None:
        from kanjire.ui.scenes.reading import ReadingScene

        self.set_scene(ReadingScene(self))

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
        self.friends.tick()
        for msg in self.friends.poll():
            self.invites.push(msg)
        if self.banner.sync():
            self.on_banner_changed()
        if self._update_selftest:
            self._run_update_selftest()

    def on_banner_changed(self) -> None:
        """The banner appeared/vanished: it eats space at the bottom, so let the
        current scene re-lay out around it."""
        self.banner.layout()
        if self.scene:
            self.scene.on_resize(self.window.width, self.window.height)

    def _run_update_selftest(self) -> None:
        """KANJIRE_UPDATE_SELFTEST=1: click "Restart & update" for us.

        Exists so the *frozen* bundle can be driven through the whole
        check -> download -> swap -> relaunch path on a real machine. Reasoning
        about that path is how it shipped broken twice; this way it can actually
        be run.
        """
        import sys as _sys

        from kanjire.update import controller as _c

        u = self.updater
        if u.status in (_c.CHECKING, _c.DOWNLOADING):
            return
        print(f"[selftest] status={u.status} staged={u.staged} "
              f"can_apply={u.can_apply()} err={u.error}",
              file=_sys.stderr, flush=True)
        if u.status == _c.READY and u.can_apply():
            print("[selftest] applying update + exiting", file=_sys.stderr,
                  flush=True)
            if u.apply():
                pyglet.app.exit()
                return
        print("[selftest] nothing to apply; exiting", file=_sys.stderr, flush=True)
        pyglet.app.exit()

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
            # Clear our retained presence: friends must not keep seeing us
            # online after we quit.
            self.friends.close()
            self.audio.shutdown()
            self.stats.close()
            self.con.close()
