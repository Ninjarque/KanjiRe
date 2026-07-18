"""Multiplayer on Kivy: connect → lobby → play → done, over the same relay.

Reuses RoomClient (MQTT relay, room codes, retained snapshots) unchanged.
Differences from the desktop scene are purely presentational/touch:

* long-press a card (on your turn) to *point* at it for everyone —
  the touch replacement for desktop's hover-to-point;
* lobby settings are chips, host-editable, live-broadcast;
* the friends sidebar becomes an "invite friends" section in the lobby.

Set ``KANJIRE_MP_LOOPBACK=1`` to wire clients to an in-process broker
(offline tests: host and joiner meet inside one process).
"""
from __future__ import annotations

import os
import random

from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from kanjire.data import db
from kanjire.i18n import tr
from kanjire.kana import hira_to_romaji
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.screens.game import CardWidget
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import (
    ChipRow,
    JPLabel,
    Panel,
    SectionLabel,
    ThemedButton,
)
from kanjire.model.sampling import weighted_sample_words
from kanjire.net.room_client import RoomClient
from kanjire.ui.layout import choose_grid, slot_center

POOL_SIZE = 140
#: Hold a card this long (on your turn) and the room is told you're eyeing it.
POINT_HOLD = 0.45
TURNS_CHOICES = (5, 10, 15)
BOARD_CHOICES = (4, 6, 8)
CARDS_CHOICES = (2, 3, 4)
LEVEL_CHOICES = (5, 4, 3, 2, 1)
FACES_FOR = {
    2: ("kanji", "meaning"),
    3: ("kanji", "reading", "meaning"),
    4: ("kanji", "reading", "romaji", "meaning"),
}

_loopback_broker = None


def _make_transport():
    """None for the real relay; a LoopbackTransport under the test env var."""
    global _loopback_broker
    if not os.environ.get("KANJIRE_MP_LOOPBACK"):
        return None
    from kanjire.net.transport import LoopbackBroker, LoopbackTransport
    if _loopback_broker is None:
        _loopback_broker = LoopbackBroker()
    return LoopbackTransport(_loopback_broker)


class _MPCard:
    """Duck-typed stand-in for the engine Card that CardWidget renders."""

    def __init__(self, d: dict) -> None:
        self.id = d["id"]
        self.group = d["group"]
        self.face = d["face"]
        self.text = d["text"]
        self.matched = bool(d.get("matched"))
        self.selected = bool(d.get("selected"))


class MultiplayerScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._ev = None
        self._reset()
        self.root_box = BoxLayout(orientation="vertical",
                                  padding=[dp(14), dp(10)], spacing=dp(8))
        self.add_widget(self.root_box)
        # After-match sentence, per each client's own display setting (the
        # lookup is deterministic — everyone reads the same sentence).
        from kanjire.kivyui.sentence_toast import SentenceToast
        self._sent = SentenceToast(app)
        self.add_widget(self._sent)

    def _reset(self) -> None:
        self.client = None
        self.me = -1
        self.room = ""
        self.state: dict = {}
        self.stage = "connect"
        self.status = ""
        self.cards: dict[int, CardWidget] = {}
        self._board_sig = ()
        self._pointed = None
        self._hold_ev = None
        self._pointer_shown = None

    # ------------------------------------------------------------------ #
    # Entry / exit
    # ------------------------------------------------------------------ #
    def open(self, join_room: str = "") -> None:
        """(Re)enter multiplayer, optionally walking straight into a room."""
        self._teardown_client()
        self._reset()
        self._build_connect()
        if join_room:
            self._join(join_room)

    def on_enter(self, *_):
        if self._ev is None:
            self._ev = Clock.schedule_interval(self._tick, 1 / 10)

    def on_leave(self, *_):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def leave(self) -> None:
        self._teardown_client()
        from kanjire.net import friends as fr
        self._app.friends.set_status(fr.ONLINE, "")
        self._app.go_home()

    def _teardown_client(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    # ------------------------------------------------------------------ #
    # Net pump
    # ------------------------------------------------------------------ #
    def _tick(self, dt: float) -> None:
        if self.client is None:
            return
        self.client.tick()
        for msg in self.client.poll():
            t = msg.get("t")
            if t == "welcome" and "player" in msg:
                self.me = int(msg["player"])
            elif t == "error":
                self.status = str(msg.get("msg") or "error")
                if self.stage in ("lobby", "play") and not self.client.connected:
                    self._set_stage("connect")
                else:
                    self._sync_status()
            elif t == "state":
                self.room = msg.get("room") or self.room
                self._on_state(msg.get("state") or {}, msg.get("event"))

    def _on_state(self, state: dict, event: dict | None) -> None:
        self.state = state
        self.status = ""
        if state.get("finished"):
            self._set_stage("done")
        elif state.get("started"):
            self._set_stage("play")
        else:
            self._set_stage("lobby")
        if self.stage == "play":
            self._sync_board(state)
            self._refresh_hud()
        elif self.stage == "lobby":
            self._sync_lobby(state)
        elif self.stage == "done":
            self._sync_done(state)
        if event:
            self._on_event(event)

    def _on_event(self, event: dict) -> None:
        sfx = self._app.audio.sfx
        et = event.get("type")
        if et == "select":
            sfx.play("select")
        elif et == "complete":
            sfx.play("match_hi" if (event.get("combo") or 0) >= 3 else "match")
            word = event.get("word") or {}
            if self._app.state.tts_on_match and word.get("reading"):
                self._app.audio.speech.say_jp(word["reading"])
            if word.get("kanji") and self.stage == "play":
                self._sent.show(word["kanji"], word.get("reading") or "")
            for cid in event.get("cards") or []:
                w = self.cards.get(cid)
                if w is not None:
                    w.celebrate_hold()
        elif et == "mismatch":
            sfx.play("mismatch")
            for cid in event.get("cards") or []:
                w = self.cards.get(cid)
                if w is not None:
                    w.flash_error()
        elif et == "start":
            sfx.play("round_clear")

    # ------------------------------------------------------------------ #
    # Stage switching
    # ------------------------------------------------------------------ #
    def _set_stage(self, stage: str) -> None:
        if stage == self.stage:
            return
        self.stage = stage
        self._publish_presence()
        if stage == "connect":
            self._build_connect()
        elif stage == "lobby":
            self._build_lobby()
        elif stage == "play":
            self._build_play()
        elif stage == "done":
            self._build_done()

    def _publish_presence(self) -> None:
        from kanjire.net import friends as fr
        status = {"lobby": fr.LOBBY, "play": fr.PLAYING,
                  "done": fr.LOBBY}.get(self.stage, fr.ONLINE)
        self._app.friends.set_status(status, self.room if self.room else "")

    def _sync_status(self) -> None:
        if getattr(self, "_status_lbl", None) is not None:
            self._status_lbl.text = self.status

    # ------------------------------------------------------------------ #
    # Stage: connect
    # ------------------------------------------------------------------ #
    def _build_connect(self) -> None:
        self.root_box.clear_widgets()
        rb = self.root_box
        rb.add_widget(JPLabel(text=tr("MP_TITLE"), bold=True,
                              font_size=sp(22), size_hint_y=None,
                              height=dp(34)))
        hint = JPLabel(text=tr("MP_CONNECT_HINT"), color=rgba(theme.MUTED),
                       font_size=sp(12), halign="center", size_hint_y=None)
        hint.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
                  texture_size=lambda w, ts: setattr(w, "height",
                                                     ts[1] + dp(6)))
        rb.add_widget(hint)

        rb.add_widget(SectionLabel(text=tr("MP_NAME")))
        self.in_name = _input(self._app.state.setting("mp_name", ""))
        rb.add_widget(self.in_name)

        rb.add_widget(SectionLabel(text=tr("MP_TURNS")))
        self._turns_each = int(self._app.state.setting("mp_turns", "10")
                               or 10)
        rb.add_widget(ChipRow([(n, str(n)) for n in TURNS_CHOICES],
                              self._turns_each,
                              on_change=self._set_turns))
        host = ThemedButton(text=tr("MP_HOST"), fill=theme.ACCENT,
                            bold=True, height=dp(50))
        host.bind(on_release=lambda *_: self._host())
        rb.add_widget(host)

        rb.add_widget(SectionLabel(text=tr("MP_CODE")))
        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(46))
        self.in_code = _input("", hint=tr("MP_CODE_PH"))
        join = ThemedButton(text=tr("MP_JOIN"), fill=theme.SUCCESS,
                            size_hint_x=None, width=dp(120), height=dp(46))
        join.bind(on_release=lambda *_: self._join())
        row.add_widget(self.in_code)
        row.add_widget(join)
        rb.add_widget(row)

        self._status_lbl = _status_label()
        rb.add_widget(self._status_lbl)
        rb.add_widget(Widget())
        back = ThemedButton(text=tr("BTN_MENU"), height=dp(46))
        back.bind(on_release=lambda *_: self.leave())
        rb.add_widget(back)
        self._sync_status()

    def _set_turns(self, n) -> None:
        self._turns_each = int(n)
        self._app.state.set_setting("mp_turns", str(n))

    def _my_name(self) -> str:
        name = (self.in_name.text if getattr(self, "in_name", None)
                else "").strip() or "player"
        self._app.state.set_setting("mp_name", name)
        self._app.maybe_go_online()
        return name

    def _make_client(self):
        client = RoomClient(transport=_make_transport())
        err = client.connect(self._my_name(), self._app.state.friend_code)
        if err:
            self.status = tr("MP_ERR_CONNECT", err=err)
            self._sync_status()
            return None
        self.client = client
        self.status = tr("MP_CONNECTING")
        self._sync_status()
        return client

    def _host(self) -> None:
        client = self._make_client()
        if client is None:
            return
        client.send({"t": "create",
                     "settings": {"turns_each": self._turns_each}})

    def _join(self, code: str = "") -> None:
        code = (code or (self.in_code.text if getattr(self, "in_code", None)
                         else "")).strip().upper()
        if not code:
            self.status = tr("MP_ERR_CODE")
            self._sync_status()
            return
        client = self._make_client()
        if client is None:
            return
        client.send({"t": "join", "room": code})

    # ------------------------------------------------------------------ #
    # Stage: lobby
    # ------------------------------------------------------------------ #
    def _settings(self) -> dict:
        from kanjire.net.server import DEFAULT_SETTINGS
        if self.state and self.state.get("settings"):
            return self.state["settings"]
        return dict(DEFAULT_SETTINGS)

    def _build_lobby(self) -> None:
        self.root_box.clear_widgets()
        rb = self.root_box
        rb.add_widget(JPLabel(text=tr("MP_LOBBY"), bold=True,
                              font_size=sp(20), size_hint_y=None,
                              height=dp(30)))
        self._code_lbl = JPLabel(text=self.room, bold=True, font_size=sp(34),
                                 color=rgba(theme.GOLD), size_hint_y=None,
                                 height=dp(46))
        rb.add_widget(self._code_lbl)

        scroller = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self._lobby_body = GridLayout(cols=1, spacing=dp(6),
                                      size_hint_y=None,
                                      padding=[0, 0, 0, dp(8)])
        self._lobby_body.bind(
            minimum_height=self._lobby_body.setter("height"))
        scroller.add_widget(self._lobby_body)
        rb.add_widget(scroller)

        self._status_lbl = _status_label()
        rb.add_widget(self._status_lbl)

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(50))
        leave = ThemedButton(text=tr("BTN_MENU"), height=dp(50))
        leave.bind(on_release=lambda *_: self.leave())
        row.add_widget(leave)
        self._start_btn = ThemedButton(text=tr("MP_START"),
                                       fill=theme.ACCENT, bold=True,
                                       height=dp(50))
        self._start_btn.bind(on_release=lambda *_: self._start())
        row.add_widget(self._start_btn)
        rb.add_widget(row)
        self._sync_lobby(self.state)

    def _sync_lobby(self, state: dict) -> None:
        if getattr(self, "_lobby_body", None) is None:
            return
        if getattr(self, "_code_lbl", None) is not None:
            self._code_lbl.text = self.room
        body = self._lobby_body
        body.clear_widgets()
        is_host = self.me == 0
        s = self._settings()

        # roster
        players = state.get("players") or []
        connected = state.get("connected") or []
        for i, name in enumerate(players):
            gone = i < len(connected) and not connected[i]
            text = name + (tr("MP_YOU") if i == self.me else "")
            if gone:
                text += f"  {tr('MP_GONE')}"
            lbl = JPLabel(text=text, halign="left", valign="middle",
                          font_size=sp(15),
                          color=rgba(theme.DIM if gone else theme.TEXT),
                          size_hint_y=None, height=dp(30))
            lbl.bind(size=lbl.setter("text_size"))
            body.add_widget(lbl)
        if len(players) < 2:
            body.add_widget(_muted(tr("MP_LOBBY_HINT")))

        hint = tr("MP_HOST_HINT") if is_host else tr("MP_GUEST_HINT")
        body.add_widget(_muted(hint))

        # settings (host: editable chips; guests: disabled)
        def chips(options, selected, key, *, multi=False):
            row = ChipRow(options, selected, multi=multi,
                          on_change=lambda v, k=key: self._set_setting(k, v))
            row.disabled = not is_host
            return row

        body.add_widget(SectionLabel(text=tr("SEC_LEVEL")))
        body.add_widget(chips([(lv, f"N{lv}") for lv in LEVEL_CHOICES],
                              [lv for lv in (s.get("levels") or [5])],
                              "levels", multi=True))
        body.add_widget(SectionLabel(text=tr("SEC_WORDS")))
        body.add_widget(chips([(n, str(n)) for n in BOARD_CHOICES],
                              int(s.get("board_size", 6)), "board_size"))
        body.add_widget(SectionLabel(text=tr("SEC_CARDS")))
        from kanjire.game.menuconfig import FACE_OPTIONS, FACE_ORDER
        from kanjire.kivyui.widgets import ChipGrid
        faces_now = s.get("faces_sel") or FACES_FOR.get(
            int(s.get("cards", 4)), FACES_FOR[4])
        fgrid = ChipGrid(
            [(f, tr(k)) for f, k in FACE_OPTIONS],
            list(faces_now), cols=2, multi=True, min_selected=2,
            colors={f: theme.FACE_COLORS[f] for f, _ in FACE_OPTIONS},
            on_change=lambda v: self._set_setting(
                "faces_sel", [f for f in FACE_ORDER if f in v]))
        fgrid.disabled = not is_host
        body.add_widget(fgrid)
        body.add_widget(SectionLabel(text=tr("MP_TURNS")))
        body.add_widget(chips([(n, str(n)) for n in TURNS_CHOICES],
                              int(s.get("turns_each", 10)), "turns_each"))
        body.add_widget(SectionLabel(text=tr("SEC_PASSES")))
        body.add_widget(chips([(n, f"×{n}") for n in (1, 2, 3, 5)],
                              int(s.get("passes", 1)), "passes"))
        body.add_widget(SectionLabel(text=tr("SEC_WRITING")))
        body.add_widget(chips(
            [("off", tr("WRITE_HORIZ")), ("random", tr("WRITE_MIX")),
             ("all", tr("WRITE_VERT"))],
            s.get("writing", "off"), "writing"))
        body.add_widget(SectionLabel(text=tr("SEC_FONTS")))
        body.add_widget(chips(
            [("fixed", tr("FONT_SINGLE")), ("random", tr("FONT_RANDOM"))],
            s.get("fonts", "fixed"), "fonts"))

        # invite friends (host with a room code)
        if is_host and self.room:
            online = [f for f in self._app.friends.friends()
                      if f.get("status") not in (None, "offline")]
            if online:
                body.add_widget(SectionLabel(text=tr("FR_TITLE")))
                for f in online:
                    row = BoxLayout(orientation="horizontal", spacing=dp(8),
                                    size_hint_y=None, height=dp(40))
                    lbl = JPLabel(text=f["name"], halign="left",
                                  valign="middle", font_size=sp(14))
                    lbl.bind(size=lbl.setter("text_size"))
                    b = ThemedButton(text=tr("FR_INVITE"),
                                     fill=theme.SUCCESS, size_hint_x=None,
                                     width=dp(90), height=dp(34),
                                     font_size=sp(12.5))
                    b.bind(on_release=lambda w, c=f["code"]:
                           self._app.friends.invite(c, self.room))
                    row.add_widget(lbl)
                    row.add_widget(b)
                    body.add_widget(row)

        if getattr(self, "_start_btn", None) is not None:
            self._start_btn.disabled = not is_host

    def _set_setting(self, key, value) -> None:
        if self.client is None or self.me != 0:
            return
        s = dict(self._settings())
        s[key] = value
        self.client.send({"t": "config", "settings": s})

    def _sample_pool(self, settings: dict) -> list[dict]:
        rng = random.Random()
        deck = settings.get("deck") or "jlpt"
        levels = settings.get("levels") or [5]
        try:
            words = db.load_words(self._app.con, decks=[deck],
                                  levels=levels if deck == "jlpt" else None,
                                  require_kanji=True)
        except Exception:
            words = []
        picked = weighted_sample_words(words, POOL_SIZE, bias=0.4, rng=rng,
                                       confusable=False)
        loc = self._app.state.locale
        return [{
            "kanji": w.expression,
            "reading": w.reading,
            "romaji": hira_to_romaji(w.reading),
            "meaning": w.get_meaning(loc),
        } for w in picked]

    def _start(self) -> None:
        if self.client is None or self.me != 0:
            return
        s = self._settings()
        pool = self._sample_pool(s)
        if len(pool) < 2:
            self.status = tr("MP_ERR_POOL")
            self._sync_status()
            return
        faces = list(s.get("faces_sel")
                     or FACES_FOR.get(int(s.get("cards", 4)), FACES_FOR[4]))
        self.client.send({
            "t": "start", "pool": pool,
            "faces": faces,
            "board_size": int(s.get("board_size", 6)),
            "turns_each": int(s.get("turns_each", 10)),
        })

    # ------------------------------------------------------------------ #
    # Stage: play
    # ------------------------------------------------------------------ #
    def _build_play(self) -> None:
        self.root_box.clear_widgets()
        self.cards = {}
        self._board_sig = ()
        rb = self.root_box
        self._hud_scores = JPLabel(text="", font_size=sp(14), halign="left",
                                   valign="middle", size_hint_y=None,
                                   height=dp(26))
        self._hud_scores.bind(size=self._hud_scores.setter("text_size"))
        self._hud_turn = JPLabel(text="", bold=True, font_size=sp(15),
                                 halign="left", valign="middle",
                                 size_hint_y=None, height=dp(26))
        self._hud_turn.bind(size=self._hud_turn.setter("text_size"))
        top = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(30), spacing=dp(8))
        back = ThemedButton(text="←", size_hint=(None, None),
                            size=(dp(40), dp(30)), font_size=sp(15))
        back.bind(on_release=lambda *_: self.leave())
        top.add_widget(back)
        top.add_widget(self._hud_scores)
        self._pause_btn = ThemedButton(text=tr("MP_PAUSE"),
                                       size_hint=(None, None),
                                       size=(dp(86), dp(30)),
                                       font_size=sp(12))
        self._pause_btn.bind(on_release=lambda *_: self._pause())
        top.add_widget(self._pause_btn)
        rb.add_widget(top)
        rb.add_widget(self._hud_turn)
        self.board = FloatLayout()
        self.board.bind(size=lambda *_: self._layout_cards())
        rb.add_widget(self.board)
        self._sync_board(self.state)
        self._refresh_hud()

    def _pause(self) -> None:
        if self.client is None or self.me != 0:
            return
        paused = bool(self.state.get("paused"))
        self.client.send({"t": "resume" if paused else "pause"})

    def _sync_board(self, state: dict) -> None:
        if getattr(self, "board", None) is None or self.stage != "play":
            return
        # With passes > 1 the server sends None for a cleared group's slots:
        # honest gaps that shuffle along with the cards.
        board = state.get("board") or []
        sig = tuple((c["id"] if c else None) for c in board)
        if sig != self._board_sig:
            self.board.clear_widgets()
            self.cards = {}
            self._board_sig = sig
            self._pointer_shown = None
            for i, d in enumerate(board):
                if d is None:
                    continue
                w = _MPCardWidget(_MPCard(d), self, size_hint=(None, None))
                self.cards[d["id"]] = w
                self.board.add_widget(w)
                w.pop_in(min(0.03 * i, 0.4))
            self._layout_cards()
        else:
            for d in board:
                if d is None:
                    continue
                w = self.cards.get(d["id"])
                if w is not None:
                    w.card.matched = bool(d.get("matched"))
                    w.card.selected = bool(d.get("selected"))
                    w.refresh_state()
        self._show_pointer(state.get("pointer"))

    def _show_pointer(self, pointer) -> None:
        if pointer == self._pointer_shown:
            return
        old = self.cards.get(self._pointer_shown)
        if old is not None:
            old.set_pointed(False)
        new = self.cards.get(pointer)
        if new is not None:
            new.set_pointed(True)
        self._pointer_shown = pointer

    def _layout_cards(self) -> None:
        # Grid positions come from the slot list (blanks included in passes
        # mode), so a gap keeps holding its place on every screen.
        if not self.cards:
            return
        n = len(self._board_sig) or len(self.cards)
        gap = dp(8)
        aw, ah = self.board.width, self.board.height
        if aw < 50 or ah < 50:
            return
        cols, rows, cw, ch = choose_grid(n, aw, ah, gap=gap)
        for i, cid in enumerate(self._board_sig or list(self.cards)):
            w = self.cards.get(cid) if cid is not None else None
            if w is None:
                continue
            cx, cy = slot_center(i, cols, rows, cw, ch, self.board.x,
                                 self.board.y, aw, ah, gap=gap, count=n)
            w.size = (cw, ch)
            w.center = (cx, cy)
            w._sync()

    def _refresh_hud(self) -> None:
        st = self.state or {}
        if getattr(self, "_hud_scores", None) is None or self.stage != "play":
            return
        players = st.get("players") or []
        scores = st.get("scores") or []
        bits = []
        for i, name in enumerate(players):
            sc = scores[i] if i < len(scores) else 0
            # ★ marks whose turn it is (▶ has no glyph in the bundled fonts).
            mark = "★" if st.get("turn") == i else ""
            bits.append(f"{mark}{name} {sc}")
        self._hud_scores.text = "   ".join(bits)
        left_txt = tr("MP_TURNS_LEFT", n=st.get("turns_left", 0))
        if int(st.get("passes") or 1) > 1:
            left_txt = (tr("MP_PASS", cur=int(st.get("pass_no") or 1),
                           total=int(st.get("passes")))
                        + "  ·  " + left_txt)
        if st.get("paused"):
            self._hud_turn.text = tr("MP_PAUSED")
            self._hud_turn.color = rgba(theme.GOLD)
        elif st.get("turn") == self.me:
            self._hud_turn.text = tr("MP_YOUR_TURN") + "  ·  " + left_txt
            self._hud_turn.color = rgba(theme.SUCCESS)
        else:
            turn = st.get("turn")
            name = (players[turn] if isinstance(turn, int)
                    and turn < len(players) else "?")
            self._hud_turn.text = (tr("MP_THEIR_TURN", name=name)
                                   + "  ·  " + left_txt)
            self._hud_turn.color = rgba(theme.MUTED)
        if getattr(self, "_pause_btn", None) is not None:
            self._pause_btn.disabled = self.me != 0
            self._pause_btn.text = (tr("MP_RESUME") if st.get("paused")
                                    else tr("MP_PAUSE"))

    # touch → engine
    def card_tapped(self, card_id: int) -> None:
        st = self.state or {}
        if (self.client is None or st.get("turn") != self.me
                or st.get("paused") or st.get("revealing")):
            return
        self.client.send({"t": "select", "card": card_id})

    def card_held(self, card_id) -> None:
        """Long-press: point at a card so everyone sees what you're eyeing."""
        st = self.state or {}
        if (self.client is None or st.get("turn") != self.me
                or st.get("paused") or st.get("revealing")):
            return
        if card_id != self._pointed:
            self._pointed = card_id
            self.client.send({"t": "point", "card": card_id})

    # ------------------------------------------------------------------ #
    # Stage: done
    # ------------------------------------------------------------------ #
    def _build_done(self) -> None:
        self.root_box.clear_widgets()
        rb = self.root_box
        rb.add_widget(JPLabel(text=tr("MP_DONE"), bold=True,
                              font_size=sp(22), size_hint_y=None,
                              height=dp(36)))
        self._done_body = GridLayout(cols=1, spacing=dp(6),
                                     size_hint_y=None)
        self._done_body.bind(
            minimum_height=self._done_body.setter("height"))
        rb.add_widget(self._done_body)
        rb.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(50))
        leave = ThemedButton(text=tr("BTN_MENU"))
        leave.bind(on_release=lambda *_: self.leave())
        row.add_widget(leave)
        self._replay_btn = ThemedButton(text=tr("MP_REPLAY"),
                                        fill=theme.ACCENT, bold=True)
        self._replay_btn.bind(on_release=lambda *_: self._replay())
        row.add_widget(self._replay_btn)
        rb.add_widget(row)
        self._sync_done(self.state)

    def _sync_done(self, state: dict) -> None:
        if getattr(self, "_done_body", None) is None or self.stage != "done":
            return
        body = self._done_body
        body.clear_widgets()
        players = state.get("players") or []
        scores = state.get("scores") or []
        ranked = sorted(range(len(players)),
                        key=lambda i: -(scores[i] if i < len(scores) else 0))
        for rank, i in enumerate(ranked):
            sc = scores[i] if i < len(scores) else 0
            row = Panel(orientation="horizontal", size_hint_y=None,
                        height=dp(46), padding=[dp(12), dp(6)])
            medal = ("金", "銀", "銅")[rank] if rank < 3 else " "
            lbl = JPLabel(text=f"{medal}  {players[i]}"
                          + (tr("MP_YOU") if i == self.me else ""),
                          halign="left", valign="middle", font_size=sp(15))
            lbl.bind(size=lbl.setter("text_size"))
            val = JPLabel(text=f"{sc:,}", halign="right", valign="middle",
                          bold=True, font_size=sp(16),
                          color=rgba(theme.GOLD if rank == 0
                                     else theme.TEXT))
            val.bind(size=val.setter("text_size"))
            row.add_widget(lbl)
            row.add_widget(val)
            body.add_widget(row)
        if getattr(self, "_replay_btn", None) is not None:
            self._replay_btn.disabled = self.me != 0

    def _replay(self) -> None:
        if self.client is not None and self.me == 0:
            self.client.send({"t": "lobby"})


