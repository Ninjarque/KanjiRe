"""Reading Room on Kivy: i+1 sentences with the curriculum dials.

All selection/ordering logic lives in the shared
:class:`kanjire.data.reading_session.ReadingSession`; this screen renders it
for touch: dials as chips, the sentence big and wrapped, tappable word chips
(gold = the new word), a word popup with +learn, and reading totals.
"""
from __future__ import annotations

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.widget import Widget

from kanjire.data import kanjidata
from kanjire.data.reading_session import (
    DIFFICULTY_OPTIONS,
    NEW_WORD_OPTIONS,
    ReadingSession,
)
from kanjire.i18n import tr
from kanjire.jputil import has_kanji
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import (
    Chip,
    ChipRow,
    JPLabel,
    Panel,
    SectionLabel,
    ThemedButton,
)


class ReadingScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self.session = None
        self._translation_shown = False
        self._build()

    def on_pre_enter(self, *_):
        if self.session is None:
            self.session = ReadingSession(self._app.con, self._app.stats,
                                          self._app.state)
            self.session.advance(log=False, reason="open-tab")
        self._show_current()
        self._update_totals()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        root.add_widget(JPLabel(text=tr("READ_TITLE"), bold=True,
                                font_size=sp(20), size_hint_y=None,
                                height=dp(30)))
        self._totals = JPLabel(text="", color=rgba(theme.MUTED),
                               font_size=sp(12), halign="left",
                               size_hint_y=None, height=dp(20))
        self._totals.bind(size=self._totals.setter("text_size"))
        root.add_widget(self._totals)

        state = self._app.state
        new_now = int(state.setting("read_new_words", "1") or 1)
        if new_now not in {v for v, _ in NEW_WORD_OPTIONS}:
            new_now = 1
        diff_now = state.setting("read_difficulty", "comfortable")
        if diff_now not in {v for v, _ in DIFFICULTY_OPTIONS}:
            diff_now = "comfortable"
        root.add_widget(SectionLabel(text=tr("READ_NEW_LABEL")))
        self._new_chips = ChipRow(
            [(v, tr(k)) for v, k in NEW_WORD_OPTIONS], new_now,
            on_change=lambda v: self._dial(lambda s: s.set_new_words(v)))
        root.add_widget(self._new_chips)
        root.add_widget(SectionLabel(text=tr("READ_DIFF_LABEL")))
        self._diff_chips = ChipRow(
            [(v, tr(k)) for v, k in DIFFICULTY_OPTIONS], diff_now,
            on_change=lambda v: self._dial(lambda s: s.set_difficulty(v)))
        root.add_widget(self._diff_chips)

        # Sentence panel
        panel = Panel(orientation="vertical", padding=dp(14), spacing=dp(8))
        self._sentence = JPLabel(text="", font_size=sp(22), halign="center",
                                 valign="middle")
        self._sentence.bind(
            size=lambda w, v: setattr(w, "text_size", (v[0] - dp(8), None)))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        scroll.add_widget(self._sentence)
        self._sentence.size_hint_y = None
        self._sentence.bind(texture_size=lambda w, ts: setattr(
            w, "height", max(ts[1] + dp(8), dp(60))))
        panel.add_widget(scroll)

        self._note = JPLabel(text="", color=rgba(theme.MUTED),
                             font_size=sp(12), halign="center",
                             size_hint_y=None, height=dp(20))
        self._note.bind(size=self._note.setter("text_size"))
        panel.add_widget(self._note)

        self._chips_box = StackLayout(orientation="lr-tb", spacing=dp(6),
                                      size_hint_y=None)
        self._chips_box.bind(
            minimum_height=self._chips_box.setter("height"))
        panel.add_widget(self._chips_box)

        self._translation = JPLabel(text="", color=rgba(theme.ACCENT),
                                    font_size=sp(14), halign="center",
                                    size_hint_y=None, height=0)
        self._translation.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(
                w, "height", ts[1] + dp(4) if w.text else 0))
        panel.add_widget(self._translation)
        root.add_widget(panel)

        self._empty = JPLabel(text="", color=rgba(theme.DIM),
                              font_size=sp(13), halign="center",
                              size_hint_y=None, height=0)
        self._empty.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        root.add_widget(self._empty)

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(50))
        # Hear the sentence: the app already speaks words on match, and a
        # reading drill is exactly where hearing the whole line helps.
        self._speak_btn = ThemedButton(text=tr("READ_SPEAK"),
                                       font_size=sp(13), height=dp(50),
                                       size_hint_x=None, width=dp(64))
        self._speak_btn.bind(on_release=lambda *_: self._speak())
        row.add_widget(self._speak_btn)
        self._trans_btn = ThemedButton(text=tr("READ_TRANSLATE"),
                                       font_size=sp(13), height=dp(50))
        self._trans_btn.bind(on_release=lambda *_: self._toggle_translation())
        self._next_btn = ThemedButton(text=tr("READ_NEXT"),
                                      fill=theme.ACCENT, bold=True,
                                      height=dp(50))
        self._next_btn.bind(on_release=lambda *_: self._next())
        row.add_widget(self._trans_btn)
        row.add_widget(self._next_btn)
        root.add_widget(row)
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    def _dial(self, apply) -> None:
        if self.session is None:
            return
        apply(self.session)
        self._show_current()

    def _next(self) -> None:
        if self.session is None:
            return
        self.session.advance(log=True, reason="next-button")
        self._update_totals()
        self._show_current()

    def _speak(self) -> None:
        """Read the current sentence aloud (no-op without a JP voice)."""
        cur = self.session.current if self.session else None
        if not cur:
            return
        try:
            self._app.audio.speech.say_jp(cur["ja"])
        except Exception:      # noqa: BLE001 — TTS is a bonus, never fatal
            pass

    def _toggle_translation(self) -> None:
        cur = self.session.current if self.session else None
        if cur is None or not cur.get("en"):
            return
        self._translation_shown = not self._translation_shown
        self._translation.text = cur["en"] if self._translation_shown else ""

    def _show_current(self) -> None:
        self._translation_shown = False
        self._translation.text = ""
        self._chips_box.clear_widgets()
        cur = self.session.current if self.session else None
        if cur is None:
            self._sentence.text = ""
            self._note.text = ""
            self._empty.text = tr("READ_EMPTY")
            self._empty.height = dp(60)
            self._next_btn.disabled = True
            self._trans_btn.disabled = True
            self._speak_btn.disabled = True
            return
        self._empty.text = ""
        self._empty.height = 0
        self._next_btn.disabled = False
        self._speak_btn.disabled = False
        self._trans_btn.disabled = not bool(cur.get("en"))
        self._sentence.text = cur["ja"]
        note = (tr("READ_ALL_KNOWN") if cur["unknown"] == 0
                else tr("READ_ONE_NEW", n=cur["unknown"]))
        lvl = self.session.level_tag()
        if lvl is not None:
            note = f"{note}   ·   {tr('READ_LEVEL_TAG', lvl=lvl)}"
        self._note.text = note

        for head, reading, _good in self.session.current_words():
            if not has_kanji(head):
                continue
            known = self.session.is_known(head)
            chip = Chip(head, text=head, size_hint=(None, None),
                        width=max(dp(52), dp(18) * (len(head) + 1)),
                        height=dp(34), font_size=sp(14))
            chip.set_fill(theme.SUCCESS if known else theme.GOLD)
            chip.bind(on_release=lambda w, h=head, r=reading:
                      self._open_popup(h, r))
            self._chips_box.add_widget(chip)

    def _update_totals(self) -> None:
        t = self._app.stats.reading_totals()
        self._totals.text = tr("READ_TOTALS", sentences=t["sentences"],
                               chars=t["chars"])

    # ------------------------------------------------------------------ #
    def _open_popup(self, head: str, reading: str | None) -> None:
        info = self.session.vocab_word(head, reading)
        reading_txt = (info or {}).get("reading") or reading or ""
        meaning = (info or {}).get("meaning") or "?"
        try:
            accent = kanjidata.pitch_of(head, reading_txt)
        except Exception:
            accent = None

        view = ModalView(size_hint=(0.86, None), height=dp(230),
                         overlay_color=rgba(theme.BG, 0.6),
                         background_color=(0, 0, 0, 0))
        panel = Panel(orientation="vertical", padding=dp(16), spacing=dp(6))
        panel.add_widget(JPLabel(text=head, bold=True, font_size=sp(26),
                                 size_hint_y=None, height=dp(40)))
        panel.add_widget(JPLabel(
            text=reading_txt + (f"  [{accent}]" if accent else ""),
            color=rgba(theme.ACCENT), font_size=sp(15),
            size_hint_y=None, height=dp(24)))
        m = JPLabel(text=meaning, color=rgba(theme.MUTED), font_size=sp(13),
                    halign="center", size_hint_y=None)
        m.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
               texture_size=lambda w, ts: setattr(w, "height",
                                                  ts[1] + dp(6)))
        panel.add_widget(m)
        panel.add_widget(Widget())
        if info and not self.session.is_known(head):
            learn = ThemedButton(text=tr("READ_LEARN"), fill=theme.GOLD,
                                 height=dp(44), font_size=sp(14))

            def _learn(*_):
                self.session.enqueue_learn(head, reading_txt)
                view.dismiss()

            learn.bind(on_release=_learn)
            panel.add_widget(learn)
        view.add_widget(panel)
        view.open()
