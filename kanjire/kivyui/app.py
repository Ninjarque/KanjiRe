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

_ON_ANDROID = "ANDROID_ARGUMENT" in os.environ

if _ON_ANDROID:
    # SDL2 delivers native touch on Android. Any [input] provider on top
    # (the desktop 'mouse' one, or a device default) re-delivers every tap
    # as a SECOND synthesized event — every tap toggled twice: cards played
    # the select sound but ended deselected, toggles flipped back, and only
    # multi-finger taps (whose duplicate pairs cancel unevenly) "worked".
    for _name, _ in Config.items("input"):
        Config.remove_option("input", _name)
else:
    Config.set("input", "mouse", "mouse,disable_multitouch")
# Let a drag that STARTS on a button still scroll the list: ScrollView holds
# the touch this long (ms) before handing it to the child, so big buttons
# don't pin the page. 55ms default is tuned for mouse wheels, not thumbs.
Config.set("widgets", "scroll_timeout", "250")
Config.set("widgets", "scroll_distance", "20")
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

IS_ANDROID = _ON_ANDROID


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
        # NO global softinput pan mode. Kivy implements 'pan'/'below_target'
        # as a pure RENDER translation (update_viewport shifts the modelview
        # by keyboard_height) and never counter-shifts touches — so whenever
        # p4a's get_keyboard_height() misreports (system bars/cutouts counted
        # as "keyboard", a known Android quirk, worse on foldables and in
        # fullscreen), the whole scene draws offset from its hitboxes and
        # buttons only respond near one edge — worst near the screen bottom.
        # That was THE long-standing "hard to press" bug. Screens that need
        # the keyboard use top-anchored layouts (Recall additionally uses
        # 'resize' while active, which shrinks Window.size — render and
        # touch stay consistent there).
        # Android back button (and desktop Esc) arrives as key 27.
        Window.bind(on_keyboard=self._on_hard_key)

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

        # Device sync: serverless, E2E-encrypted progress exchange between
        # the player's own devices. Connects only if this device is linked
        # (pairing connects on demand from Settings).
        from kanjire.net.syncsvc import SyncService
        self.sync = SyncService(self.state, self._stats_con)
        if self.sync.linked and not os.environ.get("KANJIRE_NO_NETWORK"):
            threading.Thread(target=self.sync.connect, daemon=True,
                             name="kanjire-sync").start()

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

        from kanjire.kivyui.update_banner import UpdateBanner
        from kanjire.update.controller import UpdateController

        self.updates = UpdateController(self.state)
        # Pre-warm the checker's call-time imports ON THE MAIN THREAD. The
        # boot check used to import ssl/PyNaCl(cffi dlopen) from its worker
        # while the main thread was still importing Kivy screens — a
        # threaded-import deadlock wedges the check forever on Android.
        try:
            import ssl  # noqa: F401
            from nacl import signing  # noqa: F401
        except Exception:
            pass
        # And start the boot check only once the app is settled.
        Clock.schedule_once(lambda dt: self.updates.maybe_start(), 3.0)

        root = FloatLayout()
        root.add_widget(self.root_box)
        self.toast = InviteToast(self)
        root.add_widget(self.toast)
        self.banner = UpdateBanner(self)
        root.add_widget(self.banner)
        Window.bind(size=lambda *_: self._place_toast())
        self._place_toast()

        # Touch-marker debug overlay (Settings → Display): draws a ring at
        # every touch Kivy receives, so any "taps land in the wrong place"
        # report becomes a screenshot we can read numbers off.
        from kanjire.kivyui.touchprobe import TouchProbe
        self.touch_probe = TouchProbe(self, root)
        self.touch_probe.set_on(
            self.state.setting("touch_debug", "off") == "on")

        # Player-chosen immersive fullscreen (Android): applied at runtime,
        # never baked into the build. Delayed so the activity is settled.
        if IS_ANDROID and self.state.setting("fullscreen", "off") == "on":
            from kanjire.kivyui.androidui import set_immersive
            Clock.schedule_once(lambda dt: set_immersive(True), 1.0)

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
        self.banner.width = w
        self.banner.x = (Window.width - w) / 2
        self.banner.top = Window.height - dp(8)

    def _net_tick(self, _dt) -> None:
        self.friends.tick()
        for msg in self.friends.poll():
            self.toast.push(msg)
        self.banner.sync()
        try:
            self.sync.tick()
        except Exception:
            pass

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

    # ------------------------------------------------------------------ #
    # Android back button / desktop Esc
    # ------------------------------------------------------------------ #
    def _on_hard_key(self, window, key, *args) -> bool:
        # Android back arrives as 27 on most devices, 1001 on some; desktop
        # Esc is 27. ANY unexpected crash in here must still consume the
        # key — an exception used to fall through and close the whole app.
        if key not in (27, 1001):
            return False
        try:
            return self._handle_back()
        except Exception:
            try:
                from kanjire import crashlog
                import sys
                crashlog.record(*sys.exc_info())
            except Exception:
                pass
            return True

    def _handle_back(self) -> bool:
        # Typing? Back just closes the keyboard (unfocus), nothing else.
        from kivy.uix.behaviors.focus import FocusBehavior
        for widget in list(FocusBehavior._keyboards.values()):
            # Only widgets actually on screen: an input on a backgrounded
            # ScreenManager screen can keep stale focus and would hijack
            # the key.
            if (widget is not None and getattr(widget, "focus", False)
                    and widget.get_root_window() is not None):
                widget.focus = False
                return True
        current = self.sm.current
        if current == "game":
            self.sm.get_screen("game")._quit()
            return True
        if current == "recall":
            rs = self.sm.get_screen("recall")
            if rs._overlay is not None:
                rs._quit()      # on the results: back = leave
            else:
                rs._finish()    # mid-drill: back = bail to results
            return True
        if current == "multiplayer":
            self.sm.get_screen("multiplayer").leave()
            return True
        if current != "play":
            self.switch_tab("play")   # any other tab: back to the main one
            return True
        # On the Play tab: leaving the app. Confirm, unless opted out.
        if self.state.setting("back_confirm", "on") != "on":
            return False              # let the OS close us
        self._confirm_exit()
        return True

    def _confirm_exit(self) -> None:
        from kivy.metrics import dp, sp
        from kivy.uix.boxlayout import BoxLayout

        from kanjire.i18n import tr
        from kanjire.kivyui.modal import _base, _message_label
        from kanjire.kivyui.widgets import ThemedButton
        view, box = _base()
        box.add_widget(_message_label(tr("EXIT_ASK")))
        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(48))
        stay = ThemedButton(text=tr("DLG_CANCEL"), font_size=sp(13))
        close = ThemedButton(text=tr("DLG_OK"), fill=theme.ACCENT,
                             font_size=sp(13))
        always = ThemedButton(text=tr("EXIT_ALWAYS"), font_size=sp(12))
        stay.bind(on_release=lambda *_: view.dismiss())
        close.bind(on_release=lambda *_: (view.dismiss(), self.stop()))

        def _always(*_):
            # "Never ask again" — also a toggle in Settings to turn it back.
            self.state.set_setting("back_confirm", "off")
            view.dismiss()
            self.stop()

        always.bind(on_release=_always)
        row.add_widget(stay)
        row.add_widget(close)
        box.add_widget(row)
        box.add_widget(always)
        box.bind(minimum_height=lambda w, v: setattr(view, "height", v))
        view.open()

    def on_pause(self):
        # Android: returning True keeps the app alive in the background
        # (returning None/False lets the OS kill it on focus loss). Save
        # state now — there may be no clean on_stop if we're reaped later.
        try:
            self.state.save()
        except Exception:
            pass
        return True

    def on_resume(self):
        # MQTT reconnects on its own (paho loop); rooms resync from the
        # broker's retained snapshot, friends re-announce on _on_connect.
        pass

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

    def go_game(self, config=None, pool=None, recall_words=None):
        config = config or PRESETS["Time Attack"]()
        # Recall has no card board — route it to its own screen (Play again
        # from its results comes back through here too, same as pyglet).
        if getattr(config, "recall_mode", False):
            self.go_recall_drill(config)
            return
        if not self.sm.has_screen("game"):
            from kanjire.kivyui.screens.game import GameScreen
            self.sm.add_widget(GameScreen(name="game"))
        screen = self.sm.get_screen("game")
        screen.start(self, config, pool=pool, recall_words=recall_words)
        self._show_nav(False)
        self.sm.current = "game"

    def go_recall_drill(self, config, words=None):
        """The typed-recall screen: standalone mode, or a session epilogue
        over explicit *words* (Journey stations / Today's hardest reviews)."""
        if not self.sm.has_screen("recall"):
            from kanjire.kivyui.screens.recall import RecallScreen
            self.sm.add_widget(RecallScreen(name="recall"))
        self._show_nav(False)
        self.sm.current = "recall"
        self.sm.get_screen("recall").start(self, config, words=words)

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
