"""The diglot-weave reader (Kivy): your own text, mostly in Japanese.

Two views share this widget:

* the **library** — passages you added, each with a progress bar showing how
  far through it you are, resumable and deletable;
* the **reader** — the passage itself, where tapping a word you don't know
  opens it up and switches its later appearances to English for a while.

The model lives in :mod:`kanjire.data.weave` and :mod:`kanjire.data.library`;
this file is only presentation, so the pyglet reader can reuse all of it.
"""
from __future__ import annotations

from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.textinput import TextInput

from kanjire.data.library import Library
from kanjire.data.weave import Lexicon, clipboard_text, describe
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, SectionLabel, ThemedButton

#: Sentences rendered at once. A novel is windowed — 40 sentences is already
#: several screens of scrolling, and every token is its own widget.
WINDOW = 40


class WeaveWord(ButtonBehavior, JPLabel):
    """One token. Tappable only when we know which word it is."""

    def __init__(self, token, english: bool, tappable: bool, **kw):
        kw.setdefault("font_size", sp(19))
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.token = token
        self.english = english
        # English stand-ins read in the accent colour so you can see at a
        # glance how much of the page is still on training wheels.
        if english:
            self.text = f" {token.meaning.split(',')[0].split(';')[0]} "
            self.color = rgba(theme.ACCENT)
        else:
            self.text = token.text
            self.color = rgba(theme.TEXT if tappable else theme.MUTED)
        self.bind(texture_size=self._fit)
        self._fit()

    def _fit(self, *_):
        self.size = (self.texture_size[0], max(dp(30), self.texture_size[1]))


