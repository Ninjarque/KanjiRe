"""The Kivy gameplay screen: board of cards, HUD, match/mismatch feedback.

Drives the shared :class:`kanjire.game.engine.GameEngine` through
:func:`kanjire.game.session.build_session` — all rules (Learn buckets,
Survival hearts/bounties, familiarize repetitions) come from the engine;
this module is presentation + touch only.
"""
from __future__ import annotations

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.widget import Widget

from kanjire.game.engine import Phase
from kanjire.game.session import build_session
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.textfit import fit_font_size
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, Panel, ThemedButton
from kanjire.ui.layout import choose_grid, slot_center

_GAP = 10  # dp between cards; tighter than desktop, phones are small


class CardWidget(ButtonBehavior, Widget):
    """One board card: rounded panel, face-coloured border, fitted text.

    *font_name*/*vertical* exist for Familiarize (random fonts, vertical
    writing). Vertical text is emulated by stacking the characters — Kivy
    has no native tategaki.
    """

    def __init__(self, card, font_name: str | None = None,
                 vertical: bool = False, **kw):
        super().__init__(**kw)
        self.card = card
        self._font = font_name or UI_FONT
        self._vertical = vertical and card.face in ("kanji", "reading")
        self._face_col = theme.FACE_COLORS.get(card.face, theme.ACCENT)
        self._sticker_text = ""
        self._sticker_col = theme.GOLD

        with self.canvas:
            self._bg_col = Color(*rgba(theme.PANEL))
            self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                        radius=[dp(10)])
            self._border_col = Color(*rgba(self._face_col, 0.55))
            self._border = Line(width=dp(1.4), rounded_rectangle=(0, 0, 1, 1, dp(10)))

        text = ("\n".join(card.text) if self._vertical else card.text)
        self._label = JPLabel(text=text, font_name=self._font,
                              color=rgba(theme.TEXT),
                              halign="center", valign="middle")
        self.add_widget(self._label)
        self._sticker = JPLabel(text="", font_size=sp(13), bold=True)
        self.add_widget(self._sticker)
        self.bind(pos=self._sync, size=self._sync)

    # ---- geometry ------------------------------------------------------ #
    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rounded_rectangle = (
            self.x + dp(1), self.y + dp(1),
            max(2, self.width - dp(2)), max(2, self.height - dp(2)), dp(10))
        pad = dp(8)
        w = max(10.0, self.width - 2 * pad)
        h = max(10.0, self.height - 2 * pad)
        wrap = self.card.face == "meaning"
        size = fit_font_size(self._label.text, w, h, font_name=self._font,
                             start=min(sp(30), h * 0.6),
                             wrap=wrap or self._vertical)
        self._label.font_size = size
        self._label.text_size = (w, None) if wrap else (None, None)
        self._label.size = (w, h)
        self._label.center = self.center
        self._sticker.size = (dp(30), dp(18))
        self._sticker.pos = (self.x + dp(6), self.top - dp(22))

    # ---- state visuals -------------------------------------------------- #
    def set_sticker(self, text: str, color) -> None:
        self._sticker.text = text
        self._sticker.color = rgba(color)

    def refresh_state(self) -> None:
        if self.card.selected:
            self._bg_col.rgba = rgba(theme.tint(theme.PANEL, 0.14))
            self._border_col.rgba = rgba(theme.ACCENT)
        else:
            self._bg_col.rgba = rgba(theme.PANEL)
            self._border_col.rgba = rgba(self._face_col, 0.55)

    def pop_in(self, delay: float) -> None:
        self.opacity = 0.0
        Animation.cancel_all(self, "opacity")
        anim = Animation(opacity=1.0, d=0.18, t="out_quad")
        Clock.schedule_once(lambda *_: anim.start(self), delay)

    def flash_error(self) -> None:
        self._bg_col.rgba = rgba(theme.DANGER, 0.55)
        x0 = self.x
        (Animation(x=x0 - dp(5), d=0.04) + Animation(x=x0 + dp(5), d=0.05)
         + Animation(x=x0, d=0.05)).start(self)
        Clock.schedule_once(lambda *_: self.refresh_state(), 0.28)

    def celebrate(self) -> None:
        self._bg_col.rgba = rgba(theme.SUCCESS, 0.6)
        self._border_col.rgba = rgba(theme.SUCCESS)
        Animation(opacity=0.0, d=0.3, t="out_quad").start(self)


