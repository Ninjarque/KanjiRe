"""The gameplay scene: board of cards, HUD, and all the juicy animations."""
from __future__ import annotations

import random

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire import kana
from kanjire.data import db
from kanjire.data.stats import knowledge_score
from kanjire.game.config import GameConfig
from kanjire.game.engine import GameEngine, Phase
from kanjire.i18n import tr
from kanjire.model.sampling import learn_sample_words
from kanjire.ui import theme
from kanjire.ui.anim import (
    Animator,
    ease_out_back,
    ease_out_cubic,
    ease_out_elastic,
)
from kanjire.ui.fonts import JP_FONT, JP_FONTS
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.layout import choose_grid, slot_center
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.card import CardView

HUD_H = 96
GAP = 16

#: Discrete Learn-mode selectors map onto these relative weights.
_LEARN_STEPS: dict[int, int] = {0: 0, 1: 1, 2: 3, 3: 6}


class _Popup:
    """A floating, fading score/combo label."""

    def __init__(self, text, x, y, color, batch, group, size=22):
        self.life = self.max_life = 0.9
        self.vy = 60.0
        self.color = color
        self.label = Label(
            text, font_name=JP_FONT, font_size=size, bold=True,
            color=theme.with_alpha(color, 255),
            anchor_x="center", anchor_y="center", x=x, y=y,
            batch=batch, group=group,
        )

    def update(self, dt) -> bool:
        self.life -= dt
        self.label.y += self.vy * dt
        self.label.color = theme.with_alpha(self.color, max(0.0, self.life / self.max_life))
        if self.life <= 0:
            self.label.delete()
            return True
        return False


class _SessionTally:
    """Session-local record of what the player struggled with this game,
    forwarding every event to the app-wide recorder unchanged."""

    def __init__(self, recorder) -> None:
        self._rec = recorder
        self._confused: dict[tuple[str, str], int] = {}
        self._words: dict[tuple[str, str], object] = {}

    def saw(self, word) -> None:
        if self._rec is not None:
            self._rec.saw(word)

    def matched(self, word) -> None:
        if self._rec is not None:
            self._rec.matched(word)

    def confused(self, target, offending, face) -> None:
        for w in (target, offending):
            key = (w.expression, w.reading)
            self._confused[key] = self._confused.get(key, 0) + 1
            self._words[key] = w
        if self._rec is not None:
            self._rec.confused(target, offending, face)

    def struggled(self, limit: int = 12) -> list:
        """Words involved in confusions this session, most-confused first."""
        keys = sorted(self._confused, key=lambda k: -self._confused[k])
        return [self._words[k] for k in keys[:limit]]

    def struggled_keys(self) -> set[tuple[str, str]]:
        return set(self._confused)


