"""Typed-recall epilogue: type the reading of the words you just reviewed.

Runs after a completed Today's Training session, over the session's hardest
review words. Typing the reading is *recall* — much stronger evidence than
recognising a card on a board — so a clean first-try answer rates Easy in
the scheduler, an eventual answer rates Hard, and giving up rates Again.

Input is IME-free: romaji is converted to hiragana live as you type (see
:func:`kanjire.kana.romaji_to_hira`); real kana input passes through.
"""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.i18n import tr
from kanjire.kana import romaji_to_hira
from kanjire.ui import theme
from kanjire.ui.anim import Animator, ease_out_cubic, ease_out_elastic
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.textinput import TextInput

#: Give up and show the answer after this many wrong submissions.
MAX_ATTEMPTS = 2


class RecallScene(Scene):
    def __init__(self, app, words, engine, config, session=None) -> None:
        super().__init__(app)
        self.words = list(words)
        self.engine = engine          # the finished Today engine (for results)
        self.config = config
        self.session = session
        self.idx = 0
        self.attempts = 0
        self._advancing = False
        self._shake = 0.0             # animated by the Animator on a miss

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
        self._show_word()

    # ------------------------------------------------------------------ #
    @property
    def word(self):
        return self.words[self.idx] if self.idx < len(self.words) else None

    def _show_word(self) -> None:
        w = self.word
        if w is None:
            self._finish()
            return
        self.attempts = 0
        self._advancing = False
        self.kanji.text = w.expression
        self.kanji.color = theme.with_alpha(theme.TEXT, 255)
        self.meaning.text = w.get_meaning(self.app.state.locale)
        self.progress.text = f"{self.idx + 1} / {len(self.words)}"
        self.feedback.text = ""
        self.input.set_text("")
        self.preview.text = ""
        self.input.focus()

    def _on_type(self, text: str) -> None:
        self.preview.text = romaji_to_hira(text) if text else ""

    def _submit(self) -> None:
        w = self.word
        if w is None or self._advancing:
            return
        answer = romaji_to_hira(self.input.text)
        if not answer:
            return
        if answer == w.reading:
            first_try = self.attempts == 0
            try:
                self.app.stats.recalled(w, first_try=first_try)
            except Exception:
                pass
            self.app.audio.sfx.play("match_hi" if first_try else "match")
            self.feedback.color = theme.with_alpha(theme.SUCCESS, 255)
            self.feedback.text = w.reading + "  ✓"
            self._advance_after(0.7)
        else:
            self.attempts += 1
            self.app.audio.sfx.play("mismatch")
            self._shake = 12.0
            self.anim.to(self, "_shake", 0.0, 0.5, ease=ease_out_elastic)
            if self.attempts >= MAX_ATTEMPTS:
                try:
                    self.app.stats.confused(w, w, "reading")
                except Exception:
                    pass
                self.feedback.color = theme.with_alpha(theme.DANGER, 255)
                self.feedback.text = tr("RECALL_ANSWER", reading=w.reading)
                if self.app.state.tts_on_mismatch:
                    self.app.audio.speech.say_jp(w.reading)
                self._advance_after(1.6)
            else:
                self.feedback.color = theme.with_alpha(theme.GOLD, 255)
                self.feedback.text = tr("RECALL_TRY_AGAIN")
                self.input.set_text("")
                self.preview.text = ""

    def _advance_after(self, delay: float) -> None:
        self._advancing = True
        if self.app.state.tts_on_match and self.feedback.color[:3] == theme.SUCCESS:
            self.app.audio.speech.say_jp(self.word.reading)

        def nxt():
            self.idx += 1
            self._show_word()
        self.anim.after(delay, nxt)

    def _finish(self) -> None:
        self.app.go_results(self.engine, self.config, session=self.session)

    # ------------------------------------------------------------------ #
    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol in (key.ENTER, key.RETURN):
            self._submit()
        elif symbol == key.ESCAPE:
            # Bail on the whole epilogue - straight to results, no penalty.
            self._finish()

    def on_text(self, text) -> None:
        if text not in ("\r", "\n"):
            self.input.on_text(text)

    def on_text_motion(self, motion) -> None:
        self.input.on_text_motion(motion)

    def on_text_motion_select(self, motion) -> None:
        self.input.on_text_motion_select(motion)

    def on_mouse_press(self, x, y, button, modifiers) -> None:
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
        in_w = 340 * s
        self.input.set_rect(cx - in_w / 2, height - 360 * s, in_w, 40 * s)
        self.preview.x, self.preview.y = cx, height - 420 * s
        self.feedback.x, self.feedback.y = cx, height - 480 * s
        self.hint.x, self.hint.y = cx, 40 * s

    def draw(self) -> None:
        h = round(6 * scale_for(self.width, self.height))
        frac = self.idx / max(1, len(self.words))
        fill_quad(0, self.height - h, self.width * frac, h, theme.GOLD)
        self.batch.draw()

    def on_exit(self) -> None:
        self.input.delete()