class _MPCardWidget(CardWidget):
    """Board card driven by server state; adds long-press-to-point."""

    def __init__(self, card, screen: MultiplayerScreen, **kw):
        super().__init__(card, **kw)
        self._screen = screen
        self._hold_ev = None
        self._held = False
        self.bind(on_release=lambda w: self._tapped())

    def _tapped(self) -> None:
        if not self._held:
            self._screen.card_tapped(self.card.id)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._held = False
            self._hold_ev = Clock.schedule_once(self._fire_hold, POINT_HOLD)
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self._hold_ev is not None:
            self._hold_ev.cancel()
            self._hold_ev = None
        return super().on_touch_up(touch)

    def _fire_hold(self, *_):
        self._held = True
        self._screen.card_held(self.card.id)

    def refresh_state(self) -> None:
        super().refresh_state()
        if self.card.matched:
            # Held on the board during the reveal window: glow success.
            self._bg_col.rgba = rgba(theme.SUCCESS, 0.45)
            self._border_col.rgba = rgba(theme.SUCCESS)

    def celebrate_hold(self) -> None:
        """Reveal pulse — the cards stay up (server clears them later)."""
        self._bg_col.rgba = rgba(theme.SUCCESS, 0.6)
        self._border_col.rgba = rgba(theme.SUCCESS)

    def set_pointed(self, pointed: bool) -> None:
        if pointed:
            self._border_col.rgba = rgba(theme.GOLD)
            self._bg_col.rgba = rgba(theme.tint(theme.PANEL, 0.10))
        else:
            self.refresh_state()


def _input(text: str = "", hint: str = "") -> TextInput:
    return TextInput(text=text, hint_text=hint, multiline=False,
                     font_name=UI_FONT, font_size=sp(16), size_hint_y=None,
                     height=dp(46), background_color=rgba(theme.PANEL),
                     foreground_color=rgba(theme.TEXT),
                     hint_text_color=rgba(theme.DIM),
                     cursor_color=rgba(theme.ACCENT),
                     padding=[dp(10), dp(12)])


def _status_label() -> JPLabel:
    lbl = JPLabel(text="", color=rgba(theme.GOLD), font_size=sp(13),
                  halign="center", size_hint_y=None, height=dp(22))
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


def _muted(text: str) -> JPLabel:
    lbl = JPLabel(text=text, color=rgba(theme.DIM), font_size=sp(12),
                  halign="left", size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(6)))
    return lbl
