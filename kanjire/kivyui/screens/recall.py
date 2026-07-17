"""Recall mode on Kivy: type the reading of each word (typed or dictation).

Same behaviour as the desktop scene: romaji converts to hiragana live as
you type, two misses reveal the answer, first-try answers score highest and
grade Easy in the scheduler. The soft keyboard is the whole input story on
Android, so the input row stays pinned above it.
"""
from __future__ import annotations

import random

from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from kivy.uix.behaviors import ButtonBehavior

from kanjire.game.recall import (
    MAX_ATTEMPTS,
    RecallEngine,
    acceptable_readings,
    choice_options,
    is_correct_reading,
    prompt_for,
)
from kanjire.i18n import tr
from kanjire.kana import romaji_to_hira
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, Panel, ThemedButton
from kanjire.model.wordpick import sample_words


class _PromptLabel(ButtonBehavior, JPLabel):
    """The big prompt (♪ or kanji); tapping it replays the audio."""

    def __init__(self, on_tap, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap

    def on_release(self):
        self._on_tap()


class RecallScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._app = None
        self._overlay = None
        self._build()

    def on_enter(self, *_):
        # 'below_target' pans by the height of the flexible spacer above the
        # input — the kanji went off-screen. 'resize' shrinks the window so
        # the layout compacts: kanji + kana preview + input all stay visible
        # above the keyboard. ANDROID ONLY: on desktop Windows, resize mode
        # re-bases Window.size into density-scaled pixels (420→525 at 125%
        # display scaling), corrupting screenshots and coordinate spaces —
        # and desktop has no overlaying keyboard to compensate for anyway.
        import os
        if "ANDROID_ARGUMENT" in os.environ:
            from kivy.core.window import Window
            Window.softinput_mode = "resize"

    def on_leave(self, *_):
        import os
        if "ANDROID_ARGUMENT" in os.environ:
            from kivy.core.window import Window
            Window.softinput_mode = "below_target"

    # ------------------------------------------------------------------ #
    def start(self, app, config, words=None) -> None:
        self._app = app
        self.config = config
        self._clear_overlay()
        # Explicit words = an epilogue drill (a won session's hardest
        # reviews); otherwise the standalone mode samples its own.
        self._rng = random.Random()
        self.words = (list(words) if words else
                      sample_words(app, config, config.words_per_round,
                                   rng=self._rng))
        self.engine = RecallEngine(self.words)
        self.idx = 0
        self.attempts = 0
        self._advancing = False
        tts_ok = bool(getattr(app.audio.speech, "available", False)
                      and not app.audio.muted)
        want = getattr(config, "recall_prompt", "mixed")
        self.modes = [prompt_for(i, want, tts_ok)
                      for i in range(len(self.words))]
        # Extra readings for multiple-choice distractors.
        self._distractors = list(self.words)
        if want == "choice":
            try:
                self._distractors += sample_words(app, config, 24,
                                                  rng=self._rng)
            except Exception:
                pass
        if not self.words:
            app.go_home()
            return
        # Study-first pass (standalone, optional): see the words once
        # before being quizzed on them.
        if words is None and getattr(config, "recall_preview", False):
            self._show_preview()
        else:
            self._show_word()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=[dp(16), dp(10)],
                         spacing=dp(8))
        top = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(32))
        back = ThemedButton(text="←", size_hint=(None, None),
                            size=(dp(44), dp(32)), font_size=sp(15))
        back.bind(on_release=lambda *_: self._quit())
        self._title = JPLabel(text=tr("RECALL_TITLE"), bold=True,
                              color=rgba(theme.MUTED), font_size=sp(14),
                              halign="center", valign="middle")
        self._title.bind(size=self._title.setter("text_size"))
        self._progress = JPLabel(text="", color=rgba(theme.DIM),
                                 font_size=sp(12), halign="right",
                                 valign="middle", size_hint_x=None,
                                 width=dp(60))
        self._progress.bind(size=self._progress.setter("text_size"))
        top.add_widget(back)
        top.add_widget(self._title)
        top.add_widget(self._progress)
        root.add_widget(top)

        # TOP-ANCHORED: prompt → kana preview → input, packed together in
        # the upper half so the soft keyboard (which overlays the bottom on
        # devices that ignore resize mode) can never hide any of it.
        # The prompt is a button: tapping ♪ (or the kanji, in 'both' mode)
        # replays the audio — phones have no F1.
        self._kanji = _PromptLabel(self._replay, text="", bold=True,
                                   font_size=sp(46), size_hint_y=None,
                                   height=dp(72))
        root.add_widget(self._kanji)
        # Short windows (phone landscape): shrink the prompt and drop the
        # hint so the header row never gets pushed off the top.
        self.bind(height=self._adapt_height)
        self._meaning = JPLabel(text="", color=rgba(theme.MUTED),
                                font_size=sp(14), halign="center",
                                size_hint_y=None)
        self._meaning.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(4)))
        root.add_widget(self._meaning)
        self._preview = JPLabel(text="", color=rgba(theme.ACCENT),
                                font_size=sp(22), size_hint_y=None,
                                height=dp(34))
        root.add_widget(self._preview)

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(48))
        self._input = TextInput(multiline=False, font_name=UI_FONT,
                                font_size=sp(18),
                                background_color=rgba(theme.PANEL),
                                foreground_color=rgba(theme.TEXT),
                                cursor_color=rgba(theme.ACCENT),
                                padding=[dp(10), dp(12)])
        self._input.bind(text=lambda w, v: self._on_type(v))
        self._input.bind(on_text_validate=lambda *_: self._submit())
        submit = ThemedButton(text="→", fill=theme.ACCENT, bold=True,
                              size_hint=(None, None), size=(dp(64), dp(48)),
                              font_size=sp(20))
        submit.bind(on_release=lambda *_: self._submit())
        row.add_widget(self._input)
        row.add_widget(submit)
        self._input_row = row
        root.add_widget(row)

        # Multiple-choice options (mode 'choice'): 2×2 tap grid.
        from kivy.uix.gridlayout import GridLayout
        self._choices = GridLayout(cols=2, spacing=dp(8), size_hint_y=None,
                                   height=0)
        root.add_widget(self._choices)

        self._feedback = JPLabel(text="", bold=True, font_size=sp(18),
                                 color=rgba(theme.SUCCESS),
                                 size_hint_y=None, height=dp(30))
        root.add_widget(self._feedback)
        self._hint = JPLabel(text=tr("RECALL_HINT"), color=rgba(theme.DIM),
                             font_size=sp(10.5), size_hint_y=None,
                             height=dp(18))
        root.add_widget(self._hint)
        root.add_widget(Widget())   # all free space BELOW the content
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    @property
    def word(self):
        return self.words[self.idx] if self.idx < len(self.words) else None

    @property
    def mode(self) -> str:
        return (self.modes[self.idx]
                if self.idx < len(self.modes) else "typed")

    def _show_preview(self) -> None:
        """Study-first: list every word (reading + meaning), then start."""
        from kivy.uix.floatlayout import FloatLayout
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.scrollview import ScrollView
        panel = Panel(orientation="vertical", padding=dp(16), spacing=dp(8),
                      size_hint=(0.92, 0.8),
                      pos_hint={"center_x": 0.5, "center_y": 0.52})
        panel.add_widget(JPLabel(text=tr("RECALL_PREVIEW_TITLE"), bold=True,
                                 font_size=sp(17), size_hint_y=None,
                                 height=dp(30)))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        loc = self._app.state.locale
        for w in self.words:
            reading = "・".join(acceptable_readings(w.reading))
            row = JPLabel(
                text=f"{w.expression}   {reading}\n"
                     f"{w.get_meaning(loc)[:60]}",
                halign="left", font_size=sp(14), size_hint_y=None)
            row.bind(width=lambda l, v: setattr(l, "text_size", (v, None)),
                     texture_size=lambda l, ts: setattr(l, "height",
                                                        ts[1] + dp(8)))
            grid.add_widget(row)
        scroll.add_widget(grid)
        panel.add_widget(scroll)
        start = ThemedButton(text=tr("RECALL_START"), fill=theme.SUCCESS,
                             bold=True, height=dp(48))
        panel.add_widget(start)
        holder = FloatLayout()
        holder.add_widget(panel)
        self._overlay = holder
        self.add_widget(holder)

        def go(*_):
            self._clear_overlay()
            self._show_word()
        start.bind(on_release=go)

    def _show_word(self) -> None:
        w = self.word
        if w is None:
            self._finish()
            return
        self.attempts = 0
        self._advancing = False
        self._choices.clear_widgets()
        self._choices.height = 0
        self._input_row.opacity = 1
        self._input_row.disabled = False
        if self.mode == "choice":
            # Pick the reading among lookalikes — no typing.
            self._kanji.text = w.expression
            self._title.text = tr("RECALL_TITLE")
            self._meaning.text = w.get_meaning(self._app.state.locale)
            self._input_row.opacity = 0
            self._input_row.disabled = True
            opts = choice_options(w, self._distractors, self._rng)
            rows = (len(opts) + 1) // 2
            self._choices.height = rows * dp(48) + (rows - 1) * dp(8)
            for r in opts:
                b = ThemedButton(text=r, font_size=sp(16), height=dp(48))
                b.bind(on_release=lambda btn, rr=r: self._choice_pick(rr, btn))
                self._choices.add_widget(b)
            self._kanji.color = rgba(theme.TEXT)
            self._progress.text = f"{self.idx + 1} / {len(self.words)}"
            self._feedback.text = ""
            self._input.text = ""
            self._preview.text = ""
            self._hint.text = ""      # the typing hint is wrong here
            return
        self._hint.text = tr("RECALL_HINT")
        if self.mode == "listen":
            self._kanji.text = "♪"
            self._title.text = tr("RECALL_LISTEN_TITLE")
            self._meaning.text = tr("RECALL_LISTEN_HINT")
            self._app.audio.speech.say_jp(w.reading)
        elif self.mode == "both":
            # See it AND hear it; tapping the kanji replays.
            self._kanji.text = w.expression
            self._title.text = tr("RECALL_TITLE")
            self._meaning.text = w.get_meaning(self._app.state.locale)
            self._app.audio.speech.say_jp(w.reading)
        else:
            self._kanji.text = w.expression
            self._title.text = tr("RECALL_TITLE")
            self._meaning.text = w.get_meaning(self._app.state.locale)
        self._kanji.color = rgba(theme.TEXT)
        self._progress.text = f"{self.idx + 1} / {len(self.words)}"
        self._feedback.text = ""
        self._input.text = ""
        self._preview.text = ""
        self._input.focus = True

    def _adapt_height(self, *_) -> None:
        short = self.height < dp(480)
        self._kanji.height = dp(48) if short else dp(72)
        self._kanji.font_size = sp(30) if short else sp(46)
        self._hint.height = 0 if short else dp(18)
        self._hint.opacity = 0 if short else 1

    def _replay(self) -> None:
        if self.mode in ("listen", "both") and self.word is not None:
            self._app.audio.speech.say_jp(self.word.reading)

    def _on_type(self, text: str) -> None:
        self._preview.text = romaji_to_hira(text) if text else ""

    def _choice_pick(self, reading: str, btn=None) -> None:
        w = self.word
        if w is None or self._advancing:
            return
        if is_correct_reading(reading, w.reading):
            self._grade_correct(w)
        else:
            if btn is not None:
                btn.disabled = True   # the retry is a real second decision
            self._grade_wrong(w)

    def _submit(self) -> None:
        w = self.word
        if w is None or self._advancing:
            return
        answer = romaji_to_hira(self._input.text)
        if not answer:
            return
        # ~20 words carry alternative readings ("なん; なに") — any counts.
        if is_correct_reading(answer, w.reading):
            self._grade_correct(w)
        else:
            self._grade_wrong(w)

    def _grade_correct(self, w) -> None:
        first_try = self.attempts == 0
        try:
            self._app.stats.recalled(w, first_try=first_try)
        except Exception:
            pass
        self.engine.record(recalled=True, first_try=first_try)
        self._app.audio.sfx.play("match_hi" if first_try else "match")
        if self._app.state.tts_on_match:
            self._app.audio.speech.say_jp(w.reading)
        self._feedback.color = rgba(theme.SUCCESS)
        self._feedback.text = w.reading + "  ○"
        if self.mode == "listen":
            self._kanji.text = w.expression
        self._advance_after(0.7)

    def _grade_wrong(self, w) -> None:
        self.attempts += 1
        self._app.audio.sfx.play("mismatch")
        if self.attempts >= MAX_ATTEMPTS:
            try:
                self._app.stats.confused(w, w, "reading")
            except Exception:
                pass
            self.engine.record(recalled=False, first_try=False)
            self._feedback.color = rgba(theme.DANGER)
            self._feedback.text = tr("RECALL_ANSWER", reading=w.reading)
            if self.mode == "listen":
                self._kanji.text = w.expression
            if self._app.state.tts_on_mismatch:
                self._app.audio.speech.say_jp(w.reading)
            self._advance_after(1.6)
        else:
            self._feedback.color = rgba(theme.GOLD)
            self._feedback.text = tr("RECALL_TRY_AGAIN")
            if self.mode == "listen":
                self._app.audio.speech.say_jp(w.reading)
            self._input.text = ""
            self._preview.text = ""

    def _advance_after(self, delay: float) -> None:
        """Advance after *delay* — but never while audio is still playing.

        The next word's prompt purges the current utterance (that's right
        for user actions, wrong here): wait for the full playback, then
        half a second more, in every mode. Capped so a stuck TTS engine
        can never freeze the drill.
        """
        self._advancing = True
        self._speech_wait = 0.0

        def nxt(*_):
            self.idx += 1
            self._show_word()

        def poll(_dt):
            self._speech_wait += 0.15
            if (self._app.audio.speech.is_speaking()
                    and self._speech_wait < 8.0):
                Clock.schedule_once(poll, 0.15)
            else:
                Clock.schedule_once(nxt, 0.5)

        Clock.schedule_once(poll, delay)

    # ------------------------------------------------------------------ #
    def _finish(self) -> None:
        e = self.engine
        if not e.rounds_completed:
            self._quit()
            return
        self._app.state.record_score(self.config.name, e.score)
        try:
            keys = [(w.expression, w.reading) for w in e.seen_words]
            self._app.stats.log_game(self.config.name, e.score, e.matches,
                                     e.mistakes, keys[:80])
        except Exception:
            pass
        self._show_results()

    def _show_results(self) -> None:
        from kivy.uix.floatlayout import FloatLayout
        e = self.engine
        panel = Panel(orientation="vertical", padding=dp(18), spacing=dp(10),
                      size_hint=(None, None), width=dp(300))
        panel.add_widget(JPLabel(text=tr("RESULTS_OVER"), bold=True,
                                 font_size=sp(24), size_hint_y=None,
                                 height=dp(40)))
        body = JPLabel(
            text=(f"{e.score:,}\n"
                  f"{tr('STAT_BEST_COMBO')}  ×{e.best_combo}\n"
                  f"{tr('STAT_ACCURACY')}  {int(e.accuracy * 100)}%\n"
                  f"{tr('STAT_LEARNED')}  {e.words_learned}"),
            color=rgba(theme.MUTED), font_size=sp(16), halign="center",
            size_hint_y=None)
        body.bind(texture_size=lambda w, ts: setattr(w, "height",
                                                     ts[1] + dp(8)))
        panel.add_widget(body)
        again = ThemedButton(text=tr("BTN_AGAIN"), fill=theme.ACCENT)
        again.bind(on_release=lambda *_: self.start(self._app, self.config))
        panel.add_widget(again)
        home = ThemedButton(text=tr("BTN_MENU"))
        home.bind(on_release=lambda *_: self._quit())
        panel.add_widget(home)
        panel.bind(minimum_height=panel.setter("height"))
        holder = FloatLayout()
        panel.pos_hint = {"center_x": 0.5, "center_y": 0.55}
        holder.add_widget(panel)
        self._overlay = holder
        self.add_widget(holder)

    def _clear_overlay(self) -> None:
        if self._overlay is not None:
            self.remove_widget(self._overlay)
            self._overlay = None

    def _quit(self) -> None:
        self._input.focus = False
        self._clear_overlay()
        self._app.go_home()
