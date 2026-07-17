"""Core themed widgets shared by every Kivy screen.

All colours come from the live ``kanjire.ui.theme`` module via
:func:`kanjire.kivyui.theming.rgba`; widgets snapshot theme colours at
construction (screens are rebuilt on palette change, matching the pyglet UI's
convention).
"""
from __future__ import annotations

from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme


class JPLabel(Label):
    """Label defaulting to the bundled Japanese UI face + theme text colour."""

    def __init__(self, **kw):
        kw.setdefault("font_name", UI_FONT)
        kw.setdefault("color", rgba(theme.TEXT))
        kw.setdefault("font_size", sp(16))
        super().__init__(**kw)


class Panel(BoxLayout):
    """Rounded, theme-panel-coloured container."""

    radius = NumericProperty(dp(12))
    bg_color = ListProperty(None, allownone=True)

    def __init__(self, **kw):
        bg = kw.pop("bg_color", None)
        super().__init__(**kw)
        self.bg_color = list(bg if bg is not None else rgba(theme.PANEL))
        with self.canvas.before:
            self._col = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[self.radius])
        self.bind(pos=self._sync, size=self._sync, bg_color=self._recolor)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _recolor(self, *_):
        self._col.rgba = self.bg_color


class SectionLabel(JPLabel):
    """Small all-caps section header, like the pyglet menu's row titles."""

    def __init__(self, **kw):
        kw.setdefault("color", rgba(theme.DIM))
        kw.setdefault("font_size", sp(12))
        kw.setdefault("bold", True)
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "bottom")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(26))
        super().__init__(**kw)
        self.bind(size=self.setter("text_size"))


class ThemedButton(ButtonBehavior, JPLabel):
    """Rounded-rect button with press feedback, sized for touch (>= 48dp)."""

    radius = NumericProperty(dp(10))

    def __init__(self, *, fill=None, text_color=None, **kw):
        self._fill = fill if fill is not None else theme.PANEL_HI
        kw.setdefault("color",
                      rgba(text_color) if text_color is not None
                      else rgba(theme.readable_on(self._fill)))
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(52))
        super().__init__(**kw)
        with self.canvas.before:
            self._col = Color(*rgba(self._fill))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[self.radius])
        self.bind(pos=self._sync, size=self._sync, state=self._feedback)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _feedback(self, *_):
        c = theme.tint(self._fill, 0.18) if self.state == "down" else self._fill
        self._col.rgba = rgba(c)

    def set_fill(self, fill, text_color=None) -> None:
        self._fill = fill
        self._col.rgba = rgba(fill)
        self.color = (rgba(text_color) if text_color is not None
                      else rgba(theme.readable_on(fill)))


class Chip(ThemedButton):
    """Small selectable pill (JLPT levels, theme names, option values).

    *selected_fill* overrides the selected colour (the face toggles use
    their FACE_COLORS hue instead of the accent).
    """

    def __init__(self, value, *, selected=False, selected_fill=None, **kw):
        self.value = value
        self._selected_fill = selected_fill
        kw.setdefault("height", dp(38))
        kw.setdefault("font_size", sp(14))
        kw.setdefault("radius", dp(19))
        super().__init__(**kw)
        self.set_selected(selected)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        on = self._selected_fill if self._selected_fill is not None \
            else theme.ACCENT
        self.set_fill(on if selected else theme.PANEL_HI)


