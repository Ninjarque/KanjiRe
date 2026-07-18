"""Touch marker debug overlay: draw a ring exactly where Kivy sees a touch.

The one tool that settles any "taps land in the wrong place" report from a
device we can't attach a debugger to: turn it on in Settings, tap around,
and compare the ring to your fingertip. Ring under the finger = input
coordinates are fine (look at widget geometry); ring offset = the input
layer itself is transformed, and the offset's direction/size is readable
straight off a screenshot. The info line carries the numbers that matter.
"""
from __future__ import annotations

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line
from kivy.metrics import dp, sp
from kivy.uix.widget import Widget

from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel


class TouchProbe:
    """Owns a zero-size canvas host + info label inside the root overlay."""

    def __init__(self, app, root) -> None:
        self._app = app
        # Zero-sized widget: its canvas can draw anywhere, it never collides
        # with touches, and sitting last in the root it paints on top.
        self._host = Widget(size_hint=(None, None), size=(0, 0))
        root.add_widget(self._host)
        self._info = JPLabel(text="", color=rgba(theme.GOLD),
                             font_size=sp(11), halign="left", valign="top",
                             size_hint=(None, None), opacity=0)
        self._info.bind(texture_size=self._info.setter("size"))
        root.add_widget(self._info)
        self.on = False
        Window.bind(on_touch_down=self._mark, on_touch_move=self._mark)

    def set_on(self, value: bool) -> None:
        self.on = bool(value)
        self._info.opacity = 1 if self.on else 0
        if not self.on:
            self._host.canvas.clear()
        else:
            self._update_info(None)

    # ------------------------------------------------------------------ #
    def _mark(self, _win, touch) -> bool:
        if not self.on:
            return False
        x, y = touch.pos
        r = dp(24)
        group = str(touch.uid) + ":" + str(Clock.get_boottime())
        with self._host.canvas:
            Color(*rgba(theme.GOLD, 0.9), group=group)
            Line(circle=(x, y, r), width=dp(1.5), group=group)
            Line(points=[x - r, y, x + r, y], width=dp(1), group=group)
            Line(points=[x, y - r, x, y + r], width=dp(1), group=group)
            Color(*rgba(theme.DANGER, 0.9), group=group)
            Ellipse(pos=(x - dp(2), y - dp(2)), size=(dp(4), dp(4)),
                    group=group)
        Clock.schedule_once(
            lambda *_: self._host.canvas.remove_group(group), 2.5)
        self._update_info(touch)
        return False    # never consume — the app must behave normally

    def _update_info(self, touch) -> None:
        try:
            nav_h = int(self._app.nav.height)
        except Exception:
            nav_h = -1
        t = (f"touch ({touch.x:.0f}, {touch.y:.0f})  "
             f"spos ({touch.sx:.3f}, {touch.sy:.3f})\n" if touch else "")
        self._info.text = (
            t
            + f"win {tuple(int(v) for v in Window.size)}  "
              f"sys {tuple(int(v) for v in Window.system_size)}\n"
            + f"density {Window._density:.2f}  dpi {Window.dpi:.0f}  "
              f"kb {Window.keyboard_height}  nav {nav_h}  "
              f"soft '{Window.softinput_mode}'")
        self._info.x = dp(8)
        self._info.top = Window.height - dp(8)
