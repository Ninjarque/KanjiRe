"""Typed-recall: type the reading of each word.

Two ways in:

* as the **epilogue** to a completed Today's Training session, over its hardest
  review words (``engine`` is the finished session, results follow it), and
* as the standalone **Recall** mode (``engine`` is None: the scene samples its
  own words from the config, keeps its own score, and builds a results screen at
  the end).

Typing the reading is *recall* — much stronger evidence than recognising a card
on a board — so a clean first-try answer rates Easy in the scheduler, an
eventual answer rates Hard, and giving up rates Again.

Input is IME-free: romaji is converted to hiragana live as you type (see
:func:`kanjire.kana.romaji_to_hira`); real kana input passes through.
"""
from __future__ import annotations

import random

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.game.recall import (
    acceptable_readings,
    choice_options,
    is_correct_reading,
    prompt_for,
)
from kanjire.i18n import tr
from kanjire.kana import romaji_to_hira
from kanjire.model.wordpick import sample_words
from kanjire.ui import theme
from kanjire.ui.anim import Animator, ease_out_cubic, ease_out_elastic
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.textinput import TextInput

#: Give up and show the answer after this many wrong submissions.
MAX_ATTEMPTS = 2

#: Standalone scoring: a clean first-try answer is worth the most, an eventual
#: answer half, a given-up word nothing.
_POINTS_FIRST = 100
_POINTS_LATER = 50


class _RecallEngine:
    """A minimal engine stand-in so ResultsScene can render a standalone Recall
    session. Exposes the same read-only surface the results screen expects from
    a real GameEngine (score / matches / mistakes / accuracy / ...)."""

    def __init__(self, words) -> None:
        self.score = 0
        self.matches = 0            # words recalled (first or eventual)
        self.mistakes = 0          # words given up on
        self.seen_words = list(words)
        self.pool = list(words)
        self.session_left = 0      # not a session-mode game
        self.rounds_completed = 0  # counts each word as it's answered
        self.best_combo = 0        # longest run of first-try recalls
        self._combo = 0

    def record(self, *, recalled: bool, first_try: bool) -> None:
        self.rounds_completed += 1
        if recalled:
            self.matches += 1
            self.score += _POINTS_FIRST if first_try else _POINTS_LATER
            if first_try:
                self._combo += 1
                self.best_combo = max(self.best_combo, self._combo)
            else:
                self._combo = 0
        else:
            self.mistakes += 1
            self._combo = 0

    @property
    def accuracy(self) -> float:
        total = self.matches + self.mistakes
        return (self.matches / total) if total else 0.0

    @property
    def words_learned(self) -> int:
        return self.matches