class GameScene(Scene):
    def __init__(self, app, config: GameConfig, pool=None) -> None:
        super().__init__(app)
        self.config = config

        # Kana mode is synthetic: words are invented on the fly per round, so
        # we don't touch the SQLite vocab DB at all and the pool is irrelevant
        # (the sampler ignores it).
        self.is_kana = kana.KANA_DECK in config.decks
        if pool is not None:
            # Explicit word list (e.g. a "practice the tricky ones" rematch).
            self.pool = list(pool)
            self.error = None if self.pool else tr("NO_WORDS")
        elif self.is_kana:
            self.pool = []
            self.error = None
        else:
            levels = config.levels or None
            self.pool = db.load_words(
                app.con, decks=list(config.decks), levels=levels, require_kanji=True
            )
            self.error = None if self.pool else tr("NO_WORDS")

        # If the player picked Learn (or Survival, which is bucket-mixed), swap
        # in a bucket-aware sampler drawn from their known/less-known/unknown
        # profile. One rng is shared by the engine and the metadata closures so
        # bounty rolls are reproducible under seeded tests.
        rng = random.Random()
        sampler = None
        meta_provider = None
        if self.is_kana:
            sampler = lambda pool, n, *, bias, rng, penalize=None: kana.sample(
                n, length=config.kana_length, script=config.kana_script, rng=rng,
            )
        elif any((config.learn_known, config.learn_less_known, config.learn_unknown)):
            buckets = app.stats.classify_words(self.pool)
            weights = {
                "known":      _LEARN_STEPS[config.learn_known],
                "less_known": _LEARN_STEPS[config.learn_less_known],
                "unknown":    _LEARN_STEPS[config.learn_unknown],
            }
            sampler = lambda pool, n, *, bias, rng, penalize=None: learn_sample_words(
                pool, n, buckets=buckets, weights=weights, bias=bias, rng=rng,
                penalize=penalize,
            )

        # Survival (lives_mode): per-deal new/bounty metadata from the player's
        # history. "new" = never matched; bounty candidate = the hardest already-
        # learned word on the board that's among their toughest overall.
        if config.lives_mode and not self.is_kana:
            hard_keys = {
                (r["expression"], r["reading"])
                for r in app.stats.hardest_seen(60)
            }

            def meta_provider(words):
                is_new: list[bool] = []
                scored: list[tuple[float, int]] = []
                for i, w in enumerate(words):
                    try:
                        row = app.stats.get_for(w.expression, w.reading)
                    except Exception:
                        row = None
                    new = (row is None) or ((row.get("matches") or 0) == 0)
                    is_new.append(new)
                    if (not new) and (w.expression, w.reading) in hard_keys:
                        scored.append((knowledge_score(row), i))
                scored.sort(key=lambda si: si[0])  # hardest (lowest) first
                cand = scored[0][1] if scored else None
                return is_new, cand

        # Every gameplay event flows through the app-wide recorder, including
        # rounds the player abandons; see kanjire.data.stats.StatsRecorder.
        # The tally keeps a session-local copy of confusions for the results
        # screen's "practice the tricky ones" rematch.
        self.tally = _SessionTally(app.stats)
        self.engine = GameEngine(config, self.pool, rng=rng, recorder=self.tally,
                                 sampler=sampler, meta_provider=meta_provider)
        if not self.error:
            self.engine.start()

        self.batch = pyglet.graphics.Batch()
        self.g_glow = OrderedGroup(0)
        self.g_cardbg = OrderedGroup(1)
        self.g_text = OrderedGroup(2)
        self.g_hud = OrderedGroup(3)

        self.anim = Animator()
        self.cards: dict[int, CardView] = {}
        self.popups: list[_Popup] = []
        self._transitioning = False
        self._ending = False
        self._s = 1.0
        self._hud_h = HUD_H

        self._build_hud()
        if not self.error:
            self._build_round(initial=True)

    # ------------------------------------------------------------------ #
    # HUD
    # ------------------------------------------------------------------ #
    def _build_hud(self) -> None:
        def mk(size, **kw):
            lbl = Label(
                "", font_name=JP_FONT, font_size=size,
                batch=self.batch, group=self.g_hud,
                color=theme.with_alpha(theme.TEXT, 255), **kw
            )
            lbl._base_fs = size  # for resolution rescaling in on_resize
            return lbl
        self.score_label = mk(30, bold=True, anchor_x="left", anchor_y="center")
        self.score_label.color = theme.with_alpha(theme.GOLD, 255)
        self.combo_label = mk(15, bold=True, anchor_x="left", anchor_y="center")
        self.combo_label.color = theme.with_alpha(theme.ACCENT, 255)

        self.center_label = mk(22, bold=True, anchor_x="center", anchor_y="center")
        self.status_label = mk(13, anchor_x="center", anchor_y="center")
        self.status_label.color = theme.with_alpha(theme.MUTED, 255)

        self.round_label = mk(14, anchor_x="right", anchor_y="center")
        self.round_label.color = theme.with_alpha(theme.MUTED, 255)
        self.hint_label = mk(11, anchor_x="right", anchor_y="center")
        self.hint_label.color = theme.with_alpha(theme.DIM, 255)
        self._hud_labels = [
            self.score_label, self.combo_label, self.center_label,
            self.status_label, self.round_label, self.hint_label,
        ]

        # timer bar shapes
        self.timer_bg = shapes.Rectangle(
            0, 0, 10, 6, color=theme.PANEL_HI, batch=self.batch, group=self.g_hud
        )
        self.timer_fg = shapes.Rectangle(
            0, 0, 10, 6, color=theme.ACCENT, batch=self.batch, group=self.g_hud
        )

    # ------------------------------------------------------------------ #
    # Round building
    # ------------------------------------------------------------------ #
    def _clear_cards(self) -> None:
        for c in self.cards.values():
            c.delete()
        self.cards.clear()

    def _pick_font(self, face: str) -> str | None:
        # Always use the default font for English meaning cards: randomising
        # a Latin gloss has no recognition benefit and is just noisy.
        if face == "meaning":
            return JP_FONT
        if self.config.random_fonts and JP_FONTS:
            return random.choice(JP_FONTS)
        return JP_FONT

    def _pick_direction(self, face: str) -> str:
        if face == "meaning":
            return "horizontal"
        if self.config.vertical_writing == "all":
            return "vertical"
        if self.config.vertical_writing == "random":
            return "vertical" if random.random() < 0.5 else "horizontal"
        return "horizontal"

    def _build_round(self, initial: bool = False) -> None:
        self._clear_cards()
        for model in self.engine.board_cards:
            self.cards[model.id] = CardView(
                model, self.batch, self.g_glow, self.g_cardbg, self.g_text,
                font_name=self._pick_font(model.face),
                direction=self._pick_direction(model.face),
            )
        self._layout_cards()
        if self.config.lives_mode:
            self._apply_stickers()
        # entrance animation: pop in with a slight stagger
        for i, c in enumerate(self.cards.values()):
            c.scale = 0.2
            c.alpha = 0.0
            delay = min(i * 0.03, 0.5)
            self.anim.to(c, "scale", 1.0, 0.45, ease=ease_out_back, delay=delay)
            self.anim.to(c, "alpha", 1.0, 0.3, ease=ease_out_cubic, delay=delay)
        self._transitioning = False

    def _apply_stickers(self) -> None:
        """Mark each card with its group's Survival sticker: ♥/¥ bounty, or 新."""
        e = self.engine
        for cv in self.cards.values():
            g = cv.model.group
            bt = e.bounty_type[g] if g < len(e.bounty_type) else None
            if bt == "heart":
                cv.set_sticker("♥", theme.DANGER)
            elif bt == "coin":
                cv.set_sticker("¥", theme.GOLD)
            elif g < len(e.is_new) and e.is_new[g]:
                cv.set_sticker("新", theme.GOLD)
            else:
                cv.set_sticker("")

    def _layout_cards(self) -> None:
        n = len(self.cards)
        if not n:
            return
        area_x = 40
        area_y = 30
        area_w = self.width - 80
        area_h = self.height - self._hud_h - 60
        cols, rows, cw, ch = choose_grid(n, area_w, area_h, GAP)
        # Generous caps so a maximised or fullscreen window actually uses the
        # space, but still bounded so 1-2 cards don't look comically large.
        cw = min(cw, 320)
        ch = min(ch, 280)
        for i, c in enumerate(self.cards.values()):
            cx, cy = slot_center(
                i, cols, rows, cw, ch, area_x, area_y, area_w, area_h, GAP, count=n
            )
            c.set_slot(cx, cy, cw, ch)

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.error or self._transitioning or self._ending:
            return
        if self.engine.phase is not Phase.PLAYING:
            return
        for c in self.cards.values():
            if c.model.matched or not c.visible:
                continue
            if c.contains(x, y):
                self._handle(self.engine.select(c.model.id))
                break

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        if self.error:
            return
        for c in self.cards.values():
            if c.model.matched or c.model.selected or not c.visible:
                continue
            hover = c.contains(x, y)
            target = 0.25 if hover else 0.0
            if abs(c.glow - target) > 0.01 and c.glow not in (0.8, 1.0):
                c.glow = target

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol == key.ESCAPE:
            self.app.go_menu()
        elif symbol == key.M:
            self.app.toggle_mute()

    # ------------------------------------------------------------------ #
    # Result handling / animations
    # ------------------------------------------------------------------ #
    def _handle(self, result) -> None:
        kind = result.kind
        audio = self.app.audio
        state = self.app.state
        if kind == "select":
            audio.sfx.play("select")
            # Optional speech feedback when a card is selected. Reading the
            # card aloud reinforces the kanji↔reading mapping for free, but
            # it's noisy if the player is mass-selecting, so it's off by
            # default. Settings → "Speak on select" turns it on.
            if state.tts_on_select and result.cards:
                self._speak_selected(result.cards[-1])
            for cid in result.cards:
                c = self.cards[cid]
                self.anim.to(c, "scale", 1.08, 0.18, ease=ease_out_back)
                self.anim.to(c, "glow", 0.85, 0.18)
        elif kind == "deselect":
            for cid in result.cards:
                c = self.cards[cid]
                self.anim.to(c, "scale", 1.0, 0.18)
                self.anim.to(c, "glow", 0.0, 0.18)
        elif kind == "group_complete":
            # Escalating feedback: clearing the whole board gets a two-note
            # arpeggio, a hot combo gets the brighter chime.
            if result.round_complete:
                audio.sfx.chord(["match", "round_clear"], spread=0.10)
            elif result.combo >= 4:
                audio.sfx.play("match_hi")
            else:
                audio.sfx.play("match")
            if state.tts_on_match and result.word is not None:
                audio.speech.say_jp(result.word.reading)
            self._animate_match(result)
        elif kind == "mismatch":
            audio.sfx.play("mismatch")
            if state.tts_on_mismatch:
                self._speak_mismatch(result)
            self._animate_mismatch(result)

        # Survival heart/coin/damage feedback (sound + a floating marker near
        # the HUD hearts).
        if self.config.lives_mode:
            self._lives_feedback(result)

        if result.game_over:
            self._end_game()

    def _lives_feedback(self, result) -> None:
        audio = self.app.audio
        px, py = self.width / 2, self.height - self._hud_h - 26 * self._s
        if getattr(result, "bounty_type", None) == "heart" and result.life_delta > 0:
            audio.sfx.play("heart")
            self.popups.append(_Popup("+♥", px, py, theme.SUCCESS,
                                      self.batch, self.g_hud, size=int(24 * self._s)))
        elif getattr(result, "bounty_type", None) == "coin" and result.bonus_points:
            audio.sfx.play("coin")
            self.popups.append(_Popup(f"+{result.bonus_points}", px, py, theme.GOLD,
                                      self.batch, self.g_hud, size=int(22 * self._s)))
        elif result.life_delta < 0:
            audio.sfx.play("damage")
            self.popups.append(_Popup("-♥", px, py, theme.DANGER,
                                      self.batch, self.g_hud, size=int(24 * self._s)))

    def _speak_selected(self, card_id: int) -> None:
        """Speak the card that was just selected: Japanese for kanji/reading,
        English for meaning. Helps drill the pronunciation as you build a group."""
        card = self.engine.cards.get(card_id)
        if card is None:
            return
        word = self.engine.round_words[card.group]
        if card.face == "meaning":
            self.app.audio.speech.say_en(word.meaning)
        else:
            self.app.audio.speech.say_jp(word.reading)

    def _speak_mismatch(self, result) -> None:
        """On a mismatch, give the most useful audio feedback.

        If the offending card was a *meaning* card, speak it in English so the
        player hears what that card actually meant. Otherwise speak the reading
        of the target word - the one they were trying to assemble - to drive the
        correct pronunciation home.
        """
        audio = self.app.audio
        cards = result.cards
        if not cards:
            return
        offending = self.engine.cards.get(cards[-1])
        if offending is not None and offending.face == "meaning":
            word = self.engine.round_words[offending.group]
            audio.speech.say_en(word.meaning)
            return
        first = self.engine.cards.get(cards[0])
        if first is not None:
            word = self.engine.round_words[first.group]
            audio.speech.say_jp(word.reading)

    def _animate_match(self, result) -> None:
        if getattr(result, "sticker_cleared", False) or getattr(result, "bounty_type", None):
            for cid in result.cards:
                self.cards[cid].set_sticker("")
        for cid in result.cards:
            c = self.cards[cid]
            c.glow = 1.0
            self.anim.to(c, "offset_y", 26, 0.5, ease=ease_out_cubic)
            self.anim.to(c, "scale", 1.16, 0.22, ease=ease_out_back)
            self.anim.to(c, "alpha", 0.0, 0.45, delay=0.18,
                         on_done=lambda c=c: setattr(c, "visible", False))

        word = result.word
        if word is not None:
            ids = result.cards
            cx = sum(self.cards[i].cx for i in ids) / len(ids)
            cy = sum(self.cards[i].cy for i in ids) / len(ids)
            self.popups.append(
                _Popup(f"+{result.points}", cx, cy + 10, theme.GOLD,
                       self.batch, self.g_hud, size=24)
            )
            if result.combo >= 2:
                self.popups.append(
                    _Popup(tr("POPUP_COMBO", n=result.combo),
                           cx, cy - 22, theme.ACCENT,
                           self.batch, self.g_hud, size=18)
                )

        if result.round_complete and not result.game_over:
            self._transitioning = True
            self.anim.after(0.75, self._next_round)

    def _animate_mismatch(self, result) -> None:
        for cid in result.cards:
            c = self.cards.get(cid)
            if c is None:
                continue
            c.flash = 1.0
            c.shake = 12.0
            self.anim.to(c, "flash", 0.0, 0.5, ease=ease_out_cubic)
            self.anim.to(c, "shake", 0.0, 0.55, ease=ease_out_elastic)
            self.anim.to(c, "scale", 1.0, 0.2)
            self.anim.to(c, "glow", 0.0, 0.3)

    def _next_round(self) -> None:
        self.engine.advance()
        self._build_round()

    def _end_game(self) -> None:
        if self._ending:
            return
        self._ending = True
        self.anim.after(0.6, lambda: self.app.go_results(
            self.engine, self.config, session=self.tally))

    # ------------------------------------------------------------------ #
    # Per-frame
    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> None:
        if self.error:
            return
        if self.engine.update(dt):  # timer expired
            self._end_game()
        self.anim.update(dt)
        self.popups = [p for p in self.popups if not p.update(dt)]
        for c in self.cards.values():
            c.apply()
        self._update_hud()

    def _update_hud(self) -> None:
        e = self.engine
        self.score_label.text = f"{e.score:,}"
        self.combo_label.text = tr("HUD_COMBO", n=e.combo) if e.combo >= 2 else ""

        if self.config.lives_mode:
            lives = max(0, e.lives)
            empty = max(0, self.config.max_lives - lives)
            self.center_label.text = "♥ " * lives + "♡ " * empty
            self.center_label.color = theme.with_alpha(theme.DANGER, 255)
            self.status_label.text = tr("HUD_LIVES")
        elif self.config.timed:
            t = max(0, int(e.time_left))
            self.center_label.text = f"{t // 60}:{t % 60:02d}"
            self.center_label.color = theme.with_alpha(
                theme.DANGER if e.time_left <= 10 else theme.TEXT, 255
            )
            frac = e.time_left / self.config.duration if self.config.duration else 0
            self.timer_fg.width = max(0, int(self.timer_bg.width * frac))
            self.timer_fg.color = theme.DANGER if frac < 0.2 else theme.ACCENT
            self.status_label.text = ""
        elif self.config.max_mistakes is not None:
            left = e.mistakes_left or 0
            self.center_label.text = "♥ " * left + "♡ " * e.mistakes
            self.center_label.color = theme.with_alpha(theme.DANGER, 255)
            self.status_label.text = tr("HUD_LIVES")
        else:
            self.center_label.text = ""
            self.status_label.text = ""

        if self.config.repetitions > 1:
            self.round_label.text = tr(
                "HUD_ROUND_PASS",
                n=e.rounds_completed + 1,
                pass_n=e.subround + 1,
                passes=self.config.repetitions,
                learned=e.words_learned,
            )
        else:
            self.round_label.text = tr(
                "HUD_ROUND",
                n=e.rounds_completed + 1,
                learned=e.words_learned,
            )
        sound = tr("HUD_SOUND_OFF") if self.app.audio.muted else tr("HUD_SOUND_ON")
        self.hint_label.text = tr("HUD_HINT", sound=sound)

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #
    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        # HUD strip
        fill_quad(0, self.height - self._hud_h, self.width, self._hud_h, theme.PANEL)
        fill_quad(0, self.height - self._hud_h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()
        if self.error:
            self._draw_error()

    def _draw_error(self) -> None:
        Label(
            self.error, font_name=JP_FONT, font_size=20,
            color=theme.with_alpha(theme.DANGER, 255),
            anchor_x="center", anchor_y="center",
            x=self.width / 2, y=self.height / 2,
        ).draw()
        Label(
            tr("NO_WORDS_HINT"),
            font_name=JP_FONT, font_size=13,
            color=theme.with_alpha(theme.MUTED, 255),
            anchor_x="center", anchor_y="center",
            x=self.width / 2, y=self.height / 2 - 34,
        ).draw()

    def on_resize(self, width: int, height: int) -> None:
        s = scale_for(width, height)
        self._s = s
        self._hud_h = round(HUD_H * s)
        for lbl in self._hud_labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        # HUD positions
        cy = height - self._hud_h / 2
        self.score_label.x, self.score_label.y = 28 * s, cy + 12 * s
        self.combo_label.x, self.combo_label.y = 30 * s, cy - 20 * s
        self.center_label.x, self.center_label.y = width / 2, cy + 8 * s
        self.status_label.x, self.status_label.y = width / 2, cy - 22 * s
        self.round_label.x, self.round_label.y = width - 28 * s, cy + 12 * s
        self.hint_label.x, self.hint_label.y = width - 28 * s, cy - 18 * s

        bar_w = 220 * s
        self.timer_bg.x = width / 2 - bar_w / 2
        self.timer_bg.y = cy - 36 * s
        self.timer_bg.width = bar_w
        self.timer_bg.height = max(4, round(6 * s))
        self.timer_fg.x = self.timer_bg.x
        self.timer_fg.y = self.timer_bg.y
        self.timer_fg.height = self.timer_bg.height
        if not self.config.timed:
            self.timer_bg.width = 0
            self.timer_fg.width = 0

        if not self.error:
            self._layout_cards()
