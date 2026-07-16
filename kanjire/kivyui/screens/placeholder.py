"""Placeholder tab for features still being ported (Journey/Read/Friends)."""
from __future__ import annotations

from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen

from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel


class PlaceholderScreen(Screen):
    def __init__(self, kanji: str, title: str, **kw):
        super().__init__(**kw)
        root = AnchorLayout()
        col = BoxLayout(orientation="vertical", spacing=dp(6),
                        size_hint=(None, None), size=(dp(280), dp(120)))
        col.add_widget(JPLabel(text=kanji, font_size=sp(48),
                               color=rgba(theme.DIM)))
        col.add_widget(JPLabel(text=title, font_size=sp(16),
                               color=rgba(theme.MUTED)))
        col.add_widget(JPLabel(text="coming to mobile soon",
                               font_size=sp(12), color=rgba(theme.DIM)))
        root.add_widget(col)
        self.add_widget(root)
