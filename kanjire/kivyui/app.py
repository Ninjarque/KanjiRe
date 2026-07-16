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

from kivy.app import App  # noqa: E402
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

        from kanjire.i18n import tr
        from kanjire.kivyui.widgets import NavBar

        self.root_box = BoxLayout(orientation="vertical")
        self.sm = ScreenManager(transition=FadeTransition(duration=0.12))
        self._build_tabs()
        self.nav = NavBar(self.switch_tab, tr)
        self.root_box.add_widget(self.sm)
        self.root_box.add_widget(self.nav)
        self.nav.set_active("play")
        return self.root_box

    def _build_tabs(self) -> None:
        """(Re)create every tab screen. Call after theme/locale changes."""
        from kanjire.i18n import tr
        from kanjire.kivyui.screens.placeholder import PlaceholderScreen
        from kanjire.kivyui.screens.play import PlayScreen
        from kanjire.kivyui.screens.settings import SettingsScreen
        from kanjire.kivyui.screens.stats import StatsScreen

        for name in [s.name for s in list(self.sm.screens)]:
            self.sm.remove_widget(self.sm.get_screen(name))
        self.sm.add_widget(PlayScreen(self, name="play"))
        self.sm.add_widget(PlaceholderScreen("旅", tr("NAV_JOURNEY"),
                                             name="journey"))
        self.sm.add_widget(PlaceholderScreen("読", tr("NAV_READ"),
                                             name="read"))
        self.sm.add_widget(PlaceholderScreen("友", tr("NAV_FRIENDS"),
                                             name="friends"))
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
        if not self.sm.has_screen("game"):
            from kanjire.kivyui.screens.game import GameScreen
            self.sm.add_widget(GameScreen(name="game"))
        screen = self.sm.get_screen("game")
        screen.start(self, config or PRESETS["Time Attack"](), pool=pool)
        self._show_nav(False)
        self.sm.current = "game"

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
