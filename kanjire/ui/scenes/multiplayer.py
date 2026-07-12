"""Multiplayer: shared board, one player at a time, everyone racing on score.

Four phases in one scene:

* **connect** — pick a name, then either HOST (spawns the room server
  in-process and creates a room) or JOIN (server address + room code).
* **lobby** — the room code to share, the player list, and (host) the
  turns-per-player choice + Start.
* **play** — the shared board. On your turn, click like solo play; every
  action is validated by the server and the resulting state is broadcast to
  everyone, so all clients always show exactly the same cards.
* **done** — the ranking.

Networking is JSON-over-TCP (see :mod:`kanjire.net`); the scene simply
renders the last state snapshot the server sent.
"""
from __future__ import annotations

import random

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data import db
from kanjire.i18n import tr
from kanjire.model.sampling import weighted_sample_words
from kanjire.net.client import NetClient
from kanjire.net.room_client import RoomClient
from kanjire.net.server import DEFAULT_PORT, start_in_thread
from kanjire.ui import theme
from kanjire.ui.anim import Animator, ease_out_back, ease_out_cubic, ease_out_elastic
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.layout import choose_grid, slot_center
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.card import CardView
from kanjire.ui.widgets.textinput import TextInput

HUD_H = 110
POOL_SIZE = 120
TURNS_CHOICES = (5, 10, 15)


class _MPCard:
    """Duck-typed stand-in for the engine Card that CardView renders."""

    def __init__(self, d: dict) -> None:
        self.id = d["id"]
        self.group = d["group"]
        self.face = d["face"]
        self.text = d["text"]
        self.matched = False
        self.selected = bool(d.get("selected"))


class MultiplayerScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_glow = OrderedGroup(0)
        self.g_bg = OrderedGroup(1)
        self.g_text = OrderedGroup(2)
        self.anim = Animator()

        self.phase = "connect"
        self.client: NetClient | None = None
        self.server = None            # in-process RoomServer when hosting
        self.me = -1
        self.room = ""
        self.state: dict | None = None
        self.status = ""
        self.turns_each = 10

        self.cards: dict[int, CardView] = {}
        self._board_sig: tuple = ()
        self._s = 1.0

        def lbl(size, color, *, bold=False, anchor_x="center"):
            out = Label("", font_name=JP_FONT, font_size=size, bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x=anchor_x, anchor_y="center",
                        batch=self.batch, group=self.g_text)
            out._base_fs = size
            return out

        self.title = lbl(24, theme.TEXT, bold=True)
        self.subtitle = lbl(12, theme.MUTED)
        self.status_lbl = lbl(13, theme.GOLD)
        self.big_code = lbl(44, theme.GOLD, bold=True)
        self.lbl_name = lbl(12, theme.MUTED, anchor_x="right")
        self.lbl_addr = lbl(12, theme.MUTED, anchor_x="right")
        self.lbl_code = lbl(12, theme.MUTED, anchor_x="right")
        self.lbl_turns = lbl(12, theme.MUTED, anchor_x="right")
        self.player_lbls = [lbl(15, theme.TEXT) for _ in range(8)]
        self.turn_lbl = lbl(15, theme.GOLD, bold=True)
        self.turns_left_lbl = lbl(12, theme.MUTED)
        self.hint = lbl(11, theme.DIM)
        self.labels = [self.title, self.subtitle, self.status_lbl,
                       self.big_code, self.lbl_name, self.lbl_addr,
                       self.lbl_code, self.lbl_turns, self.turn_lbl,
                       self.turns_left_lbl, self.hint] + self.player_lbls

        self.in_name = TextInput(self.batch, self.g_bg, self.g_text,
                                 self.g_text, font_size=14, placeholder="")
        self.in_addr = TextInput(self.batch, self.g_bg, self.g_text,
                                 self.g_text, font_size=14,
                                 placeholder=tr("MP_ADDR_PH"))
        self.in_code = TextInput(self.batch, self.g_bg, self.g_text,
                                 self.g_text, font_size=14,
                                 placeholder=tr("MP_CODE_PH"))
        self.in_name.set_text(app.state.setting("mp_name", ""))
        # The address stays EMPTY by default: an empty field means the
        # code-only relay path, which is what nearly everyone wants. (A
        # remembered address is offered only if the player used one before
        # AND explicitly re-enters it - we never silently force the direct
        # path on them.)
        self.inputs = [self.in_name, self.in_addr, self.in_code]

        self.host_btn = Button(tr("MP_HOST"), self._host, self.batch,
                               self.g_bg, self.g_text,
                               accent=theme.SUCCESS, font_size=15)
        self.join_btn = Button(tr("MP_JOIN"), self._join, self.batch,
                               self.g_bg, self.g_text,
                               accent=theme.ACCENT, font_size=15)
        self.start_btn = Button(tr("MP_START"), self._start, self.batch,
                                self.g_bg, self.g_text,
                                accent=theme.SUCCESS, font_size=15)
        self.back_btn = Button(tr("BTN_MENU"), self._leave, self.batch,
                               self.g_bg, self.g_text,
                               accent=theme.DIM, font_size=12)
        self.turns_btns = [
            (n, Button(f"{n}", lambda n=n: self._set_turns(n), self.batch,
                       self.g_bg, self.g_text, accent=theme.GOLD,
                       font_size=12))
            for n in TURNS_CHOICES
        ]
        self.buttons = [self.host_btn, self.join_btn, self.start_btn,
                        self.back_btn] + [b for _n, b in self.turns_btns]
        self._apply_phase()

    # ------------------------------------------------------------------ #
    # Phase plumbing
    # ------------------------------------------------------------------ #
    def _apply_phase(self) -> None:
        ph = self.phase
        self.title.text = {
            "connect": tr("MP_TITLE"), "lobby": tr("MP_LOBBY"),
            "play": "", "done": tr("MP_DONE"),   # play: the HUD is the title
        }[ph]
        self.subtitle.text = tr("MP_CONNECT_HINT") if ph == "connect" else (
            tr("MP_LOBBY_HINT") if ph == "lobby" else "")
        for w in (self.in_name, self.in_addr, self.in_code):
            if ph == "connect":
                pass
            else:
                w.unfocus()
                w.set_rect(-4000, -4000, 1, 1)
        vis = {
            self.host_btn: ph == "connect",
            self.join_btn: ph == "connect",
            self.start_btn: ph == "lobby" and self.me == 0,
            self.back_btn: True,
        }
        for b, v in vis.items():
            b.set_visible(v)
            if not v:
                b.set_rect(-4000, -4000, 1, 1)
        for _n, b in self.turns_btns:
            show = ph == "connect"
            b.set_visible(show)
            if not show:
                b.set_rect(-4000, -4000, 1, 1)
        self.lbl_name.text = tr("MP_NAME") if ph == "connect" else ""
        self.lbl_addr.text = tr("MP_ADDR") if ph == "connect" else ""
        self.lbl_code.text = tr("MP_CODE") if ph == "connect" else ""
        self.lbl_turns.text = tr("MP_TURNS") if ph == "connect" else ""
        self.big_code.text = self.room if ph == "lobby" else ""
        self.hint.text = tr("MP_HOST_HINT") if ph == "lobby" else (
            tr("MP_PLAY_HINT") if ph == "play" else "")
        if ph != "play":
            self._clear_cards()
        if ph != "lobby":
            for lbl in self.player_lbls:
                lbl.text = ""
        self.on_resize(self.width, self.height)

    def _set_phase(self, ph: str) -> None:
        if ph != self.phase:
            self.phase = ph
            self._apply_phase()

    # ------------------------------------------------------------------ #
    # Connect / host / join
    # ------------------------------------------------------------------ #
    def _my_name(self) -> str:
        name = self.in_name.text.strip() or "player"
        self.app.state.set_setting("mp_name", name)
        return name

    def _sample_pool(self) -> list[dict]:
        """The host contributes the room's words (server stays data-free)."""
        rng = random.Random()
        try:
            words = db.load_words(self.app.con, decks=["jlpt"],
                                  levels=[5, 4], require_kanji=True)
        except Exception:
            words = []
        picked = weighted_sample_words(words, POOL_SIZE, bias=0.4, rng=rng,
                                       confusable=False)
        loc = self.app.state.locale
        return [{"kanji": w.expression, "reading": w.reading,
                 "meaning": w.get_meaning(loc)} for w in picked]

    def _make_client(self, addr: str):
        """Room-code-only by default (relay, no setup); a direct server
        address is the optional advanced path (LAN / self-hosted)."""
        if addr:
            self.app.state.set_setting("mp_address", addr)
            client = NetClient()
            err = client.connect(addr, self._my_name())
        else:
            client = RoomClient()
            err = client.connect(self._my_name())
        if err:
            self.status = tr("MP_ERR_CONNECT", err=err)
            return None
        self.client = client
        self.status = tr("MP_CONNECTING")
        return client

    def _host(self) -> None:
        pool = self._sample_pool()
        if len(pool) < 4:
            self.status = tr("MP_ERR_POOL")
            return
        addr = self.in_addr.text.strip()
        if addr:
            # Advanced: also run a server here so friends can connect direct.
            try:
                self.server = start_in_thread(port=DEFAULT_PORT)
            except OSError:
                self.server = None      # already running: reuse it
            addr = f"127.0.0.1:{DEFAULT_PORT}"
        client = self._make_client(addr)
        if client is None:
            return
        client.send({"t": "create", "pool": pool,
                     "faces": ["kanji", "reading", "meaning"],
                     "board_size": 6, "turns_each": self.turns_each})

    def _join(self) -> None:
        code = self.in_code.text.strip().upper()
        if not code:
            self.status = tr("MP_ERR_CODE")
            return
        client = self._make_client(self.in_addr.text.strip())
        if client is None:
            return
        client.send({"t": "join", "room": code})

    def _set_turns(self, n: int) -> None:
        """Turns-per-player, chosen on the connect screen before hosting."""
        self.turns_each = n
        for v, b in self.turns_btns:
            b.set_selected(v == n)

    def _start(self) -> None:
        if self.client is not None:
            self.client.send({"t": "start"})

    def _leave(self) -> None:
        if self.client is not None:
            self.client.close()
        self.app.go_menu()

    # ------------------------------------------------------------------ #
    # State intake
    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> None:
        self.anim.update(dt)
        for c in self.cards.values():
            c.apply()
        if self.client is None:
            return
        for msg in self.client.poll():
            t = msg.get("t")
            if t == "welcome" and "player" in msg:
                self.me = int(msg["player"])
            elif t == "error":
                self.status = str(msg.get("msg") or "error")
                self.status_lbl.text = self.status
                if self.phase in ("lobby", "play") and not self.client.connected:
                    self._set_phase("connect")
            elif t == "state":
                self.room = msg.get("room") or self.room
                self._on_state(msg.get("state") or {}, msg.get("event"))
        self.status_lbl.text = self.status

    def _on_state(self, state: dict, event: dict | None) -> None:
        self.state = state
        self.status = ""
        if state.get("finished"):
            self._set_phase("done")
        elif state.get("started"):
            self._set_phase("play")
        else:
            self._set_phase("lobby")
        if event:
            self._on_event(event)
        if self.phase == "play":
            self._sync_board(state)
        self._refresh_hud()

    def _on_event(self, event: dict) -> None:
        sfx = self.app.audio.sfx
        et = event.get("type")
        if et == "select":
            sfx.play("select")
        elif et == "complete":
            sfx.play("match_hi" if (event.get("combo") or 0) >= 3 else "match")
            if (self.app.state.tts_on_match
                    and (event.get("word") or {}).get("reading")):
                self.app.audio.speech.say_jp(event["word"]["reading"])
        elif et == "mismatch":
            sfx.play("mismatch")
            for cid in event.get("cards") or []:
                cv = self.cards.get(cid)
                if cv is not None:
                    cv.flash = 1.0
                    cv.shake = 12.0
                    self.anim.to(cv, "flash", 0.0, 0.5, ease=ease_out_cubic)
                    self.anim.to(cv, "shake", 0.0, 0.55,
                                 ease=ease_out_elastic)
        elif et == "start":
            sfx.play("round_clear")

    # ------------------------------------------------------------------ #
    # Board rendering
    # ------------------------------------------------------------------ #
    def _clear_cards(self) -> None:
        for c in self.cards.values():
            c.delete()
        self.cards.clear()
        self._board_sig = ()

    def _sync_board(self, state: dict) -> None:
        board = state.get("board") or []
        sig = tuple(c["id"] for c in board)
        if sig != self._board_sig:
            self._clear_cards()
            self._board_sig = sig
            for d in board:
                self.cards[d["id"]] = CardView(
                    _MPCard(d), self.batch, self.g_glow, self.g_bg,
                    self.g_text)
            self._layout_cards()
            for i, c in enumerate(self.cards.values()):
                c.scale = 0.2
                c.alpha = 0.0
                delay = min(i * 0.03, 0.4)
                self.anim.to(c, "scale", 1.0, 0.4, ease=ease_out_back,
                             delay=delay)
                self.anim.to(c, "alpha", 1.0, 0.3, ease=ease_out_cubic,
                             delay=delay)
        else:
            for d in board:
                cv = self.cards.get(d["id"])
                if cv is not None:
                    was = cv.model.selected
                    cv.model.selected = bool(d.get("selected"))
                    if cv.model.selected and not was:
                        cv.glow = 0.85
                        cv.scale = 1.08
                    elif was and not cv.model.selected:
                        cv.glow = 0.0
                        cv.scale = 1.0

    def _layout_cards(self) -> None:
        n = len(self.cards)
        if not n:
            return
        s = self._s
        hud = HUD_H * s
        area_x, area_y = 40, 30
        area_w = self.width - 80
        area_h = self.height - hud - 60
        cols, rows, cw, ch = choose_grid(n, area_w, area_h, 16)
        cw = min(cw, 320)
        ch = min(ch, 280)
        for i, c in enumerate(self.cards.values()):
            cx, cy = slot_center(i, cols, rows, cw, ch, area_x, area_y,
                                 area_w, area_h, 16, count=n)
            c.set_slot(cx, cy, cw, ch)

    # ------------------------------------------------------------------ #
    # HUD
    # ------------------------------------------------------------------ #
    def _refresh_hud(self) -> None:
        st = self.state or {}
        players = st.get("players") or []
        scores = st.get("scores") or []
        combos = st.get("combos") or []
        connected = st.get("connected") or []
        turn = st.get("turn", -1)
        if self.phase in ("lobby", "play", "done"):
            order = range(len(players))
            if self.phase == "done":
                order = sorted(order, key=lambda i: -(scores[i] if i < len(scores) else 0))
            for slot, lbl in enumerate(self.player_lbls):
                idx = list(order)[slot] if slot < len(players) else None
                if idx is None:
                    lbl.text = ""
                    continue
                name = players[idx] + (tr("MP_YOU") if idx == self.me else "")
                bits = [name]
                if self.phase != "lobby":
                    bits.append(f"{scores[idx]:,}")
                    if combos[idx] >= 2:
                        bits.append(f"x{combos[idx]}")
                if idx < len(connected) and not connected[idx]:
                    bits.append(tr("MP_GONE"))
                lbl.text = "   ".join(bits)
                if self.phase == "done" and slot == 0:
                    lbl.color = theme.with_alpha(theme.GOLD, 255)
                elif self.phase == "play" and idx == turn:
                    lbl.color = theme.with_alpha(theme.GOLD, 255)
                else:
                    lbl.color = theme.with_alpha(theme.TEXT, 255)
        if self.phase == "play":
            mine = (turn == self.me)
            self.turn_lbl.text = (tr("MP_YOUR_TURN") if mine else
                                  tr("MP_THEIR_TURN",
                                     name=players[turn] if 0 <= turn < len(players) else "?"))
            left = max(0, (st.get("turns_total") or 0)
                       - (st.get("turns_used") or 0))
            self.turns_left_lbl.text = tr("MP_TURNS_LEFT", n=left)
        elif self.phase == "done":
            self.turn_lbl.text = ""
            self.turns_left_lbl.text = ""

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.phase == "connect":
            for w in self.inputs:
                if w.on_mouse_press(x, y, button, modifiers):
                    for other in self.inputs:
                        if other is not w:
                            other.unfocus()
                    return
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return
        if (self.phase == "play" and self.state
                and self.state.get("turn") == self.me
                and self.client is not None):
            for cv in self.cards.values():
                if cv.visible and cv.contains(x, y):
                    self.client.send({"t": "select", "card": cv.model.id})
                    return

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))
        if self.phase == "play" and self.state \
                and self.state.get("turn") == self.me:
            for cv in self.cards.values():
                if not cv.model.selected:
                    hover = cv.contains(x, y)
                    target = 0.25 if hover else 0.0
                    if abs(cv.glow - target) > 0.01 and cv.glow not in (0.85,):
                        cv.glow = target

    def on_text(self, text) -> None:
        for w in self.inputs:
            if w.focused:
                w.on_text(text)
                return

    def on_text_motion(self, motion) -> None:
        for w in self.inputs:
            if w.focused:
                w.on_text_motion(motion)
                return

    def on_text_motion_select(self, motion) -> None:
        for w in self.inputs:
            if w.focused:
                w.on_text_motion_select(motion)
                return

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self._leave()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        for b in self.buttons:
            b.set_scale(s)
        cx = width / 2
        self.title.x, self.title.y = cx, height - 46 * s
        self.subtitle.x, self.subtitle.y = cx, height - 76 * s
        self.status_lbl.x, self.status_lbl.y = cx, 90 * s
        self.back_btn.set_rect(16 * s, 16 * s, 120 * s, 26 * s)

        if self.phase == "connect":
            # TextInput's font doesn't scale, so its box must not shrink
            # below the text height on small windows.
            in_w, in_h = max(280, 340 * s), max(32, 34 * s)
            in_x = cx - in_w / 2 + 40 * s
            y0 = height - 160 * s
            self.lbl_name.x, self.lbl_name.y = in_x - 14 * s, y0 + in_h / 2
            self.in_name.set_rect(in_x, y0, in_w, in_h)
            y1 = y0 - 58 * s
            self.lbl_code.x, self.lbl_code.y = in_x - 14 * s, y1 + in_h / 2
            self.in_code.set_rect(in_x, y1, in_w, in_h)
            y2 = y1 - 52 * s
            self.lbl_turns.x, self.lbl_turns.y = in_x - 14 * s, y2 + 14 * s
            for i, (_n, b) in enumerate(self.turns_btns):
                b.set_rect(in_x + i * 64 * s, y2, 56 * s, 28 * s)
            self.host_btn.set_rect(cx - 260 * s, y2 - 80 * s, 240 * s, 48 * s)
            self.join_btn.set_rect(cx + 20 * s, y2 - 80 * s, 240 * s, 48 * s)
            # Advanced (optional): direct server address for LAN/self-hosting.
            y3 = y2 - 152 * s
            self.lbl_addr.x, self.lbl_addr.y = in_x - 14 * s, y3 + in_h / 2
            self.in_addr.set_rect(in_x, y3, in_w, in_h)
        elif self.phase == "lobby":
            self.big_code.x, self.big_code.y = cx, height - 150 * s
            for i, lbl in enumerate(self.player_lbls):
                lbl.x, lbl.y = cx, height - 230 * s - i * 30 * s
            self.start_btn.set_rect(cx - 130 * s, 120 * s, 260 * s, 50 * s)
            self.hint.x, self.hint.y = cx, 60 * s
        elif self.phase in ("play", "done"):
            if self.phase == "play":
                n = max(1, len((self.state or {}).get("players") or []))
                span_l, span_r = width * 0.08, width * 0.72
                for i, lbl in enumerate(self.player_lbls):
                    if n == 1:
                        lbl.x = span_l
                    else:
                        lbl.x = span_l + (span_r - span_l) * min(i, n - 1) / (n - 1)
                    lbl.anchor_x = "left"
                    lbl.y = height - 36 * s
                self.turn_lbl.x, self.turn_lbl.y = cx, height - 80 * s
                self.turns_left_lbl.x = width - 100 * s
                self.turns_left_lbl.y = height - 36 * s
                self._layout_cards()
            else:
                for i, lbl in enumerate(self.player_lbls):
                    lbl.anchor_x = "center"
                    lbl.x, lbl.y = cx, height - 180 * s - i * 40 * s
            self.hint.x, self.hint.y = cx, 16 * s

    def draw(self) -> None:
        if self.phase == "play":
            s = self._s
            fill_quad(0, self.height - HUD_H * s, self.width,
                      HUD_H * s, theme.PANEL)
            fill_quad(0, self.height - HUD_H * s - 2, self.width, 2,
                      theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        if self.client is not None:
            self.client.close()
        self._clear_cards()
        for b in self.buttons:
            b.delete()
        for w in self.inputs:
            w.delete()
