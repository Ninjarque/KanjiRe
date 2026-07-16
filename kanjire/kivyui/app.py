"""KanjiRe Kivy application shell.

Owns the long-lived objects (DB connection, user state, screen manager) the
same way ``kanjire.ui.app.App`` does on the pyglet side, and exposes the same
navigation verbs (``go_home``, ``go_game``, …) so screens stay decoupled.

Desktop dev runs open a phone-portrait window by default; set
``KANJIRE_KIVY_SIZE=WxH`` to test other geometries (Fold inner screen etc.).
On Android, Kivy is fullscreen and window size follows the device/fold state.
"""
from __future__ import annotations

import os

os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.config import Config  # noqa: E402  (must precede Window import)

Config.set("input", "mouse", "mouse,disable_multitouch")
Config.set("kivy", "exit_on_escape", "0")

import threading  # noqa: E402

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.uix.screenmanager import FadeTransition, ScreenManager  # noqa: E402

from kanjire import crashlog, i18n  # noqa: E402
from kanjire.data import db  # noqa: E402
from kanjire.data.stats import StatsRecorder  # noqa: E402
from kanjire.game.config import PRESETS  # noqa: E402
from kanjire.kivyui import fonts  # noqa: E402
from kanjire.kivyui.theming import rgba, theme  # noqa: E402
from kanjire.paths import STATS_DB_PATH  # noqa: E402
from kanjire.userstate import UserState  # noqa: E402

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ


def _dev_window_size() -> tuple[int, int]:
    spec = os.environ.get("KANJIRE_KIVY_SIZE", "420x900")
    try:
        w, h = spec.lower().split("x")
        return max(200, int(w)), max(200, int(h))
    except Exception:
        return 420, 900


