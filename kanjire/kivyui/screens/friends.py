"""Friends tab: your code, pending requests, live friend list with actions.

Mirrors the pyglet FriendsScene over the same FriendService: presence-aware
rows, invite (when you're hosting), ask-to-join (when they're in a room),
remove with confirm, and add-by-code which sends a mutual-consent request.
"""
from __future__ import annotations

from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from kanjire.i18n import tr
from kanjire.kivyui import modal
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, Panel, SectionLabel, ThemedButton

_STATUS_TR = {"online": "FR_ST_ONLINE", "lobby": "FR_ST_LOBBY",
              "playing": "FR_ST_PLAYING", "offline": "FR_ST_OFFLINE"}
_STATUS_COL = {"online": "SUCCESS", "lobby": "GOLD", "playing": "ACCENT",
               "offline": "DIM"}


class FriendsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._ev = None
        self._sig = None
        self._build_static()

    def on_pre_enter(self, *_):
        self._refresh(force=True)

    def on_enter(self, *_):
        if self._ev is None:
            self._ev = Clock.schedule_interval(lambda dt: self._refresh(), 2.0)

    def on_leave(self, *_):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    # ------------------------------------------------------------------ #
    def _build_static(self) -> None:
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        root.add_widget(JPLabel(text=tr("FR_TITLE"), bold=True,
                                font_size=sp(22), size_hint_y=None,
                                height=dp(34)))
        self._code_lbl = JPLabel(
            text=tr("FR_MY_CODE", code=self._app.state.friend_code),
            color=rgba(theme.MUTED), font_size=sp(13), halign="left",
            size_hint_y=None, height=dp(22))
        self._code_lbl.bind(size=self._code_lbl.setter("text_size"))
        root.add_widget(self._code_lbl)

        add = ThemedButton(text=tr("FR_ADD"), fill=theme.ACCENT,
                           height=dp(44))
        add.bind(on_release=lambda *_: self._add_by_code())
        root.add_widget(add)

        scroller = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self._body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                                padding=[0, dp(4)])
        self._body.bind(minimum_height=self._body.setter("height"))
        scroller.add_widget(self._body)
        root.add_widget(scroller)
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    def _refresh(self, force: bool = False) -> None:
        fr = self._app.friends
        pending = fr.pending_requests()
        friends = fr.friends()
        hosting = self._app.current_room_code()
        sig = (tuple((p["code"], p["name"]) for p in pending),
               tuple((f["code"], f["name"], f["status"], f["room"])
                     for f in friends),
               hosting)
        if not force and sig == self._sig:
            return
        self._sig = sig
        self._body.clear_widgets()

        if pending:
            self._body.add_widget(SectionLabel(text=tr("FR_WANTS_FRIEND",
                                                       name="").strip()))
            for p in pending:
                self._body.add_widget(_RequestRow(self._app, p))

        self._body.add_widget(SectionLabel(text=tr("FR_TITLE")))
        if not friends:
            empty = JPLabel(text=tr("FR_NONE"), color=rgba(theme.DIM),
                            font_size=sp(13), halign="left",
                            size_hint_y=None)
            empty.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                       texture_size=lambda w, ts: setattr(w, "height",
                                                          ts[1] + dp(8)))
            self._body.add_widget(empty)
        for f in friends:
            self._body.add_widget(_FriendRow(self._app, f, hosting))

    def _add_by_code(self) -> None:
        def submit(code):
            code = (code or "").strip().upper()
            if not code:
                return
            self._app.friends.send_friend_request(code)
            self._refresh(force=True)
        modal.prompt(tr("FR_MY_CODE", code=self._app.state.friend_code)
                     + "\n" + tr("FR_ADD"), submit)


class _RequestRow(Panel):
    def __init__(self, app, req: dict, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(52))
        kw.setdefault("padding", [dp(10), dp(6)])
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        name = JPLabel(text=f"{req['name']}  ·  {req['code']}",
                       halign="left", valign="middle", font_size=sp(14))
        name.bind(size=name.setter("text_size"))
        self.add_widget(name)
        yes = ThemedButton(text=tr("FR_ACCEPT"), fill=theme.SUCCESS,
                           size_hint_x=None, width=dp(86), height=dp(38),
                           font_size=sp(12.5))
        no = ThemedButton(text=tr("FR_DECLINE"), size_hint_x=None,
                          width=dp(86), height=dp(38), font_size=sp(12.5))
        yes.bind(on_release=lambda *_: (
            app.friends.accept_request(req["code"], req["name"]),
            app.sm.get_screen("friends")._refresh(force=True)))
        no.bind(on_release=lambda *_: (
            app.friends.decline_request(req["code"]),
            app.sm.get_screen("friends")._refresh(force=True)))
        self.add_widget(yes)
        self.add_widget(no)


class _FriendRow(Panel):
    def __init__(self, app, f: dict, hosting: str, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(56))
        kw.setdefault("padding", [dp(10), dp(6)])
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        status = f.get("status") or "offline"
        col = getattr(theme, _STATUS_COL.get(status, "DIM"))

        left = BoxLayout(orientation="vertical")
        name = JPLabel(text=f["name"], halign="left", valign="middle",
                       font_size=sp(15), size_hint_y=0.6)
        name.bind(size=name.setter("text_size"))
        st = JPLabel(text="● " + tr(_STATUS_TR.get(status, "FR_ST_OFFLINE")),
                     halign="left", valign="middle", font_size=sp(11.5),
                     color=rgba(col), size_hint_y=0.4)
        st.bind(size=st.setter("text_size"))
        left.add_widget(name)
        left.add_widget(st)
        self.add_widget(left)

        if hosting and status != "offline":
            b = ThemedButton(text=tr("FR_INVITE"), fill=theme.SUCCESS,
                             size_hint_x=None, width=dp(84), height=dp(38),
                             font_size=sp(12.5))
            b.bind(on_release=lambda *_: app.friends.invite(f["code"],
                                                            hosting))
            self.add_widget(b)
        elif status in ("lobby", "playing") and f.get("room"):
            b = ThemedButton(text=tr("FR_ASK_JOIN"), fill=theme.ACCENT,
                             size_hint_x=None, width=dp(96), height=dp(38),
                             font_size=sp(12.5))
            b.bind(on_release=lambda *_: app.friends.ask_to_join(f["code"]))
            self.add_widget(b)

        rm = ThemedButton(text="×", size_hint_x=None, width=dp(40),
                          height=dp(38), font_size=sp(16),
                          text_color=theme.DANGER)
        rm.bind(on_release=lambda *_: modal.confirm(
            f"{tr('FR_REMOVE')}  {f['name']}?",
            lambda: (app.friends.remove_friend(f["code"]),
                     app.sm.get_screen("friends")._refresh(force=True)),
            danger=True))
        self.add_widget(rm)
