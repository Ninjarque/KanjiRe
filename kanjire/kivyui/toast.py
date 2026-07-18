"""Friend invites / requests as an overlay card (Kivy port of invite_toast).

Anchored to the bottom of the screen above the nav bar so it reaches the
player on any tab. One card at a time; the rest queue behind it. Same
action semantics as the pyglet toast: accept an invite = walk straight into
the friend's room; accept a join request = invite them back with our room
code; friend requests get accept/decline; answers to our own requests are
info-only.
"""
from __future__ import annotations

from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout

from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import GhostWhenHidden, JPLabel, ThemedButton


class InviteToast(GhostWhenHidden, BoxLayout):
    def __init__(self, app, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", dp(12))
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self._app = app
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.opacity = 0
        self.disabled = True
        with self.canvas.before:
            self._bg = Color(*rgba(theme.PANEL))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[dp(12)])
            self._bcol = Color(*rgba(theme.GOLD))
            self._border = Line(width=dp(1.4),
                                rounded_rectangle=(0, 0, 1, 1, dp(12)))
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border.rounded_rectangle = (
            self.x + dp(1), self.y + dp(1),
            max(2, self.width - dp(2)), max(2, self.height - dp(2)), dp(12))

    # ---- intake --------------------------------------------------------- #
    def push(self, msg: dict) -> None:
        self.queue.append(msg)
        if self.current is None:
            self._next()

    def _next(self) -> None:
        self.clear_widgets()
        self.current = self.queue.pop(0) if self.queue else None
        if self.current is None:
            self.opacity = 0
            self.disabled = True
            return
        self._build()

    def _text(self) -> str:
        m = self.current or {}
        who = m.get("name") or "?"
        kind = m.get("type")
        if kind == "invite":
            return tr("FR_INVITE_MSG", name=who)
        if kind == "friend_request":
            return tr("FR_WANTS_FRIEND", name=who)
        if kind == "friend_accept":
            return tr("FR_ACCEPTED_MSG", name=who)
        if kind == "friend_decline":
            return tr("FR_DECLINED_MSG", name=who)
        return tr("FR_REQUEST_MSG", name=who)

    def _build(self) -> None:
        m = self.current or {}
        kind = m.get("type")
        accent = theme.SUCCESS if kind == "invite" else theme.GOLD
        self._bcol.rgba = rgba(accent)
        self.opacity = 1
        self.disabled = False

        lbl = JPLabel(text=self._text(), font_size=sp(13.5), halign="left",
                      size_hint_y=None)
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                 texture_size=lambda w, ts: setattr(w, "height",
                                                    ts[1] + dp(4)))
        self.add_widget(lbl)

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(38))
        if kind in ("friend_accept", "friend_decline"):
            ok = ThemedButton(text=tr("FR_OK"), height=dp(38),
                              font_size=sp(13))
            ok.bind(on_release=lambda *_: self._next())
            row.add_widget(ok)
        else:
            no = ThemedButton(text=tr("FR_DECLINE"), height=dp(38),
                              font_size=sp(13))
            yes = ThemedButton(text=tr("FR_ACCEPT"), fill=accent,
                               height=dp(38), font_size=sp(13))
            no.bind(on_release=lambda *_: self._decline())
            yes.bind(on_release=lambda *_: self._accept())
            row.add_widget(no)
            row.add_widget(yes)
        self.add_widget(row)
        self.height = lbl.height + dp(38) + dp(12) * 2 + dp(8)

    # ---- actions ---------------------------------------------------------- #
    def _accept(self) -> None:
        m = self.current or {}
        kind = m.get("type")
        if kind == "friend_request":
            self._app.friends.accept_request(str(m.get("from") or ""),
                                             str(m.get("name") or "?"))
            self._next()
            return
        if kind == "invite":
            self._app.go_multiplayer(join_room=str(m.get("room") or ""))
        else:
            room = self._app.current_room_code()
            if room:
                self._app.friends.invite(str(m.get("from") or ""), room)
        self._next()

    def _decline(self) -> None:
        m = self.current or {}
        if m.get("type") == "friend_request":
            self._app.friends.decline_request(str(m.get("from") or ""))
        self._next()
