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
from kanjire.ui.scenes.menu import WRITING_OPTIONS, _deck_label
from kanjire.model.sampling import weighted_sample_words
from kanjire.net.client import NetClient
from kanjire.net.room_client import RoomClient
from kanjire.net.server import DEFAULT_PORT, start_in_thread
from kanjire.ui import theme
from kanjire.ui.anim import Animator, ease_out_back, ease_out_cubic, ease_out_elastic
from kanjire.ui.fonts import JP_FONT, JP_FONTS
from kanjire.ui.gfx import fill_quad
from kanjire.ui.layout import choose_grid, slot_center
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.card import CardView
from kanjire.ui.widgets.textinput import TextInput

HUD_H = 110
POOL_SIZE = 140
TURNS_CHOICES = (5, 10, 15)
BOARD_CHOICES = (4, 6, 8)
CARDS_CHOICES = (2, 3, 4)
LEVEL_CHOICES = (5, 4, 3, 2, 1)
#: cards-per-word -> the faces each word is split into (4 adds romaji).
FACES_FOR = {
    2: ("kanji", "meaning"),
    3: ("kanji", "reading", "meaning"),
    4: ("kanji", "reading", "romaji", "meaning"),
}


class _MPCard:
    """Duck-typed stand-in for the engine Card that CardView renders."""

    def __init__(self, d: dict) -> None:
        self.id = d["id"]
        self.group = d["group"]
        self.face = d["face"]
        self.text = d["text"]
        # True while a completed group is held on the board for everyone to see.
        self.matched = bool(d.get("matched"))
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

        # ---- lobby settings: the host edits, everyone watches live ---- #
        def srow(label_key):
            lb = lbl(11, theme.MUTED, bold=True, anchor_x="right")
            lb.text = tr(label_key)
            self.labels.append(lb)
            return lb

        self.lbl_s_deck = srow("SEC_DECK")
        self.lbl_s_level = srow("SEC_LEVEL")
        self.lbl_s_words = srow("SEC_WORDS")
        self.lbl_s_cards = srow("SEC_CARDS")
        self.lbl_s_turns = srow("MP_TURNS")
        self.lbl_s_writing = srow("SEC_WRITING")
        self.lbl_s_fonts = srow("SEC_FONTS")
        self.settings_labels = [self.lbl_s_deck, self.lbl_s_level,
                                self.lbl_s_words, self.lbl_s_cards,
                                self.lbl_s_turns, self.lbl_s_writing,
                                self.lbl_s_fonts]

        decks = []
        try:
            for r in db.list_decks(app.con):
                if r["name"] == "jlpt" or r["name"].startswith("corpus:"):
                    decks.append(r["name"])
        except Exception:
            decks = ["jlpt"]
        self.deck_btns = [
            (d, Button(_deck_label(d), lambda d=d: self._set_setting("deck", d),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.ACCENT, font_size=11))
            for d in (decks or ["jlpt"])[:4]
        ]
        self.level_btns = [
            (lv, Button(f"N{lv}", lambda lv=lv: self._toggle_level(lv),
                        self.batch, self.g_bg, self.g_text,
                        accent=theme.GOLD, font_size=11))
            for lv in LEVEL_CHOICES
        ]
        self.words_btns = [
            (n, Button(str(n), lambda n=n: self._set_setting("board_size", n),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.SUCCESS, font_size=11))
            for n in BOARD_CHOICES
        ]
        # Same labels as the single-player Advanced tab, so "cards per word"
        # reads identically in both places.
        _CARD_LABELS = {2: "FACES_TWO", 3: "FACES_THREE", 4: "FACES_FOUR"}
        self.cards_btns = [
            (n, Button(tr(_CARD_LABELS[n]),
                       lambda n=n: self._set_setting("cards", n),
                       self.batch, self.g_bg, self.g_text,
                       accent=(theme.FACE_COLORS["romaji"] if n == 4
                               else theme.FACE_COLORS["meaning"]),
                       font_size=10))
            for n in CARDS_CHOICES
        ]
        self.lturns_btns = [
            (n, Button(str(n), lambda n=n: self._set_setting("turns_each", n),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.GOLD, font_size=11))
            for n in TURNS_CHOICES
        ]
        # Presentation, with the same options (and the same words) as the
        # single-player Advanced tab.
        self.writing_btns = [
            (v, Button(tr(key), lambda v=v: self._set_setting("writing", v),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.ACCENT, font_size=11))
            for v, key in WRITING_OPTIONS
        ]
        self.fonts_btns = [
            (v, Button(tr(key), lambda v=v: self._set_setting("fonts", v),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.ACCENT, font_size=11))
            for v, key in (("fixed", "FONT_SINGLE"), ("random", "FONT_RANDOM"))
        ]
        self.setting_btns = (self.deck_btns + self.level_btns
                             + self.words_btns + self.cards_btns
                             + self.lturns_btns + self.writing_btns
                             + self.fonts_btns)

        # ---- in-game host controls ---- #
        self.pause_btn = Button("", self._pause, self.batch, self.g_bg,
                                self.g_text, accent=theme.GOLD, font_size=12)
        self.lobby_btn = Button(tr("MP_TO_LOBBY"), self._to_lobby, self.batch,
                                self.g_bg, self.g_text, accent=theme.DANGER,
                                font_size=12)
        # Results screen: run it back with the same settings, same players.
        self.replay_btn = Button(tr("MP_REPLAY"), self._replay, self.batch,
                                 self.g_bg, self.g_text, accent=theme.SUCCESS,
                                 font_size=15)

        self.buttons = ([self.host_btn, self.join_btn, self.start_btn,
                         self.back_btn, self.pause_btn, self.lobby_btn,
                         self.replay_btn]
                        + [b for _n, b in self.turns_btns]
                        + [b for _v, b in self.setting_btns])
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
        paused = bool((self.state or {}).get("paused"))
        vis = {
            self.host_btn: ph == "connect",
            self.join_btn: ph == "connect",
            self.start_btn: ph == "lobby" and self.me == 0,
            self.back_btn: True,
            # Host controls during play: pause/resume, and bail to the lobby
            # (where the settings are) - offered while paused.
            self.pause_btn: ph == "play" and self.me == 0,
            self.lobby_btn: (self.me == 0
                             and (ph == "done" or (ph == "play" and paused))),
            self.replay_btn: ph == "done" and self.me == 0,
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
        # Settings rows: visible to EVERYONE in the lobby (so players can see
        # what they're about to play), but only the host can click them.
        for _v, b in self.setting_btns:
            b.set_visible(ph == "lobby")
            # set_enabled (not `.enabled = ...`) so the colours repaint: a
            # guest's read-only buttons must still show what's selected.
            b.set_enabled(ph == "lobby" and self.me == 0)
            if ph != "lobby":
                b.set_rect(-4000, -4000, 1, 1)
        for lb in self.settings_labels:
            lb.opacity = 255 if ph == "lobby" else 0
            if ph != "lobby":
                lb.x = lb.y = -4000
        self.lbl_name.text = tr("MP_NAME") if ph == "connect" else ""
        self.lbl_addr.text = tr("MP_ADDR") if ph == "connect" else ""
        self.lbl_code.text = tr("MP_CODE") if ph == "connect" else ""
        self.lbl_turns.text = tr("MP_TURNS") if ph == "connect" else ""
        self.big_code.text = self.room if ph == "lobby" else ""
        if ph == "lobby":
            self.hint.text = (tr("MP_HOST_HINT") if self.me == 0
                              else tr("MP_GUEST_HINT"))
        elif ph == "play":
            self.hint.text = tr("MP_PLAY_HINT")
        else:
            self.hint.text = ""
        self.pause_btn.set_text(tr("MP_RESUME") if paused else tr("MP_PAUSE"))
        self._refresh_settings()
        if ph != "play":
            self._clear_cards()
        if ph != "lobby":
            for lbl in self.player_lbls:
                lbl.text = ""
        self.on_resize(self.width, self.height)

    def _refresh_settings(self) -> None:
        """Mirror the room's live settings onto the buttons - this is what
        makes the host's changes visible to every player as they happen."""
        s = self._settings()
        is_jlpt = (s.get("deck") or "jlpt") == "jlpt"
        for d, b in self.deck_btns:
            b.set_selected(d == s.get("deck"))
        for lv, b in self.level_btns:
            b.set_selected(is_jlpt and lv in (s.get("levels") or []))
            b.set_enabled(self.phase == "lobby" and self.me == 0 and is_jlpt)
        for n, b in self.words_btns:
            b.set_selected(n == s.get("board_size"))
        for n, b in self.cards_btns:
            b.set_selected(n == s.get("cards"))
        for n, b in self.lturns_btns:
            b.set_selected(n == s.get("turns_each"))
        for v, b in self.writing_btns:
            b.set_selected(v == (s.get("writing") or "off"))
        for v, b in self.fonts_btns:
            b.set_selected(v == (s.get("fonts") or "fixed"))
        self.lbl_s_level.opacity = (255 if (self.phase == "lobby" and is_jlpt)
                                    else 0)

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

    def _settings(self) -> dict:
        """The room's live settings (server-authoritative once created)."""
        from kanjire.net.server import DEFAULT_SETTINGS
        if self.state and self.state.get("settings"):
            return self.state["settings"]
        return dict(DEFAULT_SETTINGS)

    def _sample_pool(self, settings: dict) -> list[dict]:
        """The host contributes the room's words, drawn from the settings it
        just chose (the server itself stays data-free)."""
        from kanjire.kana import hira_to_romaji
        rng = random.Random()
        deck = settings.get("deck") or "jlpt"
        levels = settings.get("levels") or [5]
        try:
            words = db.load_words(self.app.con, decks=[deck],
                                  levels=levels if deck == "jlpt" else None,
                                  require_kanji=True)
        except Exception:
            words = []
        picked = weighted_sample_words(words, POOL_SIZE, bias=0.4, rng=rng,
                                       confusable=False)
        loc = self.app.state.locale
        out = []
        for w in picked:
            out.append({
                "kanji": w.expression,
                "reading": w.reading,
                "romaji": hira_to_romaji(w.reading),
                "meaning": w.get_meaning(loc),
            })
        return out

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
        # Settings are tuned in the lobby (where everyone can watch); the
        # word pool is sampled from them at Start.
        client.send({"t": "create",
                     "settings": {"turns_each": self.turns_each}})

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
        """Host: sample a fresh pool from the CURRENT settings and go."""
        if self.client is None or self.me != 0:
            return
        s = self._settings()
        pool = self._sample_pool(s)
        if len(pool) < 2:
            self.status = tr("MP_ERR_POOL")
            return
        self.client.send({
            "t": "start", "pool": pool,
            "faces": list(FACES_FOR.get(int(s.get("cards", 3)), FACES_FOR[3])),
            "board_size": int(s.get("board_size", 6)),
            "turns_each": int(s.get("turns_each", 10)),
        })

    def _set_setting(self, key: str, value) -> None:
        """Host-only: push one setting change; everyone sees it immediately."""
        if self.client is None or self.me != 0:
            return
        self.client.send({"t": "config", "settings": {key: value}})

    def _toggle_level(self, lv: int) -> None:
        levels = list(self._settings().get("levels") or [5])
        if lv in levels:
            if len(levels) > 1:
                levels.remove(lv)
        else:
            levels.append(lv)
        self._set_setting("levels", sorted(set(levels)))

    def _pause(self) -> None:
        if self.client is not None and self.me == 0:
            paused = bool((self.state or {}).get("paused"))
            self.client.send({"t": "resume" if paused else "pause"})

    def _to_lobby(self) -> None:
        if self.client is not None and self.me == 0:
            self.client.send({"t": "lobby"})

    def _replay(self) -> None:
        """Host, on the results screen: same players, same settings, new words.

        Goes through the lobby first because that's what resets the finished
        game and the scores; then immediately starts a freshly-sampled round.
        """
        if self.client is None or self.me != 0:
            return
        self.client.send({"t": "lobby"})
        self._start()

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
        # Heartbeat: proves we're still here, and drops anyone who isn't.
        self.client.tick()
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
        # Board first, THEN the event: _sync_board assigns glow/scale for newly
        # selected cards, so running it afterwards would overwrite the
        # completed group's reveal animation with a plain selection glow.
        if self.phase == "play":
            self._sync_board(state)
        if event:
            self._on_event(event)
        # Settings / pause state can change without a phase change (the host
        # tweaking the lobby, or pausing mid-game), so always re-apply.
        self._apply_phase()
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
            # The server holds this group on the board for REVEAL_SECONDS so
            # everyone can see what went together - make it unmissable while
            # it's up, instead of the cards just sitting there.
            for cid in event.get("cards") or []:
                cv = self.cards.get(cid)
                if cv is None:
                    continue
                cv.glow = 1.0
                cv.scale = 1.16
                self.anim.to(cv, "scale", 1.0, 0.45, ease=ease_out_back)
                self.anim.to(cv, "glow", 0.75, 0.5, ease=ease_out_cubic)
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

    def _card_style(self, d: dict) -> tuple[str | None, str]:
        """(font, direction) for one card - identical on every client.

        The single-player scene rolls these with a plain ``random``, which would
        give each player a *different-looking* board of the same cards. Seeding
        off the room code + card id keeps everyone's screens in sync while still
        looking shuffled.
        """
        s = self._settings()
        face = d.get("face")
        if face in ("meaning", "romaji"):
            return JP_FONT, "horizontal"
        rng = random.Random(f"{self.room}:{d.get('id')}")
        font = JP_FONT
        if s.get("fonts") == "random" and JP_FONTS:
            font = rng.choice(JP_FONTS)
        writing = s.get("writing") or "off"
        if writing == "all":
            direction = "vertical"
        elif writing == "random":
            direction = "vertical" if rng.random() < 0.5 else "horizontal"
        else:
            direction = "horizontal"
        return font, direction

    def _sync_board(self, state: dict) -> None:
        board = state.get("board") or []
        sig = tuple(c["id"] for c in board)
        if sig != self._board_sig:
            self._clear_cards()
            self._board_sig = sig
            for d in board:
                font, direction = self._card_style(d)
                self.cards[d["id"]] = CardView(
                    _MPCard(d), self.batch, self.g_glow, self.g_bg,
                    self.g_text, font_name=font, direction=direction)
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
                    # A revealed group keeps the same card ids, so this branch
                    # (not the rebuild above) is what learns it was matched.
                    cv.model.matched = bool(d.get("matched"))
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
        area_x, area_y = 40, 46          # clear of the bottom hint line
        area_w = self.width - 80
        area_h = self.height - hud - 76
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
            if st.get("paused"):
                self.turn_lbl.text = tr("MP_PAUSED")
                self.turn_lbl.color = theme.with_alpha(theme.DANGER, 255)
            else:
                self.turn_lbl.color = theme.with_alpha(theme.GOLD, 255)
                self.turn_lbl.text = (tr("MP_YOUR_TURN") if mine else
                                      tr("MP_THEIR_TURN",
                                         name=players[turn] if 0 <= turn < len(players) else "?"))
            # Trust the server's count: a player who left takes their unplayed
            # turns with them, so total-minus-used overstates what's left.
            left = st.get("turns_left")
            if left is None:
                left = max(0, (st.get("turns_total") or 0)
                           - (st.get("turns_used") or 0))
            self.turns_left_lbl.text = tr("MP_TURNS_LEFT", n=int(left))
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
                and not self.state.get("paused")
                # A completed group is being shown: the board is frozen for
                # everyone (the server rejects clicks anyway - don't pretend).
                and not self.state.get("revealing")
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
                and not self.state.get("revealing") \
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
        # The player rows double as the in-game HUD and the final scoreboard;
        # on the results screen they're the headline, so blow them up.
        if self.phase == "done":
            for lbl in self.player_lbls:
                lbl.font_size = max(16, round(28 * s))
            self.title.font_size = max(22, round(34 * s))
        for b in self.buttons:
            b.set_scale(s)
        cx = width / 2
        self.title.x, self.title.y = cx, height - 46 * s
        self.subtitle.x, self.subtitle.y = cx, height - 76 * s
        self.status_lbl.x, self.status_lbl.y = cx, 90 * s
        self.back_btn.set_rect(16 * s, 16 * s, 120 * s, 26 * s)

        if self.phase == "connect":
            for w in self.inputs:
                w.set_scale(s)
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
            self.big_code.x, self.big_code.y = cx, height - 128 * s
            n_players = max(1, len((self.state or {}).get("players") or [1]))
            for i, lbl in enumerate(self.player_lbls):
                lbl.anchor_x = "center"
                lbl.x, lbl.y = cx, height - 178 * s - i * 26 * s

            # Settings block, under the player list. Label on the left of each
            # row, its choices to the right - same for host and guests, so
            # everyone reads the same thing while the host tweaks.
            rows = [
                (self.lbl_s_deck, self.deck_btns, 108 * s),
                (self.lbl_s_level, self.level_btns, 46 * s),
                (self.lbl_s_words, self.words_btns, 46 * s),
                (self.lbl_s_cards, self.cards_btns, 168 * s),
                (self.lbl_s_turns, self.lturns_btns, 46 * s),
                (self.lbl_s_writing, self.writing_btns, 64 * s),
                (self.lbl_s_fonts, self.fonts_btns, 78 * s),
            ]
            # Seven rows + the player list can outgrow a short window, so the
            # row pitch shrinks to fit rather than running Start off the bottom.
            top = height - 200 * s - n_players * 26 * s
            floor = 150 * s                     # leaves room for Start + hint
            pitch = min(40 * s, max(28 * s, (top - floor) / max(1, len(rows))))
            ry = top
            gap = 8 * s
            for lb, btns, bw in rows:
                total = len(btns) * bw + (len(btns) - 1) * gap
                x0 = cx - total / 2 + 60 * s
                lb.x, lb.y = x0 - 16 * s, ry
                for i, (_v, b) in enumerate(btns):
                    b.set_rect(x0 + i * (bw + gap), ry - 13 * s, bw, 26 * s)
                ry -= pitch

            self.start_btn.set_rect(cx - 130 * s, max(96 * s, ry - 40 * s),
                                    260 * s, 46 * s)
            self.hint.x, self.hint.y = cx, 52 * s
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
                self.turn_lbl.x, self.turn_lbl.y = cx, height - 78 * s
                self.turns_left_lbl.anchor_x = "right"
                self.turns_left_lbl.x = width - 24 * s
                self.turns_left_lbl.y = height - 36 * s
                # Host controls live INSIDE the HUD strip, never over the
                # board (they used to sit on top of the bottom card row).
                self.pause_btn.set_rect(width - 310 * s, height - 92 * s,
                                        140 * s, 28 * s)
                self.lobby_btn.set_rect(width - 160 * s, height - 92 * s,
                                        140 * s, 28 * s)
                self._layout_cards()
            else:
                # Results: the scores are the point of this screen, so they get
                # room to breathe (they used to be the same size as a HUD line).
                for i, lbl in enumerate(self.player_lbls):
                    lbl.anchor_x = "center"
                    lbl.x, lbl.y = cx, height - 200 * s - i * 56 * s
                # Replay (same players, same settings) sits next to the way out.
                if self.me == 0:
                    self.replay_btn.set_rect(cx - 220 * s, 90 * s, 210 * s, 44 * s)
                    self.lobby_btn.set_rect(cx + 10 * s, 90 * s, 210 * s, 44 * s)
                else:
                    self.lobby_btn.set_rect(cx - 105 * s, 90 * s, 210 * s, 44 * s)
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