class ChipRow(BoxLayout):
    """A row of chips; single- or multi-select. Calls on_change(values)."""

    def __init__(self, options, selected, *, multi=False, on_change=None,
                 colors=None, min_selected=1, **kw):
        """*options* is a list of (value, label) pairs. *colors* maps a
        value to its selected fill; *min_selected* floors multi-select."""
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("spacing", dp(6))
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(38))
        super().__init__(**kw)
        self._multi = multi
        self._min = max(1, min_selected)
        self._on_change = on_change
        self._chips: list[Chip] = []
        selected = set(selected if multi else [selected])
        for value, label in options:
            chip = Chip(value, text=str(label), selected=value in selected,
                        selected_fill=(colors or {}).get(value))
            chip.bind(on_release=self._tapped)
            self._chips.append(chip)
            self.add_widget(chip)

    def _tapped(self, chip: Chip) -> None:
        if self._multi:
            if chip.selected and \
                    sum(c.selected for c in self._chips) <= self._min:
                return  # keep the floor (a board needs >= 2 faces, etc.)
            chip.set_selected(not chip.selected)
        else:
            for c in self._chips:
                c.set_selected(c is chip)
        if self._on_change:
            self._on_change(self.values())

    def values(self):
        sel = [c.value for c in self._chips if c.selected]
        return sel if self._multi else (sel[0] if sel else None)


class ChipGrid(GridLayout):
    """ChipRow's wrapping sibling: same API, chips flow over N columns."""

    def __init__(self, options, selected, *, cols=3, multi=False,
                 on_change=None, colors=None, min_selected=1, **kw):
        kw.setdefault("cols", cols)
        kw.setdefault("spacing", dp(6))
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        self.bind(minimum_height=self.setter("height"))
        self._multi = multi
        self._min = max(1, min_selected)
        self._on_change = on_change
        self._chips: list[Chip] = []
        selected = set(selected if multi else [selected])
        for value, label in options:
            chip = Chip(value, text=str(label), selected=value in selected,
                        selected_fill=(colors or {}).get(value),
                        size_hint_y=None)
            chip.bind(on_release=self._tapped)
            self._chips.append(chip)
            self.add_widget(chip)

    def _tapped(self, chip: Chip) -> None:
        if self._multi:
            if chip.selected and \
                    sum(c.selected for c in self._chips) <= self._min:
                return
            chip.set_selected(not chip.selected)
        else:
            for c in self._chips:
                c.set_selected(c is chip)
        if self._on_change:
            self._on_change(self.values())

    def values(self):
        sel = [c.value for c in self._chips if c.selected]
        return sel if self._multi else (sel[0] if sel else None)


class NavBar(BoxLayout):
    """Bottom navigation: kanji icon + tiny label per tab."""

    TABS = [
        ("play",     "遊", "NAV_PLAY"),
        ("journey",  "旅", "NAV_JOURNEY"),
        ("read",     "読", "NAV_READ"),
        ("friends",  "友", "NAV_FRIENDS"),
        ("stats",    "計", "NAV_STATS"),
        ("settings", "設", "NAV_SETTINGS"),
    ]

    def __init__(self, on_tab, tr, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(56))
        super().__init__(**kw)
        with self.canvas.before:
            self._col = Color(*rgba(theme.PANEL))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[0])
        self.bind(pos=self._sync, size=self._sync)
        self._on_tab = on_tab
        self._items: dict[str, tuple[JPLabel, JPLabel]] = {}
        for name, icon, key in self.TABS:
            col = _NavItem(lambda n=name: self._tapped(n))
            ic = JPLabel(text=icon, font_size=sp(20), size_hint_y=None,
                         height=dp(28), color=rgba(theme.MUTED))
            lb = JPLabel(text=tr(key), font_size=sp(9.5), size_hint_y=None,
                         height=dp(14), color=rgba(theme.MUTED))
            col.add_widget(Widget())
            col.add_widget(ic)
            col.add_widget(lb)
            col.add_widget(Widget())
            self._items[name] = (ic, lb)
            self.add_widget(col)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _tapped(self, name: str) -> None:
        self._on_tab(name)

    def set_active(self, name: str) -> None:
        for tab, (ic, lb) in self._items.items():
            c = rgba(theme.ACCENT) if tab == name else rgba(theme.MUTED)
            ic.color = c
            lb.color = c


class _NavItem(ButtonBehavior, BoxLayout):
    def __init__(self, cb, **kw):
        kw.setdefault("orientation", "vertical")
        super().__init__(**kw)
        self._cb = cb

    def on_release(self):
        self._cb()