class WeaveView(BoxLayout):
    """Library + reader, swapped in place."""

    def __init__(self, app, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self._app = app
        self.library = Library(app.stats.con)
        self._lex = None
        self.book = None            #: the open book, or None on the library
        self._weave = None
        self._pos = 0
        self.show_library()

    # ---- lazily built, because it reads the whole vocabulary ------------ #
    def lexicon(self) -> Lexicon:
        if self._lex is None:
            self._lex = Lexicon(self._app.con)
        return self._lex

    # ---- library ------------------------------------------------------- #
    def show_library(self) -> None:
        self.book = None
        self.clear_widgets()
        self.add_widget(SectionLabel(text=tr("LIB_TITLE")))
        add = ThemedButton(text=tr("LIB_ADD"), fill=theme.PANEL_HI,
                           text_color=theme.SUCCESS, height=dp(44))
        add.bind(on_release=lambda *_: self._add_text())
        self.add_widget(add)

        books = self.library.books()
        if not books:
            hint = JPLabel(text=tr("LIB_EMPTY"), color=rgba(theme.DIM),
                           font_size=sp(12), size_hint_y=None, height=dp(40))
            hint.bind(size=hint.setter("text_size"))
            self.add_widget(hint)
            return

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                          padding=[0, 0, 0, dp(8)])
        body.bind(minimum_height=body.setter("height"))
        for book in books:
            row = BoxLayout(orientation="horizontal", spacing=dp(6),
                            size_hint_y=None, height=dp(52))
            pct = int(round(book["ratio"] * 100))
            btn = ThemedButton(
                text=f"{book['title']}   {pct}%", font_size=sp(14),
                height=dp(52), fill=theme.PANEL_HI,
                text_color=theme.readable_on(theme.PANEL_HI))
            # The same progress fill the genre tiles use: a bar beats a
            # colour, because "started" and "finished" are not the same.
            btn.set_progress(book["ratio"],
                             theme.SUCCESS if book["done"] else theme.GOLD)
            btn.bind(on_release=lambda w, b=book["id"]: self.open_book(b))
            row.add_widget(btn)
            # Plain ASCII "x", like the friends list: the bundled JP fonts
            # have no ✕ (U+2715) and it would be a tofu box on Linux.
            rm = ThemedButton(text="x", font_size=sp(16), width=dp(46),
                              height=dp(52), size_hint_x=None,
                              fill=theme.PANEL_HI, text_color=theme.DANGER)
            rm.bind(on_release=lambda w, b=book: self._confirm_delete(b))
            row.add_widget(rm)
            body.add_widget(row)
        scroll.add_widget(body)
        self.add_widget(scroll)

    def _field(self, hint: str, *, multiline: bool, height: float):
        return TextInput(
            hint_text=hint, multiline=multiline, font_name=UI_FONT,
            font_size=sp(15), size_hint_y=None, height=height,
            background_color=rgba(theme.PANEL_HI),
            foreground_color=rgba(theme.TEXT),
            hint_text_color=rgba(theme.DIM),
            cursor_color=rgba(theme.ACCENT), padding=[dp(10), dp(10)])

    def _add_text(self) -> None:
        """A whole screen for adding a passage, not a modal.

        A chapter does not fit in a popup, and the popup's own paste handler
        silently dropped everything for text with Windows line endings — so
        pasting goes through our own button, and the counts below the box
        prove the text actually arrived.
        """
        self.book = None
        self.clear_widgets()
        self.add_widget(SectionLabel(text=tr("LIB_ADD_TITLE")))
        title = self._field(tr("LIB_ASK_TITLE"), multiline=False,
                            height=dp(44))
        title.text = tr("LIB_DEFAULT_TITLE")
        self.add_widget(title)

        body = self._field(tr("LIB_ASK_TEXT"), multiline=True, height=dp(240))
        self.add_widget(body)

        counts = JPLabel(text=tr("LIB_COUNTS", chars=0, sentences=0),
                         color=rgba(theme.DIM), font_size=sp(12),
                         size_hint_y=None, height=dp(20))
        counts.bind(size=counts.setter("text_size"))

        def _recount(*_):
            chars, sentences = describe(body.text)
            counts.text = tr("LIB_COUNTS", chars=chars, sentences=sentences)

        body.bind(text=_recount)

        paste = ThemedButton(text=tr("LIB_PASTE"), font_size=sp(14),
                             height=dp(46), fill=theme.PANEL_HI,
                             text_color=theme.ACCENT)

        def _paste(*_):
            got = clipboard_text()
            if got:
                body.text = (body.text + got) if body.text else got
            _recount()

        paste.bind(on_release=_paste)
        self.add_widget(paste)
        self.add_widget(counts)

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        size_hint_y=None, height=dp(48))
        cancel = ThemedButton(text=tr("DLG_CANCEL"), font_size=sp(14),
                              height=dp(48))
        cancel.bind(on_release=lambda *_: self.show_library())
        save = ThemedButton(text=tr("DLG_OK"), fill=theme.ACCENT,
                            font_size=sp(14), height=dp(48))

        def _save(*_):
            book_id = self.library.add(title.text, body.text)
            if book_id is None:
                self._app.info(tr("LIB_NOTHING"))
                return
            self.open_book(book_id)

        save.bind(on_release=_save)
        row.add_widget(cancel)
        row.add_widget(save)
        self.add_widget(row)

    def _confirm_delete(self, book) -> None:
        def apply() -> None:
            self.library.delete(book["id"])
            self.show_library()

        self._app.confirm(tr("LIB_DELETE_MSG", name=book["title"]), apply,
                          danger=True)

    # ---- reader -------------------------------------------------------- #
    def open_book(self, book_id: int) -> None:
        book = self.library.get(book_id)
        if book is None:
            self.show_library()
            return
        self.book = book
        self._weave = self.library.load_weave(book_id)
        self._pos = int(book["position"] or 0)
        if self._pos >= (book["n_sentences"] or 0):
            self._pos = 0           # finished: start it again rather than end
        self._render()

    def _stats_row(self, token):
        try:
            return self._app.stats.get_for(token.expression, token.reading)
        except Exception:           # noqa: BLE001
            return None

    def _render(self) -> None:
        self.clear_widgets()
        book = self.book
        head = BoxLayout(orientation="horizontal", spacing=dp(6),
                         size_hint_y=None, height=dp(40))
        back = ThemedButton(text=tr("LIB_BACK"), font_size=sp(13),
                            height=dp(40), width=dp(96), size_hint_x=None,
                            fill=theme.PANEL_HI, text_color=theme.MUTED)
        back.bind(on_release=lambda *_: self._leave())
        head.add_widget(back)
        total = book["n_sentences"] or 1
        title = JPLabel(
            text=f"{book['title']}   {int(self._pos / total * 100)}%",
            font_size=sp(13), color=rgba(theme.MUTED))
        title.bind(size=title.setter("text_size"))
        head.add_widget(title)
        self.add_widget(head)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        flow = StackLayout(orientation="lr-tb", spacing=dp(2),
                           size_hint_y=None, padding=[0, dp(4)])
        flow.bind(minimum_height=flow.setter("height"))
        lex = self.lexicon()
        sentences = self.library.sentences(book["id"], self._pos, WINDOW)
        for sentence in sentences:
            for token in lex.tokenize(sentence):
                row = self._stats_row(token) if token.known_word else None
                english = self._weave.show_english(token, row)
                w = WeaveWord(token, english, token.known_word)
                if token.known_word:
                    w.bind(on_release=lambda _w, t=token: self._tap(t))
                flow.add_widget(w)
                if english or self._weave.holds.get(token.key):
                    # Each appearance spends one of the word's crutch turns.
                    self._weave.consume(token)
        scroll.add_widget(flow)
        self.add_widget(scroll)

        foot = BoxLayout(orientation="horizontal", spacing=dp(8),
                         size_hint_y=None, height=dp(48))
        prev = ThemedButton(text=tr("LIB_PREV"), font_size=sp(14),
                            height=dp(48))
        prev.bind(on_release=lambda *_: self._page(-WINDOW))
        nxt = ThemedButton(text=tr("LIB_NEXT"), fill=theme.ACCENT,
                           font_size=sp(14), height=dp(48))
        nxt.bind(on_release=lambda *_: self._page(WINDOW))
        prev.disabled = self._pos <= 0
        nxt.disabled = self._pos + WINDOW >= total
        foot.add_widget(prev)
        foot.add_widget(nxt)
        self.add_widget(foot)

    def _page(self, delta: int) -> None:
        total = self.book["n_sentences"] or 0
        self._pos = max(0, min(max(0, total - 1), self._pos + delta))
        self._save()
        self._render()

    def _save(self) -> None:
        if self.book is not None:
            self.library.save_position(self.book["id"], self._pos,
                                       weave=self._weave)

    def _leave(self) -> None:
        self._save()
        self.show_library()

    def _tap(self, token) -> None:
        """Reveal a word — and let that count as evidence everywhere."""
        row = self._stats_row(token)
        self._weave.tapped(token, row)
        try:
            self._app.stats.reading_lookup(token.expression, token.reading,
                                           token.meaning)
        except Exception:           # noqa: BLE001
            pass
        try:
            if self._app.state.tts_on_select:
                self._app.audio.speech.say_jp(token.reading or token.expression)
        except Exception:           # noqa: BLE001
            pass
        self._app.info(
            f"{token.expression}  ({token.reading})\n{token.meaning}")
        self._save()
        self._render()