class _FloatText(JPLabel):
    """Rising, fading score/combo popup."""

    def __init__(self, text, color, center, **kw):
        super().__init__(text=text, color=rgba(color), bold=True,
                         font_size=sp(18), size_hint=(None, None),
                         size=(dp(140), dp(30)), **kw)
        self.center = center
        anim = Animation(y=self.y + dp(46), opacity=0.0, d=0.8, t="out_quad")
        anim.bind(on_complete=lambda *_: self.parent and self.parent.remove_widget(self))
        Clock.schedule_once(lambda *_: anim.start(self), 0)


class GameScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.session = None
        self.engine = None
        self._cards: dict[int, CardWidget] = {}
        self._tick_ev = None
        self._overlay = None

        root = BoxLayout(orientation="vertical")
        self.hud = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=dp(54), padding=[dp(8), dp(4)],
                             spacing=dp(8))
        self._btn_back = ThemedButton(text="←", size_hint=(None, None),
                                      size=(dp(46), dp(46)), font_size=sp(20))
        self._btn_back.bind(on_release=lambda *_: self._quit())
        self._lbl_score = JPLabel(text="0", bold=True, font_size=sp(20),
                                  halign="left", valign="middle")
        self._lbl_score.bind(size=self._lbl_score.setter("text_size"))
        self._lbl_mid = JPLabel(text="", color=rgba(theme.MUTED),
                                font_size=sp(15), halign="center",
                                valign="middle")
        self._lbl_mid.bind(size=self._lbl_mid.setter("text_size"))
        self._lbl_right = JPLabel(text="", bold=True, font_size=sp(18),
                                  halign="right", valign="middle")
        self._lbl_right.bind(size=self._lbl_right.setter("text_size"))
        self.hud.add_widget(self._btn_back)
        self.hud.add_widget(self._lbl_score)
        self.hud.add_widget(self._lbl_mid)
        self.hud.add_widget(self._lbl_right)

        self.board = FloatLayout()
        self.board.bind(size=lambda *_: self._layout_cards())
        root.add_widget(self.hud)
        root.add_widget(self.board)
        self.add_widget(root)
        # Example-sentence strip: an OVERLAY pinned to the bottom, never a
        # layout sibling — resizing the board on every match re-flowed the
        # whole grid ("cards jump around when I match things").
        self._sent_box = BoxLayout(orientation="vertical",
                                   size_hint=(1, None), height=dp(44),
                                   padding=[dp(10), 0], opacity=0)
        self._sent_ja = JPLabel(text="", font_size=sp(14), halign="center")
        self._sent_ja.bind(size=self._sent_ja.setter("text_size"))
        self._sent_en = JPLabel(text="", font_size=sp(11),
                                color=rgba(theme.MUTED), halign="center")
        self._sent_en.bind(size=self._sent_en.setter("text_size"))
        self._sent_box.add_widget(self._sent_ja)
        self._sent_box.add_widget(self._sent_en)
        self._sent_box.pos = (0, 0)
        self.bind(size=lambda w, v: setattr(self._sent_box, "width", v[0]))
        self.add_widget(self._sent_box)
        self._sent_ev = None

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def start(self, app, config, pool=None, recall_words=None) -> None:
        self._app = app
        self._clear_overlay()
        #: Typed-recall epilogue after a *won* session (Journey/Today).
        self.recall_words = list(recall_words) if recall_words else []
        self.session = build_session(app.con, app.stats, config, pool=pool)
        self.engine = self.session.engine
        if self.session.error:
            self._show_overlay(self.session.error, final=False)
            return
        self.engine.start()
        self._build_board(initial=True)
        self._update_hud()
        if self._tick_ev is None:
            self._tick_ev = Clock.schedule_interval(self._tick, 1 / 30)

    def on_leave(self, *_):
        if self._tick_ev is not None:
            self._tick_ev.cancel()
            self._tick_ev = None

    def _quit(self):
        self.session = None
        self.engine = None
        self._app.go_home()

    # ------------------------------------------------------------------ #
    # Board construction / layout
    # ------------------------------------------------------------------ #
    def _build_board(self, initial=False) -> None:
        import random as _random

        from kanjire.kivyui.fonts import jp_fonts

        self.board.clear_widgets()
        self._cards = {}
        cfg = self.engine.config
        variety = jp_fonts()
        for i, card in enumerate(self.engine.board_cards):
            # Familiarize: stable per-card style (seeded by id so a relayout
            # doesn't reshuffle the look mid-round).
            font = None
            vertical = False
            if card.face in ("kanji", "reading"):
                rng = _random.Random(card.id)
                if cfg.random_fonts and variety:
                    font = rng.choice(variety)
                if cfg.vertical_writing == "all":
                    vertical = True
                elif cfg.vertical_writing == "random":
                    vertical = rng.random() < 0.5
            w = CardWidget(card, font_name=font, vertical=vertical,
                           size_hint=(None, None))
            w.bind(on_release=lambda wid: self._on_card(wid))
            self._cards[card.id] = w
            self.board.add_widget(w)
            w.pop_in(0.03 * i)
        self._apply_stickers()
        self._layout_cards()

    def _apply_stickers(self) -> None:
        e = self.engine
        if not e.config.lives_mode:
            return
        for cid, w in self._cards.items():
            g = w.card.group
            if e.bounty_type[g] == "heart":
                w.set_sticker("♥", theme.DANGER)
            elif e.bounty_type[g] == "coin":
                w.set_sticker("¥", theme.GOLD)
            elif e.is_new[g]:
                w.set_sticker("新", theme.GOLD)

    def _layout_cards(self) -> None:
        if not self._cards or self.engine is None:
            return
        n = len(self._cards)
        gap = dp(_GAP)
        aw, ah = self.board.width, self.board.height
        if aw < 50 or ah < 50:
            return
        cols, rows, cw, ch = choose_grid(n, aw, ah, gap=gap,
                                         prefer_exact=True)
        ordered = [self._cards[cid] for cid in self.engine.board
                   if cid in self._cards]
        for i, w in enumerate(ordered):
            cx, cy = slot_center(i, cols, rows, cw, ch,
                                 self.board.x, self.board.y, aw, ah, gap=gap,
                                 count=n)
            w.size = (cw, ch)
            w.center = (cx, cy)
            w._sync()

    # ------------------------------------------------------------------ #
    # Input → engine → feedback
    # ------------------------------------------------------------------ #
    def _on_card(self, widget: CardWidget) -> None:
        if self.engine is None or self.engine.phase is not Phase.PLAYING:
            return
        audio = self._app.audio
        state = self._app.state
        res = self.engine.select(widget.card.id)
        if res.kind in ("select", "deselect"):
            for cid in res.cards:
                self._cards[cid].refresh_state()
            if res.kind == "select":
                audio.sfx.play("select")
                if state.tts_on_select and res.cards:
                    self._speak_selected(res.cards[-1])
        elif res.kind == "mismatch":
            audio.sfx.play("mismatch")
            if state.tts_on_mismatch:
                self._speak_mismatch(res)
            for cid in res.cards:
                self._cards[cid].flash_error()
            if res.life_delta < 0:
                audio.sfx.play("damage")
                self._popup("-♥", theme.DANGER, widget.center)
        elif res.kind == "group_complete":
            if res.round_complete:
                audio.sfx.chord(["match", "round_clear"], spread=0.10)
            elif res.combo >= 4:
                audio.sfx.play("match_hi")
            else:
                audio.sfx.play("match")
            if state.tts_on_match and res.word is not None:
                audio.speech.say_jp(res.word.reading)
            if res.word is not None and not self.session.is_kana:
                self._show_sentence(res.word)
            for cid in res.cards:
                self._cards[cid].celebrate()
            txt = f"+{res.points}" + (f"  ×{res.combo}" if res.combo > 1 else "")
            self._popup(txt, theme.SUCCESS, widget.center)
            if res.bounty_type == "heart":
                audio.sfx.play("heart")
                self._popup("+♥", theme.SUCCESS,
                            (widget.center_x, widget.center_y + dp(24)))
            elif res.bounty_type == "coin":
                audio.sfx.play("coin")
                self._popup(f"+{res.bonus_points}", theme.GOLD,
                            (widget.center_x, widget.center_y + dp(24)))
            if res.round_complete and not res.game_over:
                Clock.schedule_once(lambda *_: self._next_round(), 0.55)
        self._update_hud()
        if res.game_over:
            Clock.schedule_once(lambda *_: self._game_over(), 0.6)

    def _next_round(self) -> None:
        if self.engine is None or self.engine.phase is not Phase.PLAYING:
            return
        self.engine.advance()
        if self.engine.phase is Phase.GAME_OVER:  # session_mode pool ran dry
            self._game_over()
            return
        self._build_board()
        self._update_hud()

    def _popup(self, text, color, center) -> None:
        self.board.add_widget(_FloatText(text, color, center))

    def _show_sentence(self, word) -> None:
        """Flash an example sentence for the just-matched word (context reps
        for free; replaced by the next match or fading out on its own)."""
        from kanjire.data import kanjidata
        try:
            got = kanjidata.sentences_for(word.expression, word.reading, 1)
        except Exception:
            got = []
        if not got:
            return
        ja, en = got[0]
        self._sent_ja.text = ja
        self._sent_en.text = en if len(en) <= 90 else en[:89] + "…"
        self._sent_box.opacity = 1
        if self._sent_ev is not None:
            self._sent_ev.cancel()
        self._sent_ev = Clock.schedule_once(self._hide_sentence, 5.0)

    def _hide_sentence(self, *_) -> None:
        self._sent_ja.text = ""
        self._sent_en.text = ""
        self._sent_box.opacity = 0
        self._sent_ev = None

    def _speak_selected(self, card_id: int) -> None:
        """Japanese for kanji/reading/romaji cards, English for meanings."""
        card = self.engine.cards.get(card_id)
        if card is None:
            return
        word = self.engine.round_words[card.group]
        if card.face == "meaning":
            self._app.audio.speech.say_en(word.meaning)
        else:
            self._app.audio.speech.say_jp(word.reading)

    def _speak_mismatch(self, res) -> None:
        """Meaning card offended → hear what it meant (EN); otherwise drive
        the target word's correct reading home (JP). Mirrors the pyglet UI."""
        if not res.cards:
            return
        offending = self.engine.cards.get(res.cards[-1])
        if offending is not None and offending.face == "meaning":
            word = self.engine.round_words[offending.group]
            self._app.audio.speech.say_en(word.meaning)
            return
        first = self.engine.cards.get(res.cards[0])
        if first is not None:
            word = self.engine.round_words[first.group]
            self._app.audio.speech.say_jp(word.reading)

    # ------------------------------------------------------------------ #
    # Time + HUD
    # ------------------------------------------------------------------ #
    def _tick(self, dt: float) -> None:
        if self.engine is None:
            return
        ended = self.engine.update(dt)
        self._update_hud()
        if ended:
            self._game_over()

    def _update_hud(self) -> None:
        e = self.engine
        if e is None:
            return
        self._lbl_score.text = str(e.score)
        self._lbl_mid.text = f"×{e.combo}" if e.combo > 1 else ""
        if e.config.lives_mode:
            full = max(0, e.lives)
            empty = max(0, e.config.max_lives - full)
            self._lbl_right.text = "♥" * full + "♡" * empty
            self._lbl_right.color = rgba(theme.DANGER)
        elif e.config.timed:
            t = max(0, int(e.time_left + 0.999))
            self._lbl_right.text = f"{t // 60}:{t % 60:02d}"
            self._lbl_right.color = rgba(
                theme.DANGER if t <= 10 else theme.TEXT)
        elif e.mistakes_left is not None:
            self._lbl_right.text = "♥" * e.mistakes_left
            self._lbl_right.color = rgba(theme.DANGER)
        else:
            m = int(e.elapsed)
            self._lbl_right.text = f"{m // 60}:{m % 60:02d}"
            self._lbl_right.color = rgba(theme.MUTED)

    # ------------------------------------------------------------------ #
    # Game over
    # ------------------------------------------------------------------ #
    def _game_over(self) -> None:
        if self._overlay is not None or self.engine is None:
            return
        e = self.engine
        self._record_game()
        # A *won* session with review words queued gets the typed-recall
        # epilogue instead of the plain results overlay (same as desktop).
        cfg = self.session.config
        if (self.recall_words and cfg.session_mode and e.session_left == 0):
            words = self.recall_words
            self.recall_words = []
            self.session = None
            self.engine = None
            self._app.go_recall_drill(cfg, words=words)
            return
        msg = (f"{e.score:,}\n"
               f"{tr('STAT_BEST_COMBO')}  ×{e.best_combo}\n"
               f"{tr('STAT_ACCURACY')}  {int(e.accuracy * 100)}%\n"
               f"{tr('STAT_LEARNED')}  {e.words_learned}")
        self._show_overlay(msg, final=True)

    def _record_game(self) -> None:
        """High score + history + streak, mirroring the pyglet ResultsScene."""
        e, cfg, sess = self.engine, self.session.config, self.session
        self.is_record = self._app.state.record_score(cfg.name, e.score)
        # Completing the daily Today session stamps the streak (freeze mercy
        # is applied inside stamp_streak).
        if cfg.session_mode and e.session_left == 0 and cfg.name == "Today":
            try:
                self._app.state.stamp_streak()
            except Exception:
                pass
        if (e.matches or e.mistakes) and "kana" not in cfg.decks:
            keys, seen = [], set()
            for w in list(e.seen_words) + (sess.tally.struggled() if sess else []):
                k = (w.expression, w.reading)
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
            try:
                self._app.stats.log_game(cfg.name, e.score, e.matches,
                                         e.mistakes, keys[:80])
            except Exception:
                pass

    def _show_overlay(self, message: str, *, final: bool) -> None:
        self._clear_overlay()
        shade = Widget()
        with shade.canvas:
            col = Color(*rgba(theme.BG, 0.72))
            rect = RoundedRectangle(pos=shade.pos, size=shade.size, radius=[0])
        shade.bind(pos=lambda w, v: setattr(rect, "pos", v),
                   size=lambda w, v: setattr(rect, "size", v))
        del col

        panel = Panel(orientation="vertical", padding=dp(18), spacing=dp(10),
                      size_hint=(None, None), width=dp(300))
        if final:
            panel.add_widget(JPLabel(text=tr("RESULTS_OVER"), bold=True,
                                     font_size=sp(24),
                                     size_hint_y=None, height=dp(40)))
        body = JPLabel(text=message, color=rgba(theme.MUTED),
                       font_size=sp(16), halign="center",
                       size_hint_y=None)
        body.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(8)))
        panel.add_widget(body)
        if final:
            again = ThemedButton(text=tr("BTN_AGAIN"), fill=theme.ACCENT)
            again.bind(on_release=lambda *_: self._play_again())
            panel.add_widget(again)
        home = ThemedButton(text=tr("BTN_MENU"))
        home.bind(on_release=lambda *_: self._quit())
        panel.add_widget(home)
        panel.bind(minimum_height=panel.setter("height"))

        holder = FloatLayout()
        holder.add_widget(shade)
        panel.pos_hint = {"center_x": 0.5, "center_y": 0.55}
        holder.add_widget(panel)
        self._overlay = holder
        self.add_widget(holder)

    def _clear_overlay(self) -> None:
        if self._overlay is not None:
            self.remove_widget(self._overlay)
            self._overlay = None

    def _play_again(self) -> None:
        cfg = self.session.config if self.session else None
        if cfg is None:
            return
        self._clear_overlay()
        self.start(self._app, cfg)
