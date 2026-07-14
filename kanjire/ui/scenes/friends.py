"""The Friends tab: who's online, requests waiting on you, and one-click play.

Everything you can do with a friend lives here:

* **Requests** you've received sit at the top - accept and you're both friends,
  decline and they're told.
* **Friends** show what they're doing (online / in a room / playing). If they're
  sitting in a room you can **Ask to join**; if *you* are hosting one, you can
  **Invite** them straight into it.
* **Sent** requests are listed so you know you're waiting on someone.

The list is rebuilt only when something actually changes (presence moves, a
request lands), not every frame.
"""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.i18n import tr
from kanjire.net import friends as fr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar

#: Presence dot + label colour per status.
_DOT = {fr.ONLINE: "●", fr.LOBBY: "●", fr.PLAYING: "●", fr.OFFLINE: "○"}


class FriendsScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)

        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_JOURNEY"),  lambda: self.app.go_journey()),
             (tr("NAV_READ"),     lambda: self.app.go_reading()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: None),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_FRIENDS"))

        def lbl(size, color, *, bold=False, anchor_x="center"):
            out = Label("", font_name=JP_FONT, font_size=size, bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x=anchor_x, anchor_y="center",
                        batch=self.batch, group=self.g_text)
            out._base_fs = size
            return out

        self.title = lbl(24, theme.TEXT, bold=True)
        self.title.text = tr("FR_TITLE")
        self.hint = lbl(12, theme.MUTED)
        self.mycode = lbl(11, theme.DIM)
        self.empty = lbl(13, theme.DIM)
        self.labels = [self.title, self.hint, self.mycode, self.empty]

        self.mp_btn = Button(tr("FR_GO_MULTIPLAYER"),
                             lambda: self.app.go_multiplayer(),
                             self.batch, self.g_bg, self.g_text,
                             accent=theme.ACCENT, font_size=13)
        self.buttons: list[Button] = [self.mp_btn]

        self._rows: list[dict] = []       # built by _sync
        self._sig: tuple = ()
        self._s = 1.0
        self._sync()

    # ------------------------------------------------------------------ #
    def _model(self) -> tuple[list[dict], list[dict], list[dict]]:
        svc = self.app.friends
        return (svc.pending_requests(), svc.friends(), self.app.state.requests_out)

    def _sync(self) -> None:
        """Rebuild the rows when (and only when) something changed."""
        pending, friends, sent = self._model()
        hosting = self.app.current_room_code()
        sig = (tuple((p["code"], p["name"]) for p in pending),
               tuple((f["code"], f["name"], f["status"], f["room"])
                     for f in friends),
               tuple(r["code"] for r in sent), hosting)
        if sig == self._sig:
            return
        self._sig = sig

        for row in self._rows:
            row["label"].delete()
            for b in row["buttons"]:
                b.delete()
                if b in self.buttons:
                    self.buttons.remove(b)
        self._rows = []

        def row(text, colour, buttons, kind):
            lb = Label(text, font_name=JP_FONT, font_size=14,
                       color=theme.with_alpha(colour, 255),
                       anchor_x="left", anchor_y="center",
                       batch=self.batch, group=self.g_text)
            lb._base_fs = 14
            self.buttons.extend(buttons)
            self._rows.append({"label": lb, "buttons": buttons, "kind": kind})

        def btn(key, cb, accent):
            return Button(tr(key), cb, self.batch, self.g_bg, self.g_text,
                          accent=accent, font_size=12)

        # 1) Requests waiting on YOU - the only thing here that's time-critical.
        for p in pending:
            row(tr("FR_WANTS_FRIEND", name=p["name"]), theme.GOLD,
                [btn("FR_ACCEPT", lambda c=p["code"], n=p["name"]:
                     self._accept(c, n), theme.SUCCESS),
                 btn("FR_DECLINE", lambda c=p["code"]: self._decline(c),
                     theme.DIM)],
                "request")

        # 2) Your friends, with what they're up to.
        for f in friends:
            status = f["status"]
            text = (f"{_DOT.get(status, '○')} {f['name']}   "
                    f"{tr('FR_ST_' + status.upper())}")
            colour = theme.TEXT if status != fr.OFFLINE else theme.DIM
            buttons = []
            if hosting and status in (fr.ONLINE, fr.LOBBY):
                buttons.append(btn("FR_INVITE",
                                   lambda c=f["code"]: self._invite(c),
                                   theme.SUCCESS))
            elif not hosting and status == fr.LOBBY and f["room"]:
                buttons.append(btn("FR_ASK_JOIN",
                                   lambda c=f["code"]: self._ask_join(c),
                                   theme.ACCENT))
            buttons.append(btn("FR_REMOVE", lambda c=f["code"]:
                               self._remove(c), theme.DIM))
            row(text, colour, buttons, "friend")

        # 3) People you've asked, who haven't answered yet.
        for r in sent:
            row(tr("FR_SENT", name=r["name"]), theme.MUTED, [], "sent")

        self.empty.text = "" if (pending or friends or sent) else tr("FR_NONE")
        self.on_resize(self.width, self.height)

    # ---- actions ------------------------------------------------------- #
    def _accept(self, code, name) -> None:
        self.app.friends.accept_request(code, name)
        self._sig = ()

    def _decline(self, code) -> None:
        self.app.friends.decline_request(code)
        self._sig = ()

    def _remove(self, code) -> None:
        self.app.friends.remove_friend(code)
        self._sig = ()

    def _invite(self, code) -> None:
        room = self.app.current_room_code()
        if room:
            self.app.friends.invite(code, room)
            self.hint.text = tr("FR_INVITED")

    def _ask_join(self, code) -> None:
        self.app.friends.ask_to_join(code)
        self.hint.text = tr("FR_ASKED")

    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> None:
        self._sync()

    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        for lb in self.labels:
            lb.font_size = max(9, round(lb._base_fs * s))
        cx = width / 2
        self.nav.set_scale(s)
        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)
        self.title.x, self.title.y = cx, height - 120 * s
        self.hint.x, self.hint.y = cx, height - 150 * s
        self.mycode.x, self.mycode.y = cx, 40 * s + self.app.banner.height()
        self.mycode.text = tr("FR_MY_CODE", code=self.app.state.friend_code)
        self.empty.x, self.empty.y = cx, height / 2

        panel_w = min(760 * s, width - 80 * s)
        x = cx - panel_w / 2
        y = height - 200 * s
        row_h = 42 * s
        for i, row in enumerate(self._rows):
            ry = y - i * row_h
            lb = row["label"]
            lb.font_size = max(10, round(14 * s))
            lb.x, lb.y = x, ry
            bx = x + panel_w
            for b in reversed(row["buttons"]):
                bw = (48 if b.text == tr("FR_REMOVE") else 118) * s
                bx -= bw + 8 * s
                b.set_scale(s)
                b.set_rect(bx, ry - 15 * s, bw, 30 * s)
        bottom = y - len(self._rows) * row_h - 30 * s
        self.mp_btn.set_scale(s)
        self.mp_btn.set_rect(cx - 130 * s,
                             max(90 * s + self.app.banner.height(), bottom),
                             260 * s, 40 * s)

    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.nav.on_mouse_press(x, y):
            return
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self.app.go_menu()

    def draw(self) -> None:
        self.batch.draw()

    def on_exit(self) -> None:
        self.nav.delete()
        for b in self.buttons:
            b.delete()