class RecallScene(Scene):
    def __init__(self, app, words=None, engine=None, config=None,
                 session=None, pool=None) -> None:
        super().__init__(app)
        self.config = config
        self.session = session
        # Standalone when no finished game engine was handed in (the Recall
        # mode). The epilogue passes the Today engine and its words; the mode
        # passes neither and samples its own from the config.
        self.standalone = engine is None
        if words:
            self.words = list(words)
        elif pool:
            self.words = list(pool)
        elif self.standalone:
            self.words = sample_words(app, config, config.words_per_round,
                                      rng=random.Random())
        else:
            self.words = []
        self.engine = engine if engine is not None else _RecallEngine(self.words)
        self.idx = 0
        self.attempts = 0
        self._advancing = False
        self._shake = 0.0             # animated by the Animator on a miss
        self.error = None if self.words else tr("NO_WORDS")
        # Which prompt each word uses. 'listen' (dictation) needs Japanese TTS;
        # without it we always fall back to typed. The standalone mode's
        # recall_prompt setting picks typed / listen / mixed; the epilogue keeps
        # its historical every-other-one dictation.
        tts_ok = bool(getattr(app.audio.speech, "has_jp", False)
                      and not app.audio.muted)
        want = getattr(config, "recall_prompt", "mixed") if self.standalone \
            else "mixed"
        # Shared policy (kanjire.game.recall): the local copy silently
        # downgraded 'both' to mixed — one source of truth now.
        self.modes = [prompt_for(i, want, tts_ok)
                      for i in range(len(self.words))]
        self._rng = random.Random()
        #: Extra readings for multiple-choice distractors (standalone only —
        #: the epilogue's word set is small and related enough on its own).
        self._distractors = list(self.words)
        if self.standalone and want in ("choice",):
            try:
                self._distractors += sample_words(app, config, 24,
                                                  rng=self._rng)
            except Exception:
                pass

        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.anim = Animator()

        def lbl(size, color, *, bold=False, anchor_x="center"):
            out = Label(
                "", font_name=JP_FONT, font_size=size, bold=bold,
                color=theme.with_alpha(color, 255),
                anchor_x=anchor_x, anchor_y="center",
                batch=self.batch, group=self.g_text,
            )
            out._base_fs = size
            return out

        self.title = lbl(16, theme.MUTED, bold=True)
        self.title.text = tr("RECALL_TITLE")
        self.progress = lbl(13, theme.DIM, anchor_x="right")
        self.kanji = lbl(64, theme.TEXT, bold=True)
        self.meaning = lbl(14, theme.MUTED)
        self.preview = lbl(22, theme.ACCENT)
        self.feedback = lbl(20, theme.SUCCESS, bold=True)
        self.hint = lbl(11, theme.DIM)
        self.hint.text = tr("RECALL_HINT")
        self.labels = [self.title, self.progress, self.kanji, self.meaning,
                       self.preview, self.feedback, self.hint]

        self.input = TextInput(self.batch, self.g_bg, self.g_text, self.g_text,
                               font_size=16, placeholder="",
                               on_change=self._on_type)
        self.input.focus()

        from kanjire.ui.widgets.button import Button as _Button
        self._Button = _Button
        #: Multiple-choice option buttons for the current word (mode 'choice').
        self.choice_btns: list[tuple[str, object]] = []
        #: Study-first pass: show the drill's words before quizzing them.
        self._preview_labels: list = []
        self.preview_btn = None
        self.previewing = bool(
            self.standalone and getattr(config, "recall_preview", False)
            and self.words and not self.error)
        if self.previewing:
            self._build_preview()
        else:
            self._show_word()

    # ------------------------------------------------------------------ #
    # Study-first preview
    # ------------------------------------------------------------------ #
    def _input_visible(self, visible: bool) -> None:
        """The text box has no hide flag — park it off-screen when a stage
        doesn't type (preview, multiple choice). It sat behind the Start
        button otherwise (user screenshot: overlapping purple rectangle)."""
        self._input_off = not visible
        if not visible:
            self.input.set_rect(-4000, -4000, 10, 10)
        elif getattr(self, "width", 0):
            self.on_resize(self.width, self.height)   # re-place it

    def _build_preview(self) -> None:
        self.title.text = tr("RECALL_PREVIEW_TITLE")
        self.kanji.text = ""
        self.meaning.text = ""
        self.hint.text = ""
        self._input_visible(False)
        loc = self.app.state.locale
        for w in self.words:
            reading = "・".join(acceptable_readings(w.reading))
            meaning = w.get_meaning(loc)
            if len(meaning) > 38:
                meaning = meaning[:37] + "…"
            out = Label(
                f"{w.expression}   {reading}   — {meaning}",
                font_name=JP_FONT, font_size=15,
                color=theme.with_alpha(theme.TEXT, 255),
                anchor_x="left", anchor_y="center",
                batch=self.batch, group=self.g_text)
            self._preview_labels.append(out)
        self.preview_btn = self._Button(
            tr("RECALL_START"), self._end_preview,
            self.batch, self.g_bg, self.g_text,
            accent=theme.SUCCESS, font_size=14)
        self.on_resize(self.width, self.height)

    def _end_preview(self) -> None:
        if not self.previewing:
            return
        self.previewing = False
        for lab in self._preview_labels:
            lab.delete()
        self._preview_labels.clear()
        if self.preview_btn is not None:
            self.preview_btn.delete()
            self.preview_btn = None
        self.title.text = tr("RECALL_TITLE")
        self.hint.text = tr("RECALL_HINT")
        self._show_word()

    # ------------------------------------------------------------------ #
    @property
    def word(self):
        return self.words[self.idx] if self.idx < len(self.words) else None

    @property
    def mode(self) -> str:
        return (self.modes[self.idx]
                if self.idx < len(self.modes) else "typed")

    def _show_word(self) -> None:
        w = self.word
        if w is None:
            self._finish()
            return
        self.attempts = 0
        self._advancing = False
        self._clear_choices()
        self._input_visible(self.mode != "choice")
        # The typing hint is wrong for multiple choice.
        self.hint.text = "" if self.mode == "choice" else tr("RECALL_HINT")
        if self.mode == "choice":
            # Multiple choice: kanji + meaning shown, pick the reading among
            # lookalike distractors — forces real discrimination.
            self.kanji.text = w.expression
            self.title.text = tr("RECALL_TITLE")
            self.meaning.text = w.get_meaning(self.app.state.locale)
            for r in choice_options(w, self._distractors, self._rng):
                b = self._Button(r, lambda rr=r: self._choice_pick(rr),
                                 self.batch, self.g_bg, self.g_text,
                                 accent=theme.FACE_COLORS["reading"],
                                 font_size=15)
                self.choice_btns.append((r, b))
            self.on_resize(self.width, self.height)
        elif self.mode == "listen":
            # Dictation: hear it, type it. Kanji revealed on the answer.
            self.kanji.text = "♪"
            self.title.text = tr("RECALL_LISTEN_TITLE")
            self.meaning.text = tr("RECALL_LISTEN_HINT")
            self.app.audio.speech.say_jp(w.reading)
        elif self.mode == "both":
            # See it AND hear it.
            self.kanji.text = w.expression
            self.title.text = tr("RECALL_TITLE")
            self.meaning.text = w.get_meaning(self.app.state.locale)
            self.app.audio.speech.say_jp(w.reading)
        else:
            self.kanji.text = w.expression
            self.title.text = tr("RECALL_TITLE")
            self.meaning.text = w.get_meaning(self.app.state.locale)
        self.kanji.color = theme.with_alpha(theme.TEXT, 255)
        self.progress.text = f"{self.idx + 1} / {len(self.words)}"
        self.feedback.text = ""
        self.input.set_text("")
        self.preview.text = ""
        self.input.focus()

    def _on_type(self, text: str) -> None:
        self.preview.text = romaji_to_hira(text) if text else ""

    def _clear_choices(self) -> None:
        for _r, b in self.choice_btns:
            b.delete()
        self.choice_btns.clear()

    def _grade_correct(self) -> None:
        w = self.word
        first_try = self.attempts == 0
        try:
            self.app.stats.recalled(w, first_try=first_try)
        except Exception:
            pass
        if self.standalone:
            self.engine.record(recalled=True, first_try=first_try)
        self.app.audio.sfx.play("match_hi" if first_try else "match")
        self.feedback.color = theme.with_alpha(theme.SUCCESS, 255)
        self.feedback.text = w.reading + "  ○"
        if self.mode == "listen":     # reveal what you transcribed
            self.kanji.text = w.expression
        self._advance_after(0.7)

    def _grade_wrong(self) -> None:
        w = self.word
        self.attempts += 1
        self.app.audio.sfx.play("mismatch")
        self._shake = 12.0
        self.anim.to(self, "_shake", 0.0, 0.5, ease=ease_out_elastic)
        if self.attempts >= MAX_ATTEMPTS:
            try:
                self.app.stats.confused(w, w, "reading")
            except Exception:
                pass
            if self.standalone:
                self.engine.record(recalled=False, first_try=False)
            self.feedback.color = theme.with_alpha(theme.DANGER, 255)
            self.feedback.text = tr("RECALL_ANSWER", reading=w.reading)
            if self.mode == "listen":
                self.kanji.text = w.expression
            if self.app.state.tts_on_mismatch:
                self.app.audio.speech.say_jp(w.reading)
            self._advance_after(1.6)
        else:
            self.feedback.color = theme.with_alpha(theme.GOLD, 255)
            self.feedback.text = tr("RECALL_TRY_AGAIN")
            if self.mode == "listen":
                self.app.audio.speech.say_jp(w.reading)   # replay
            self.input.set_text("")
            self.preview.text = ""

    def _submit(self) -> None:
        w = self.word
        if w is None or self._advancing or self.previewing:
            return
        answer = romaji_to_hira(self.input.text)
        if not answer:
            return
        # ~20 words carry alternative readings ("なん; なに") — any counts.
        if is_correct_reading(answer, w.reading):
            self._grade_correct()
        else:
            self._grade_wrong()

    def _choice_pick(self, reading: str) -> None:
        w = self.word
        if w is None or self._advancing or self.previewing:
            return
        if is_correct_reading(reading, w.reading):
            self._grade_correct()
        else:
            # Grey the wrong pick so the retry is a real second decision.
            for r, b in self.choice_btns:
                if r == reading:
                    b.enabled = False
                    b._refresh()
            self._grade_wrong()

    def _advance_after(self, delay: float) -> None:
        self._advancing = True
        if self.app.state.tts_on_match and self.feedback.color[:3] == theme.SUCCESS:
            self.app.audio.speech.say_jp(self.word.reading)
        self._speech_wait = 0.0

        def nxt():
            self.idx += 1
            self._show_word()

        def poll():
            # Never advance while audio is still playing: the next prompt
            # purges the current utterance and cut answers short. Wait for
            # the full playback + 0.5s, capped so TTS can't freeze the drill.
            self._speech_wait += 0.15
            if (self.app.audio.speech.is_speaking()
                    and self._speech_wait < 8.0):
                self.anim.after(0.15, poll)
            else:
                self.anim.after(0.5, nxt)

        self.anim.after(delay, poll)

    def _finish(self) -> None:
        # A standalone session with nothing to recall (empty pool, or bailed
        # before answering anything) has no meaningful results screen.
        if self.standalone and not self.engine.seen_words:
            self.app.go_menu()
            return
        self.app.go_results(self.engine, self.config, session=self.session)

    # ------------------------------------------------------------------ #
    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol in (key.ENTER, key.RETURN):
            if self.previewing:
                self._end_preview()
            else:
                self._submit()
        elif (self.choice_btns and not self._advancing
                and key._1 <= symbol <= key._4):
            i = symbol - key._1
            if i < len(self.choice_btns) and self.choice_btns[i][1].enabled:
                self._choice_pick(self.choice_btns[i][0])
        elif (symbol == key.F1 and self.mode in ("listen", "both")
                and self.word):
            self.app.audio.speech.say_jp(self.word.reading)   # replay
        elif symbol == key.ESCAPE:
            # Bail on the whole epilogue - straight to results, no penalty.
            self._finish()

    def on_text(self, text) -> None:
        # No typing during the study-first preview or multiple choice —
        # keystrokes fed the hidden input and left a ghost kana preview.
        if self.previewing or self.mode == "choice":
            return
        if text not in ("\r", "\n"):
            self.input.on_text(text)

    def on_text_motion(self, motion) -> None:
        if self.previewing or self.mode == "choice":
            return
        self.input.on_text_motion(motion)

    def on_text_motion_select(self, motion) -> None:
        if self.previewing or self.mode == "choice":
            return
        self.input.on_text_motion_select(motion)

    def on_mouse_press(self, x, y, button, modifiers) -> None:
        # Clicking the ♪ / kanji prompt replays the audio (same as F1) —
        # phones have no F1, and it's a natural click target anywhere.
        if (self.mode in ("listen", "both") and self.word
                and abs(x - self.kanji.x) < 120
                and abs(y - self.kanji.y) < 70):
            self.app.audio.speech.say_jp(self.word.reading)
            return
        if self.previewing:
            if self.preview_btn is not None and self.preview_btn.contains(x, y):
                self.preview_btn.click()
            return
        for _r, b in self.choice_btns:
            if b.enabled and b.contains(x, y):
                b.click()
                return
        self.input.on_mouse_press(x, y, button, modifiers)
        self.input.focus()          # there's nothing else to focus here

    def update(self, dt: float) -> None:
        self.anim.update(dt)
        base_x = self.width / 2
        self.kanji.x = base_x + self._shake * (1 if int(self._shake * 7) % 2 else -1)

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        cx = width / 2
        self.title.x, self.title.y = cx, height - 60 * s
        self.progress.x, self.progress.y = width - 40 * s, height - 60 * s
        self.kanji.x, self.kanji.y = cx, height - 200 * s
        self.meaning.x, self.meaning.y = cx, height - 280 * s
        if not getattr(self, "_input_off", False):
            in_w = 340 * s
            self.input.set_rect(cx - in_w / 2, height - 360 * s,
                                in_w, 40 * s)
        self.preview.x, self.preview.y = cx, height - 420 * s
        self.feedback.x, self.feedback.y = cx, height - 480 * s
        self.hint.x, self.hint.y = cx, 40 * s
        # Study-first list: one row per word, row height shrinking so even a
        # 24-word drill fits above the Start button at any window size.
        if self._preview_labels:
            n = len(self._preview_labels)
            avail = height - 240 * s
            row_h = min(34 * s, avail / max(1, n))
            y0 = height - 130 * s
            for i, lab in enumerate(self._preview_labels):
                lab.font_size = max(8, round(min(15 * s, row_h * 0.55)))
                lab.x = cx - 260 * s
                lab.y = y0 - i * row_h
            if self.preview_btn is not None:
                self.preview_btn.set_scale(s)
                self.preview_btn.set_rect(
                    cx - 110 * s,
                    max(60 * s, y0 - n * row_h - 30 * s),
                    220 * s, 40 * s)
        # Multiple-choice options: 2×2 grid below the meaning.
        if self.choice_btns:
            bw, bh, gap = 220 * s, 44 * s, 14 * s
            grid_w = 2 * bw + gap
            x0 = cx - grid_w / 2
            y0 = height - 360 * s
            for i, (_r, b) in enumerate(self.choice_btns):
                r_, c_ = divmod(i, 2)
                b.set_scale(s)
                b.set_rect(x0 + c_ * (bw + gap),
                           y0 - r_ * (bh + gap), bw, bh)

    def draw(self) -> None:
        h = round(6 * scale_for(self.width, self.height))
        frac = self.idx / max(1, len(self.words))
        fill_quad(0, self.height - h, self.width * frac, h, theme.GOLD)
        self.batch.draw()

    def on_exit(self) -> None:
        self.input.delete()
        self._clear_choices()
        for lab in self._preview_labels:
            lab.delete()
        self._preview_labels.clear()
        if self.preview_btn is not None:
            self.preview_btn.delete()