class KanjiReApp(App):
    title = "KanjiRe"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.state = UserState()
        i18n.set_locale(self.state.locale)
        theme.apply_palette(self.state.setting("palette", theme.DEFAULT_PALETTE))
        self.con = db.connect(read_only=True)
        # Stats live in a separate per-user SQLite so a vocab DB rebuild (or a
        # release update) never wipes the player's progress.
        self._stats_con = db.connect(STATS_DB_PATH)
        self.stats = StatsRecorder(self._stats_con)
        self.sm: ScreenManager | None = None

    # ------------------------------------------------------------------ #
    # Kivy lifecycle
    # ------------------------------------------------------------------ #
    def build(self):
        fonts.register()
        from kanjire.kivyui.audio import Audio
        self.audio = Audio(muted=self.state.muted)
        if not IS_ANDROID:
            Window.size = _dev_window_size()
        Window.clearcolor = rgba(theme.BG)

        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.floatlayout import FloatLayout

        from kanjire.i18n import tr
        from kanjire.kivyui.toast import InviteToast
        from kanjire.kivyui.widgets import NavBar
        from kanjire.net.friends import FriendService

        # Friends: presence + invites, app-level so a friend can reach you on
        # any tab. Stays fully offline for players who never used multiplayer.
        self.friends = FriendService(self.state)
        self.maybe_go_online()

        self.root_box = BoxLayout(orientation="vertical")
        self.sm = ScreenManager(transition=FadeTransition(duration=0.12))
        # Collapsing the nav moves/resizes the manager, but ScreenManager only
        # positions screens when they're added or switched to — screens added
        # earlier keep a stale pos and render 56dp too high. Keep them synced.
        self.sm.bind(pos=self._sync_screens, size=self._sync_screens)
        self._build_tabs()
        self.nav = NavBar(self.switch_tab, tr)
        self.root_box.add_widget(self.sm)
        self.root_box.add_widget(self.nav)
        self.nav.set_active("play")

        root = FloatLayout()
        root.add_widget(self.root_box)
        self.toast = InviteToast(self)
        root.add_widget(self.toast)
        Window.bind(size=lambda *_: self._place_toast())
        self._place_toast()

        Clock.schedule_interval(self._net_tick, 0.5)
        return root

    def _sync_screens(self, *_) -> None:
        for s in self.sm.screens:
            s.pos = self.sm.pos
            s.size = self.sm.size

    def _place_toast(self) -> None:
        from kivy.metrics import dp
        w = min(Window.width - dp(20), dp(440))
        self.toast.width = w
        self.toast.x = (Window.width - w) / 2
        self.toast.y = dp(66)

    def _net_tick(self, _dt) -> None:
        self.friends.tick()
        for msg in self.friends.poll():
            self.toast.push(msg)

    def maybe_go_online(self) -> None:
        """Announce ourselves to friends, if we have any reason to.

        A player who has never touched multiplayer gets no network connection
        at all — going online is not something to do to someone silently.
        """
        if os.environ.get("KANJIRE_NO_NETWORK"):
            return
        if not (self.state.friends or self.state.setting("mp_name", "")):
            return
        if getattr(self.friends, "connected", False):
            return
        threading.Thread(target=self.friends.connect, daemon=True,
                         name="kanjire-friends").start()

    def _build_tabs(self) -> None:
        """(Re)create every tab screen. Call after theme/locale changes."""
        from kanjire.kivyui.screens.play import PlayScreen
        from kanjire.kivyui.screens.settings import SettingsScreen
        from kanjire.kivyui.screens.stats import StatsScreen

        for name in [s.name for s in list(self.sm.screens)]:
            self.sm.remove_widget(self.sm.get_screen(name))
        from kanjire.kivyui.screens.friends import FriendsScreen
        from kanjire.kivyui.screens.journey import JourneyScreen
        from kanjire.kivyui.screens.reading import ReadingScreen

        self.sm.add_widget(PlayScreen(self, name="play"))
        self.sm.add_widget(JourneyScreen(self, name="journey"))
        self.sm.add_widget(ReadingScreen(self, name="read"))
        self.sm.add_widget(FriendsScreen(self, name="friends"))
        self.sm.add_widget(StatsScreen(self, name="stats"))
        self.sm.add_widget(SettingsScreen(self, name="settings"))

    def rebuild_ui(self, keep: str = "play") -> None:
        """Rebuild all chrome after a palette/locale change."""
        from kanjire.i18n import tr
        from kanjire.kivyui.widgets import NavBar

        Window.clearcolor = rgba(theme.BG)
        self._build_tabs()
        self.root_box.remove_widget(self.nav)
        self.nav = NavBar(self.switch_tab, tr)
        self.root_box.add_widget(self.nav)
        self.switch_tab(keep if self.sm.has_screen(keep) else "play")

    def on_stop(self):
        try:
            # Clear our retained presence: friends must not keep seeing us
            # online after the app is gone.
            self.friends.close()
        except Exception:
            pass
        try:
            self.audio.shutdown()
            self.state.save()
            self.con.close()
            self._stats_con.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Navigation — same verbs as the pyglet app
    # ------------------------------------------------------------------ #
    def switch_tab(self, name: str) -> None:
        if self.sm.current == "game":
            return  # tabs are hidden during a game; ignore stray taps
        self.sm.current = name
        self.nav.set_active(name)

    def go_home(self):
        self._show_nav(True)
        self.sm.current = "play"
        self.nav.set_active("play")

    def go_game(self, config=None, pool=None):
        config = config or PRESETS["Time Attack"]()
        # Recall has no card board — route it to its own screen (Play again
        # from its results comes back through here too, same as pyglet).
        if getattr(config, "recall_mode", False):
            if not self.sm.has_screen("recall"):
                from kanjire.kivyui.screens.recall import RecallScreen
                self.sm.add_widget(RecallScreen(name="recall"))
            self._show_nav(False)
            self.sm.current = "recall"
            self.sm.get_screen("recall").start(self, config)
            return
        if not self.sm.has_screen("game"):
            from kanjire.kivyui.screens.game import GameScreen
            self.sm.add_widget(GameScreen(name="game"))
        screen = self.sm.get_screen("game")
        screen.start(self, config, pool=pool)
        self._show_nav(False)
        self.sm.current = "game"

    def go_multiplayer(self, join_room: str = ""):
        if not self.sm.has_screen("multiplayer"):
            from kanjire.kivyui.screens.multiplayer import MultiplayerScreen
            self.sm.add_widget(MultiplayerScreen(self, name="multiplayer"))
        screen = self.sm.get_screen("multiplayer")
        self._show_nav(False)
        self.sm.current = "multiplayer"
        screen.open(join_room)

    def current_room_code(self) -> str:
        """The room we're hosting right now, if any (to answer a join ask)."""
        if self.sm.has_screen("multiplayer"):
            mp = self.sm.get_screen("multiplayer")
            if mp.room and mp.me == 0 and self.sm.current == "multiplayer":
                return mp.room
        return ""

    # ------------------------------------------------------------------ #
    # Dialogs — same contract as the pyglet app
    # ------------------------------------------------------------------ #
    def confirm(self, message, on_confirm, **kw) -> None:
        from kanjire.kivyui import modal
        modal.confirm(message, on_confirm, **kw)

    def prompt(self, message, on_submit, **kw) -> None:
        from kanjire.kivyui import modal
        modal.prompt(message, on_submit, **kw)

    def _show_nav(self, visible: bool) -> None:
        from kivy.metrics import dp
        self.nav.height = dp(56) if visible else 0
        self.nav.opacity = 1 if visible else 0
        self.nav.disabled = not visible

    # ------------------------------------------------------------------ #
    # QA hooks
    # ------------------------------------------------------------------ #
    def screenshot(self, path: str) -> str:
        """Write the current frame to *path* (Kivy may suffix a counter)."""
        return Window.screenshot(name=path)


def run() -> None:
    crashlog.install()
    KanjiReApp().run()
