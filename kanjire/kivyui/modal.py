"""In-app confirm/prompt dialogs (the Kivy counterpart of ui/widgets/modal.py).

Same app-level contract as the pyglet side: ``app.confirm(message, cb)`` and
``app.prompt(message, cb)`` — never a native dialog (there is none on
Android, and tkinter isn't in any frozen build).
"""
from __future__ import annotations

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput

from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, ThemedButton


def _base(height_hint: float = 0.32) -> tuple[ModalView, BoxLayout]:
    view = ModalView(size_hint=(0.86, None), height=dp(10),
                     background_color=rgba(theme.BG, 0.75),
                     overlay_color=rgba(theme.BG, 0.6))
    box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
    from kivy.graphics import Color, RoundedRectangle
    with box.canvas.before:
        col = Color(*rgba(theme.PANEL))
        rect = RoundedRectangle(pos=box.pos, size=box.size, radius=[dp(12)])
    box.bind(pos=lambda w, v: setattr(rect, "pos", v),
             size=lambda w, v: setattr(rect, "size", v))
    del col
    view.add_widget(box)
    return view, box


def _message_label(text: str) -> JPLabel:
    lbl = JPLabel(text=text, font_size=sp(15), halign="center",
                  size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(6)))
    return lbl


def confirm(message: str, on_confirm, *, danger: bool = False,
            on_cancel=None) -> None:
    view, box = _base()
    lbl = _message_label(message)
    box.add_widget(lbl)
    row = BoxLayout(orientation="horizontal", spacing=dp(10),
                    size_hint_y=None, height=dp(48))
    yes = ThemedButton(text=tr("DLG_OK"),
                       fill=theme.DANGER if danger else theme.ACCENT)
    no = ThemedButton(text=tr("DLG_CANCEL"))

    def _yes(*_):
        view.dismiss()
        on_confirm()

    def _no(*_):
        view.dismiss()
        if on_cancel:
            on_cancel()

    yes.bind(on_release=_yes)
    no.bind(on_release=_no)
    row.add_widget(no)
    row.add_widget(yes)
    box.add_widget(row)
    box.bind(minimum_height=lambda w, v: setattr(view, "height", v))
    view.open()


def prompt(message: str, on_submit, *, initial: str = "") -> None:
    view, box = _base()
    box.add_widget(_message_label(message))
    field = TextInput(text=initial, multiline=False, font_name=UI_FONT,
                      font_size=sp(16), size_hint_y=None, height=dp(44),
                      background_color=rgba(theme.PANEL_HI),
                      foreground_color=rgba(theme.TEXT),
                      cursor_color=rgba(theme.ACCENT),
                      padding=[dp(10), dp(10)])
    box.add_widget(field)
    row = BoxLayout(orientation="horizontal", spacing=dp(10),
                    size_hint_y=None, height=dp(48))
    ok = ThemedButton(text=tr("DLG_OK"), fill=theme.ACCENT)
    cancel = ThemedButton(text=tr("DLG_CANCEL"))

    def _ok(*_):
        view.dismiss()
        on_submit(field.text)

    ok.bind(on_release=_ok)
    cancel.bind(on_release=lambda *_: view.dismiss())
    field.bind(on_text_validate=_ok)
    row.add_widget(cancel)
    row.add_widget(ok)
    box.add_widget(row)
    box.bind(minimum_height=lambda w, v: setattr(view, "height", v))
    view.open()
    field.focus = True
