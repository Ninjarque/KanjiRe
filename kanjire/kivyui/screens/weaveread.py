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

from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from kanjire.data.library import Library
from kanjire.data.paginate import (english_of, page_of_sentence,
                                   paginate)
from kanjire.data.weave import (FONT_SIZES, Lexicon, clipboard_text,
                                describe, font_size_of,
                                sentence_font, stamp_slots)
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, SectionLabel, ThemedButton

#: Sentences rendered at once. A novel is windowed — 40 sentences is already
#: several screens of scrolling, and every token is its own widget.
WINDOW = 40


class WeaveWord(ButtonBehavior, JPLabel):
    """One token. Tappable only when we know which word it is."""

    def __init__(self, token, english: bool, tappable: bool,
                 vertical: bool = False, **kw):
        kw.setdefault("font_size", sp(19))
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.token = token
        self.vertical = vertical      # set before _fit(), which reads it
        self.english = english
        # English stand-ins read in the accent colour so you can see at a
        # glance how much of the page is still on training wheels.
        self.tappable = tappable
        if vertical:
            self.halign = "center"
        self.set_state(english)
        self.bind(texture_size=self._fit)
        self._fit()

    def set_state(self, english: bool) -> None:
        """Repaint in place — cheaper than rebuilding the page after a tap."""
        self.english = english
        if english:
            gloss = english_of(self.token)
            # Vertical Japanese stacks one character per cell; a gloss does
            # the same rather than being rotated, so a column keeps its width
            # and the measurement stays a simple character count.
            self.text = ("\n".join(gloss.strip()) if self.vertical
                         else gloss)
            self.color = rgba(theme.ACCENT)
        else:
            self.text = ("\n".join(self.token.text) if self.vertical
                         else self.token.text)
            # Clear contrast: a word you CAN tap is full-strength text, the
            # rest is clearly dimmer. The old pair was so close together that
            # tappable words were impossible to pick out.
            self.color = rgba(theme.TEXT if self.tappable else theme.DIM)
        self.bold = self.tappable and not english

    def _fit(self, *_):
        # Horizontal keeps a dp(30) floor so a one-character word is still a
        # usable tap target. Vertical must NOT: the column advance is measured
        # per character, and a floor taller than that measurement is exactly
        # how columns used to run off the bottom of the page.
        h = self.texture_size[1]
        self.size = (self.texture_size[0],
                     h if self.vertical else max(dp(30), h))


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
        self._cache = None          #: (tokens, stats rows) for self._pos
        self._cache_pos = None
        self._words: list = []      #: the tappable widgets on screen
        self._show_opts = False     #: reader type controls, toggled by Aa
        self._page_cache = None     #: (pages, stats rows) for the current key
        self._page_key = None
        self._metrics_cache: dict = {}
        self._page_index = 0        #: which measured page is on screen
        self._page_count = 0
        # The page box PERSISTS across renders. Pagination needs the real
        # size of the area it will draw into, and only a laid-out widget
        # knows that — arithmetic over the chrome heights was wrong at every
        # geometry and collapsed to nothing in landscape with options open.
        self._box = BoxLayout(orientation="vertical")
        self._box.bind(size=self._on_box_size)
        self._filled_for = None
        self._title_lbl = None
        self._foot = ()
        self.show_library()

    # ---- pagination ----------------------------------------------------- #
    def vertical_mode(self) -> bool:
        return self._app.state.setting("read_orientation",
                                       "horizontal") == "vertical"

    def _line_metrics(self, size: float, face) -> tuple[float, float]:
        """(one line's thickness, one character's advance) as DRAWN.

        The paginator and the renderer must agree to the pixel, so both take
        their numbers from here. Measured from the real text engine rather
        than a guessed multiple of the font size: at size 15 the true line
        box is *taller* than 1.9x the font, which is what used to push the
        last lines of every small-text page off the bottom.
        """
        from kivy.core.text import Label as CoreLabel
        key = (round(size, 2), face)
        got = self._metrics_cache.get(key)
        if got is None:
            kw = {"font_name": face} if face else {}
            one = CoreLabel(text="\u3042", font_size=size, **kw)
            one.refresh()
            two = CoreLabel(text="\u3042\n\u3042", font_size=size, **kw)
            two.refresh()
            char_h = float(one.texture.size[1]) if one.texture else size * 1.2
            both_h = float(two.texture.size[1]) if two.texture else char_h * 2
            # The per-character advance inside a stacked column is the
            # DIFFERENCE, not the single-line height (which includes the
            # font's ascent/descent padding once).
            advance = max(1.0, both_h - char_h)
            got = (char_h, advance)
            self._metrics_cache[key] = got
        return got

    def _measurer(self, face, size):
        """A width function over the REAL text engine, memoised.

        Estimating from character counts would mis-page every proportional
        gloss; Kivy can tell us exactly, and a page is only a few hundred
        distinct strings.
        """
        from kivy.core.text import Label as CoreLabel
        cache: dict[str, float] = {}

        def measure(text: str) -> float:
            if not text:
                return 0.0
            got = cache.get(text)
            if got is None:
                lbl = CoreLabel(text=text, font_size=size,
                                **({"font_name": face} if face else {}))
                lbl.refresh()
                got = float(lbl.texture.size[0]) if lbl.texture \
                    else len(text) * size
                cache[text] = got
            return got

        return measure

    def _pages(self):
        """Pages for the current passage, font, size, orientation and screen.

        Rebuilt only when one of those changes — the key includes all of them,
        because every one of them moves the breaks.
        """
        size = font_size_of(self._app.state)
        varied = self._app.state.setting("read_fonts", "single") == "random"
        vertical = self.vertical_mode()
        area = self._page_area()
        key = (self.book["id"] if self.book else None, size, varied, vertical,
               round(area[0]), round(area[1]))
        if self._page_key == key and self._page_cache is not None:
            return self._page_cache

        groups, rows = self._page_tokens()
        avail_w, avail_h = area
        from kanjire.kivyui.fonts import jp_fonts
        faces = jp_fonts()
        # Random fonts change the face — and therefore the width of the same
        # word — PER SENTENCE, so each sentence is measured in its own face.
        def measure_for(index):
            if vertical:
                return len
            return self._measurer(self._face_for(faces, index, varied),
                                  sp(size))

        if vertical:
            # A column is a stack of character cells; a page is a row of
            # columns. Both numbers come from the renderer's own metrics.
            char_h, advance = self._line_metrics(
                sp(size), self._face_for(faces, 0, varied))
            pitch = self._column_pitch(size)
            # A stacked column of N characters is char_h + (N-1)*advance
            # tall: the first cell carries the font's ascent and descent.
            budget = 1 + max(0, int((avail_h - char_h) // advance))
            pages = paginate(groups, len,
                             main_axis=max(1, budget),
                             cross_axis=max(1, int(avail_w // pitch)),
                             line_extent=1, measure_for=measure_for,
                             split_units=True)
        else:
            face = self._face_for(faces, 0, varied)
            char_h, _adv = self._line_metrics(sp(size), face)
            # As drawn: each line is a StackLayout row (floored at dp(30) for
            # tap targets) plus the grid spacing between rows.
            line_h = max(dp(30), char_h) + dp(8)
            pages = paginate(groups, self._measurer(face, sp(size)),
                             main_axis=avail_w, cross_axis=avail_h,
                             line_extent=line_h, measure_for=measure_for)
        self._page_cache, self._page_key = (pages, rows), key
        return self._page_cache

    @staticmethod
    def _face_for(faces, index: int, varied: bool) -> str:
        """The face this sentence is really drawn in — never None.

        Measuring with a fallback face while drawing in the bundled Japanese
        one is a 36px-vs-44px error per character, which is precisely how
        vertical columns ran past the top of the page in landscape.
        """
        return sentence_font(faces, index, varied) or UI_FONT

    @staticmethod
    def _column_pitch(size: float) -> float:
        """Distance between vertical columns, as the renderer lays them out.

        Must match the holder width + the body spacing exactly: this single
        number being 1.15x the font instead of 1.6x plus spacing is what put
        vertical text hundreds of pixels past the right edge at every size.
        """
        return sp(size) * 1.6 + dp(6)

    def _page_area(self) -> tuple[float, float]:
        """The REAL page box, once it has been laid out.

        Falls back to an estimate only before the first layout pass; the
        estimate is never what we ship a page against, because the chrome
        (title row, options panel, footer) varies with the geometry.
        """
        w, h = self._box.size
        if w > dp(40) and h > dp(40):
            return (w - dp(4), h - dp(8))      # the grid's own padding
        w = max(dp(120), (self.width or dp(360)) - dp(8))
        h = max(dp(120), (self.height or dp(600)) - dp(104)
                - (self._opts_height() + dp(8) if self._show_opts else 0))
        return (w, h)

    def _opts_height(self) -> float:
        """The type-controls panel, capped so the page always survives it.

        A fixed dp(140) panel left a phone in landscape with a ZERO-height
        page — every word on it was out of bounds.
        """
        return min(dp(140), max(dp(60), (self.height or dp(600)) * 0.34))

    def _on_box_size(self, _box, size) -> None:
        """Fill (or re-fill) the page when its real size is known or changes.

        Rotation, the options panel and the first layout pass all arrive this
        way, and each of them moves the page breaks.
        """
        key = (round(size[0]), round(size[1]))
        if self.book is None or key == self._filled_for:
            return
        if size[0] < dp(40) or size[1] < dp(40):
            return
        self._filled_for = key
        self._page_cache = self._page_key = None
        self._fill_page()

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

        A chapter does not fit in a popup. The Paste button is a convenience
        beside the platform's own paste gesture (which works, and is the only
        one that can offer Android's clipboard history); the live counts
        underneath are the point — they show the text actually arrived.
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
        """Open a passage, warming the word index behind the spinner.

        Building the index reads the whole vocabulary and generates every
        inflection — quick on a computer, visible on a phone — so it happens
        once, behind the same loading ring the game launcher uses, instead of
        silently stalling the first tap.
        """
        if self._lex is None:
            self._app.loading.show()
            Clock.schedule_once(lambda _dt: self._open_now(book_id), 0)
            return
        self._open_now(book_id)

    def _open_now(self, book_id: int) -> None:
        try:
            self.lexicon()
        finally:
            self._app.loading.hide()
        book = self.library.get(book_id)
        if book is None:
            self.show_library()
            return
        self.book = book
        self._weave = self.library.load_weave(book_id)
        self._cache = self._cache_pos = None
        self._page_cache = self._page_key = None
        self._pos = int(book["position"] or 0)
        if self._pos >= (book["n_sentences"] or 0):
            self._pos = 0           # finished: start it again rather than end
        pages, _rows = self._pages()
        self._page_index = page_of_sentence(pages, self._pos)
        self._render()

    def _page_tokens(self):
        """Tokenise the page once and fetch every stats row in one query.

        Both used to happen per token, per render — and a tap re-rendered
        the page, so opening one word re-tokenised forty sentences and ran
        hundreds of queries. That is what made a tap feel unresponsive.
        """
        if self._cache is not None and self._cache_pos == self.book["id"]:
            return self._cache
        lex = self.lexicon()
        # Grouped BY SENTENCE: the reader puts each on its own line, which is
        # what makes a page followable instead of one running block.
        # The WHOLE passage: pagination decides what fits, so a fixed
        # forty-sentence window would cut pages off arbitrarily. Tokenising a
        # 2000-sentence chapter measures at 0.05s, so this is affordable.
        groups = stamp_slots(
            [lex.tokenize(sentence) for sentence in
             self.library.sentences(self.book["id"], 0,
                                    self.book["n_sentences"] or 1)])
        flat = [t for g in groups for t in g]
        try:
            rows = self._app.stats.rows_for(
                [t.key for t in flat if t.known_word])
        except Exception:           # noqa: BLE001
            rows = {}
        self._cache, self._cache_pos = (groups, rows), self.book["id"]
        return self._cache

    def _stats_row(self, token):
        _groups, rows = self._page_tokens()
        return rows.get(token.key)

    def _render(self) -> None:
        """Draw the open passage. A no-op with nothing open — opening is
        deferred a frame now (the spinner warms the word index), so callers
        can reach here before there is a book."""
        if self.book is None:
            self.show_library()
            return
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
        pages_n = max(1, self._page_count)
        title = JPLabel(
            text=f"{book['title']}   {int(self._pos / total * 100)}%   "
                 f"{self._page_index + 1}/{pages_n}",
            font_size=sp(13), color=rgba(theme.MUTED))
        title.bind(size=title.setter("text_size"))
        head.add_widget(title)
        opts = ThemedButton(text=tr("READ_SIZE_BTN"), font_size=sp(13),
                            height=dp(40), width=dp(52), size_hint_x=None,
                            fill=theme.PANEL_HI, text_color=theme.MUTED)

        def _toggle_opts(*_):
            self._show_opts = not self._show_opts
            self._render()

        opts.bind(on_release=_toggle_opts)
        head.add_widget(opts)
        self.add_widget(head)

        self._title_lbl = title
        if self._show_opts:
            self.add_widget(self._reader_options())
        # The page goes in the PERSISTENT box, so pagination can be done
        # against its real, laid-out size rather than a guess at the chrome.
        if self._box.parent is not None:
            self._box.parent.remove_widget(self._box)
        self.add_widget(self._box)
        self._filled_for = None
        self._fill_page()
        self._add_footer()

    def _set_title(self) -> None:
        if self._title_lbl is None or self.book is None:
            return
        total = self.book["n_sentences"] or 1
        self._title_lbl.text = (
            f"{self.book['title']}   {int(self._pos / total * 100)}%   "
            f"{self._page_index + 1}/{max(1, self._page_count)}")

    def _fill_page(self) -> None:
        """Build the page that fits the box we actually have."""
        if self.book is None:
            return
        self._box.clear_widgets()
        # A measured page: everything on it fits, so there is nothing to
        # scroll. Vertical lays columns out right-to-left, like a real book.
        pages, rows = self._pages()
        vertical = self.vertical_mode()
        self._page_count = len(pages)
        self._page_index = max(0, min(self._page_index, len(pages) - 1)) \
            if pages else 0
        self._words = []
        spent_here: set = set()     #: words that already paid on THIS page
        size = font_size_of(self._app.state)
        varied = self._app.state.setting("read_fonts", "single") == "random"
        from kanjire.kivyui.fonts import jp_fonts
        faces = jp_fonts()

        if vertical:
            body = BoxLayout(orientation="horizontal", spacing=dp(6))
            # Vertical text begins at the RIGHT edge, so the empty space goes
            # on the left. A BoxLayout fills in the order widgets are added,
            # so this spacer has to come first.
            body.add_widget(Widget())
        else:
            body = GridLayout(cols=1, spacing=dp(8), padding=[0, dp(4)])

        current = pages[self._page_index] if pages else None
        lines = list(current.lines) if current else []
        if vertical:
            lines.reverse()          # right-to-left: first column on the right
        for order, line in enumerate(lines):
            index = (len(lines) - 1 - order) if vertical else order
            face = self._face_for(faces, index, varied)
            if vertical:
                holder = BoxLayout(orientation="vertical",
                                   size_hint_x=None, width=sp(size) * 1.6)
            else:
                # No inter-token spacing: Japanese does not space its words,
                # and any spacing here would be width the paginator never
                # measured — which is how lines used to over-run.
                holder = StackLayout(orientation="lr-tb", spacing=0,
                                     size_hint_y=None)
                holder.bind(minimum_height=holder.setter("height"))
            for token in line.tokens:
                row = rows.get(token.key) if token.known_word else None
                english = self._weave.show_english(token, row)
                w = WeaveWord(token, english, token.known_word,
                              vertical=vertical, font_size=sp(size),
                              font_name=face)
                if token.known_word:
                    whole = getattr(token, "token", token)
                    w.bind(on_release=lambda _w, t=whole: self._tap(t))
                    self._words.append(w)
                holder.add_widget(w)
                if english or self._weave.holds.get(token.key):
                    # One crutch turn per word per PAGE: a page with six
                    # appearances of a word must not spend six turns, and a
                    # word continued across two columns is still one word.
                    if token.key not in spent_here \
                            and getattr(token, "start", 0) == 0:
                        spent_here.add(token.key)
                        self._weave.consume(token)
            if vertical:
                holder.add_widget(Widget())      # push the column to the top
            body.add_widget(holder)
        self._box.add_widget(body)
        self._body = body      #: the drawn page, for measuring and QA
        self._set_title()
        self._sync_footer()

    def _add_footer(self) -> None:
        foot = BoxLayout(orientation="horizontal", spacing=dp(8),
                         size_hint_y=None, height=dp(48))
        # Vertical Japanese runs right-to-left, so "next" points LEFT and the
        # buttons swap sides — turning a page should feel like the book does.
        vert = self.vertical_mode()
        prev = ThemedButton(text=tr("LIB_NEXT_V") if vert else tr("LIB_PREV"),
                            font_size=sp(14), height=dp(48))
        nxt = ThemedButton(text=tr("LIB_PREV_V") if vert else tr("LIB_NEXT"),
                           fill=theme.ACCENT, font_size=sp(14), height=dp(48))
        if vert:
            prev.bind(on_release=lambda *_: self._page(1))
            nxt.bind(on_release=lambda *_: self._page(-1))
            prev.disabled = self._page_index + 1 >= max(1, self._page_count)
            nxt.disabled = self._page_index <= 0
        else:
            prev.bind(on_release=lambda *_: self._page(-1))
            nxt.bind(on_release=lambda *_: self._page(1))
            prev.disabled = self._page_index <= 0
            nxt.disabled = self._page_index + 1 >= max(1, self._page_count)
        foot.add_widget(prev)
        foot.add_widget(nxt)
        self.add_widget(foot)
        self._foot = (prev, nxt)
        self._sync_footer()

    def _sync_footer(self) -> None:
        """Page buttons follow the page count, which the fill discovers."""
        if not self._foot:
            return
        prev, nxt = self._foot
        last = self._page_index + 1 >= max(1, self._page_count)
        first = self._page_index <= 0
        if self.vertical_mode():
            prev.disabled, nxt.disabled = last, first
        else:
            prev.disabled, nxt.disabled = first, last

    def _reader_options(self):
        """Type size, font variety and orientation, previewed by the page.

        The panel is capped at a third of the screen. A fixed dp(140) left a
        phone in landscape with no page at all — the controls have to give way
        to the text, not the other way round.
        """
        from kanjire.kivyui.widgets import ChipRow
        height = self._opts_height()
        rows = [ChipRow([(n, str(n)) for n in FONT_SIZES],
                        font_size_of(self._app.state),
                        on_change=lambda v: self._set_display("read_font_size",
                                                              str(v))),
                ChipRow([("single", tr("FONT_SINGLE")),
                         ("random", tr("FONT_RANDOM"))],
                        self._app.state.setting("read_fonts", "single"),
                        on_change=lambda v: self._set_display("read_fonts", v)),
                ChipRow([("horizontal", tr("READ_HORIZ")),
                         ("vertical", tr("READ_VERT"))],
                        self._app.state.setting("read_orientation",
                                                "horizontal"),
                        on_change=lambda v: self._set_display(
                            "read_orientation", v))]
        # Short screen: drop the caption and scroll the rows rather than
        # squeezing them into a few pixels each.
        tall = height >= dp(120)
        box = GridLayout(cols=1, spacing=dp(4), size_hint_y=None,
                         height=height)
        if tall:
            box.add_widget(JPLabel(text=tr("READ_SIZE"),
                                   color=rgba(theme.MUTED), font_size=sp(11),
                                   size_hint_y=None, height=dp(18)))
            for row in rows:
                box.add_widget(row)
            return box
        inner = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        inner.bind(minimum_height=inner.setter("height"))
        for row in rows:
            row.size_hint_y = None
            row.height = dp(40)
            inner.add_widget(row)
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        scroll.add_widget(inner)
        box.add_widget(scroll)
        return box

    def _set_display(self, key: str, value: str) -> None:
        """Re-render immediately: the page IS the preview.

        Text size, font and orientation all move the page breaks, so the
        passage is re-paginated — and the reader keeps their place because
        the cursor is a sentence, not a page.
        """
        self._app.state.set_setting(key, value)
        self._page_cache = self._page_key = None
        pages, _rows = self._pages()
        self._page_index = page_of_sentence(pages, self._pos)
        self._render()

    def _page(self, delta: int) -> None:
        """Turn one measured page. Stores the SENTENCE the page starts on.

        Saving a page *number* would teleport the reader whenever the text
        size, font or orientation changed — and two synced devices have
        different screens, so they would disagree about where you are.
        """
        pages, _rows = self._pages()
        if not pages:
            return
        self._page_index = max(0, min(len(pages) - 1,
                                      self._page_index + (1 if delta > 0
                                                          else -1)))
        self._pos = pages[self._page_index].first_sentence
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
        """Reveal a word — and let that count as evidence everywhere.

        Order matters. The reveal and the repaint happen NOW; the two SQLite
        commits (the lookup stat, the saved position) are pushed to the next
        frame. Each commit is a synchronous disk write — about 20ms on a
        phone, the same cost that used to hitch every card match — and a full
        page re-render on top of them made a tap feel like nothing happened.
        """
        row = self._stats_row(token)
        english, verdict = self._weave.flip(token, row)
        self._repaint(token)
        if english:
            # Turned INTO English: they needed the gloss, so show it.
            self._app.info(
                f"{token.expression}  ({token.reading})\n{token.meaning}")

        def _persist(_dt):
            try:
                if verdict == "learned":
                    self._app.stats.reading_recall(token.expression,
                                                   token.reading,
                                                   token.meaning)
                elif verdict == "missed":
                    self._app.stats.reading_lookup(token.expression,
                                                   token.reading,
                                                   token.meaning)
            except Exception:       # noqa: BLE001
                pass
            self._save()
            try:
                # Dropping the crutch always speaks the word: hearing it is
                # the point of turning it back into Japanese.
                if not english or self._app.state.tts_on_select:
                    self._app.audio.speech.say_jp(
                        token.reading or token.expression)
            except Exception:       # noqa: BLE001
                pass

        Clock.schedule_once(_persist, 0)

    def _repaint(self, token) -> None:
        """Flip every on-screen instance of one word, without rebuilding."""
        rows = self._page_tokens()[1]
        for w in self._words:
            if w.token.key == token.key:
                w.set_state(self._weave.show_english(w.token,
                                                     rows.get(w.token.key)))
