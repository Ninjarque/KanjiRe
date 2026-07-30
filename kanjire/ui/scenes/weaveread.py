"""The diglot-weave reader on the desktop — the pyglet twin of the Kivy one.

Same three views (library / add / reader) over the same model
(:mod:`kanjire.data.weave`, :mod:`kanjire.data.library`), so a passage added
on one device reads identically on the other. Only the drawing differs.

The reader flows its tokens by hand: each token is a Label, measured and
wrapped, and clicks are hit-tested against the resulting rectangles. That is
what lets any *word* be clickable rather than the whole sentence.
"""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data.library import Library
from kanjire.data.paginate import (english_of, page_of_sentence,
                                   paginate)
from kanjire.data.weave import (FONT_SIZES, Lexicon, clipboard_text, describe,
                                font_size_of, sentence_font, stamp_slots)
from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT, JP_FONTS
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar
from kanjire.ui.widgets.textinput import TextInput



class WeaveScene(Scene):
    """Library, add-a-text, and the reader itself."""

    def __init__(self, app, book_id: int | None = None) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.library = Library(app.stats.con)
        self._lex: Lexicon | None = None
        self.view = "library"          #: "library" | "add" | "read"
        self.book = None
        self._weave = None
        self._pos = 0                  #: cursor — a SENTENCE, not a page
        self._tokens: list[tuple] = []     # (token, [Label], x, y, w, h)
        self._page_index = 0
        self._page_cache = None
        self._page_key = None
        self._groups_cache: list | None = None
        self._groups_key = None

        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_JOURNEY"),  lambda: self.app.go_journey()),
             (tr("NAV_READ"),     lambda: self.app.go_reading()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: self.app.go_friends()),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_READ"))
        self.subtabs = TabBar(
            [(tr("READ_MODE_FEED"), lambda: self.app.go_reading()),
             (tr("READ_MODE_LIBRARY"), lambda: None)],
            self.batch, self.g_bg, self.g_text,
            accent=theme.GOLD, font_size=12,
        )
        self.subtabs.set_active(1)

        def lbl(size, color, bold=False, anchor_x="center"):
            out = Label("", font_name=JP_FONT, font_size=size, bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x=anchor_x, anchor_y="center",
                        batch=self.batch, group=self.g_text)
            out._base_fs = size
            return out

        self.title = lbl(16, theme.MUTED, bold=True)
        self.subtitle = lbl(12, theme.DIM)
        self.counts = lbl(12, theme.DIM)
        self.labels = [self.title, self.subtitle, self.counts]

        # Buttons that persist across views (shown/hidden per view).
        self.add_btn = self._btn(tr("LIB_ADD"), self.show_add, theme.SUCCESS)
        self.paste_btn = self._btn(tr("LIB_PASTE"), self._paste, theme.ACCENT)
        self.save_btn = self._btn(tr("DLG_OK"), self._save, theme.SUCCESS)
        self.cancel_btn = self._btn(tr("DLG_CANCEL"), self.show_library,
                                    theme.DIM)
        self.back_btn = self._btn(tr("LIB_BACK"), self._leave, theme.DIM)
        self.prev_btn = self._btn(tr("LIB_PREV"), lambda: self._page(-1),
                                  theme.DIM)
        self.next_btn = self._btn(tr("LIB_NEXT"), lambda: self._page(1),
                                  theme.ACCENT)
        self.size_btn = self._btn(tr("READ_SIZE_BTN"), self._cycle_size,
                                  theme.PANEL_HI, 11)
        self.font_btn = self._btn(tr("FONT_SINGLE"), self._cycle_fonts,
                                  theme.PANEL_HI, 11)
        self.orient_btn = self._btn(tr("READ_HORIZ"), self._cycle_orient,
                                    theme.PANEL_HI, 11)
        self._book_btns: list[tuple] = []

        self.title_in = TextInput(self.batch, self.g_bg, self.g_bg,
                                  self.g_text, font_size=13,
                                  placeholder=tr("LIB_ASK_TITLE"))
        self.body_in = TextInput(self.batch, self.g_bg, self.g_bg,
                                 self.g_text, font_size=13,
                                 placeholder=tr("LIB_ASK_TEXT"),
                                 on_change=lambda _t: self._recount())
        self.inputs = [self.title_in, self.body_in]

        if book_id is not None:
            self.open_book(book_id)
        else:
            self.show_library()

    # ------------------------------------------------------------------ #
    def _btn(self, text, on_click, accent, font_size=12) -> Button:
        return Button(text, on_click, self.batch, self.g_bg, self.g_text,
                      accent=accent, font_size=font_size)

    def lexicon(self) -> Lexicon:
        if self._lex is None:
            self._lex = Lexicon(self.app.con)
        return self._lex

    def _all_buttons(self) -> list[Button]:
        return ([self.add_btn, self.paste_btn, self.save_btn, self.cancel_btn,
                 self.back_btn, self.prev_btn, self.next_btn,
                 self.size_btn, self.font_btn, self.orient_btn]
                + [b for _i, b, _d in self._book_btns])

    def _set_view(self, view: str) -> None:
        self.view = view
        for b in self._all_buttons():
            b.set_visible(False)
        for w in self.inputs:
            w.set_visible(False)
        if view == "library":
            self.add_btn.set_visible(True)
            for _i, b, d in self._book_btns:
                b.set_visible(True)
                d.set_visible(True)
        elif view == "add":
            for b in (self.paste_btn, self.save_btn, self.cancel_btn):
                b.set_visible(True)
            for w in self.inputs:
                w.set_visible(True)
        else:
            for b in (self.back_btn, self.prev_btn, self.next_btn,
                      self.size_btn, self.font_btn, self.orient_btn):
                b.set_visible(True)
        self.on_resize(self.width, self.height)

    # ---- library ------------------------------------------------------- #
    def show_library(self) -> None:
        self.book = None
        self._clear_tokens()
        for _i, b, d in self._book_btns:
            b.delete()
            d.delete()
        self._book_btns.clear()
        for book in self.library.books():
            pct = int(round(book["ratio"] * 100))
            btn = self._btn(f"{book['title']}   {pct}%",
                            lambda b=book["id"]: self.open_book(b),
                            theme.SUCCESS if book["done"] else theme.GOLD, 13)
            btn.set_progress(book["ratio"])
            # Plain ASCII: the bundled fonts have no ✕, and it would be tofu.
            rm = self._btn("x", lambda b=book: self._confirm_delete(b),
                           theme.DANGER, 12)
            self._book_btns.append((book["id"], btn, rm))
        self.title.text = tr("LIB_TITLE")
        n = len(self._book_btns)
        self.subtitle.text = "" if n else tr("LIB_EMPTY")
        self.counts.text = ""
        self._set_view("library")

    def _confirm_delete(self, book) -> None:
        def apply() -> None:
            self.library.delete(book["id"])
            self.show_library()

        self.app.confirm(tr("LIB_DELETE_MSG", name=book["title"]), apply,
                         danger=True)

    # ---- add ----------------------------------------------------------- #
    def show_add(self) -> None:
        self.title.text = tr("LIB_ADD_TITLE")
        self.subtitle.text = ""
        self.title_in.set_text(tr("LIB_DEFAULT_TITLE"))
        self.body_in.set_text("")
        self._recount()
        self._set_view("add")

    def _recount(self) -> None:
        chars, sentences = describe(self.body_in.text)
        self.counts.text = tr("LIB_COUNTS", chars=chars, sentences=sentences)

    def _paste(self) -> None:
        """Paste through the shared helper, not the field.

        The clipboard's Windows line endings are what made a pasted chapter
        arrive as an empty box on the phone; this path normalises them, and
        the counts under the field prove the text landed.
        """
        got = clipboard_text()
        if got:
            self.body_in.set_text((self.body_in.text or "") + got)
        self._recount()

    def _save(self) -> None:
        book_id = self.library.add(self.title_in.text, self.body_in.text)
        if book_id is None:
            self.app.confirm(tr("LIB_NOTHING"), lambda: None, danger=False)
            return
        self.open_book(book_id)

    # ---- reader -------------------------------------------------------- #
    def open_book(self, book_id: int) -> None:
        book = self.library.get(book_id)
        if book is None:
            self.show_library()
            return
        self.book = book
        self._weave = self.library.load_weave(book_id)
        self._page_cache = self._page_key = None
        self._pos = int(book["position"] or 0)
        if self._pos >= (book["n_sentences"] or 0):
            self._pos = 0
        self.title.text = book["title"]
        self._set_view("read")

    # ---- display settings, shared with the Kivy reader ------------------ #
    def vertical_mode(self) -> bool:
        return self.app.state.setting("read_orientation",
                                      "horizontal") == "vertical"

    def _set_display(self, key: str, value: str) -> None:
        """Change size / font / orientation and re-paginate in place.

        The reader keeps their place across all three because the cursor is a
        sentence index — a page number would teleport them.
        """
        self.app.state.set_setting(key, value)
        self._page_cache = self._page_key = None
        self.on_resize(self.width, self.height)

    def _cycle_size(self) -> None:
        sizes = list(FONT_SIZES)
        i = sizes.index(font_size_of(self.app.state)) \
            if font_size_of(self.app.state) in sizes else 0
        self._set_display("read_font_size", str(sizes[(i + 1) % len(sizes)]))

    def _cycle_fonts(self) -> None:
        one = self.app.state.setting("read_fonts", "single") != "random"
        self._set_display("read_fonts", "random" if one else "single")

    def _cycle_orient(self) -> None:
        self._set_display("read_orientation",
                          "horizontal" if self.vertical_mode() else "vertical")

    def _stats_row(self, token):
        try:
            return self.app.stats.get_for(token.expression, token.reading)
        except Exception:  # noqa: BLE001
            return None

    def _clear_tokens(self) -> None:
        for _t, labels, *_rest in self._tokens:
            for label in labels:
                label.delete()
        self._tokens.clear()

    def _measurer(self, face, fs):
        """Width of a string in the REAL text engine, memoised.

        Character counts would mis-page every proportional English gloss, and
        a page only ever holds a few hundred distinct strings.
        """
        cache: dict[str, float] = {}

        def measure(text: str) -> float:
            if not text:
                return 0.0
            got = cache.get(text)
            if got is None:
                lbl = Label(text, font_name=face or JP_FONT, font_size=fs)
                got = float(lbl.content_width)
                lbl.delete()
                cache[text] = got
            return got

        return measure

    def _groups(self) -> list:
        """The whole passage, tokenised once per book."""
        bid = self.book["id"] if self.book else None
        if self._groups_key != bid or self._groups_cache is None:
            lex = self.lexicon()
            self._groups_cache = stamp_slots(
                [lex.tokenize(t)
                 for t in self.library.sentences(bid, 0, 10 ** 9)]) if bid \
                else []
            self._groups_key = bid
        return self._groups_cache

    def _page_area(self, s) -> tuple[float, float]:
        """Usable text box: the window minus the header and footer rows."""
        w = min(self.width - 80 * s, 900 * s)
        h = (self.height - 226 * s) - 120 * s
        return (max(80.0, w), max(80.0, h))

    @staticmethod
    def _vertical_metrics(fs) -> tuple[float, float]:
        """(character advance down a column, distance between columns)."""
        return (fs * 1.3, fs * 1.75)

    def _pages(self, s):
        """Pages for this passage at this size, font, orientation and window.

        Every one of those moves the breaks, so every one is in the key.
        """
        state = self.app.state
        size = font_size_of(state)
        varied = state.setting("read_fonts", "single") == "random"
        vertical = self.vertical_mode()
        aw, ah = self._page_area(s)
        key = (self.book["id"] if self.book else None, size, varied, vertical,
               round(aw), round(ah))
        if self._page_key == key and self._page_cache is not None:
            return self._page_cache

        groups = self._groups()
        fs = max(10, round(size * s))
        # Random fonts change the face — and the width of the same word —
        # per sentence, so each sentence is measured in its own face.
        measurers: dict = {}

        def measure_for(index):
            if vertical:
                return len
            face = sentence_font(JP_FONTS, index, varied) or JP_FONT
            got = measurers.get(face)
            if got is None:
                got = measurers[face] = self._measurer(face, fs)
            return got

        if vertical:
            # One character per cell in both scripts, so the measurement is a
            # character count and needs no text engine at all. The pitch
            # between columns is wider than the cell: kanji are drawn to the
            # full em, so neighbouring columns would otherwise touch.
            cell, pitch = self._vertical_metrics(fs)
            # A word longer than a whole column continues in the next one,
            # as vertical Japanese really does — otherwise it is drawn off
            # the page (measured: 4 characters in a 3-cell column).
            pages = paginate(groups, len,
                             main_axis=max(1, int(ah // cell)),
                             cross_axis=max(1, int(aw // pitch)),
                             line_extent=1, split_units=True)
        else:
            face = sentence_font(JP_FONTS, 0, varied) or JP_FONT
            pages = paginate(groups, self._measurer(face, fs),
                             main_axis=aw, cross_axis=ah,
                             line_extent=fs * 1.9, measure_for=measure_for)
        self._page_cache, self._page_key = pages, key
        return pages

    def _page(self, delta: int) -> None:
        pages = self._pages(getattr(self, "_s", 1.0))
        if not pages:
            return
        self._page_index = max(0, min(len(pages) - 1,
                                      self._page_index + delta))
        self._pos = pages[self._page_index].first_sentence
        self._save_pos()
        self.on_resize(self.width, self.height)

    def _save_pos(self) -> None:
        if self.book is not None:
            self.library.save_position(self.book["id"], self._pos,
                                       weave=self._weave)

    def _leave(self) -> None:
        self._save_pos()
        self.show_library()

    def _tap(self, token) -> None:
        """Flip one word between Japanese and English.

        Turning it INTO English opens the gloss (they needed it); turning it
        BACK reads the word aloud, because hearing it is the point of dropping
        the crutch. Either way it is evidence exactly once per appearance —
        see :meth:`kanjire.data.weave.WeaveState.flip`.
        """
        row = self._stats_row(token)
        english, verdict = self._weave.flip(token, row)
        try:
            if verdict == "learned":
                self.app.stats.reading_recall(token.expression, token.reading,
                                              token.meaning)
            elif verdict == "missed":
                self.app.stats.reading_lookup(token.expression, token.reading,
                                              token.meaning)
        except Exception:  # noqa: BLE001
            pass
        self._save_pos()
        try:
            if not english or self.app.state.tts_on_select:
                self.app.audio.speech.say_jp(token.reading or token.expression)
        except Exception:  # noqa: BLE001 — TTS is a bonus, never fatal
            pass
        if english:
            self.app.confirm(
                f"{token.expression}  ({token.reading})\n{token.meaning}",
                lambda: None, danger=False)
        self.on_resize(self.width, self.height)

    # ---- layout -------------------------------------------------------- #
    def _token_face(self, faces, index, varied):
        return sentence_font(faces, index, varied) or JP_FONT

    def _draw_token(self, token, s, spent_here=None):
        """The text and colour a token shows right now, and its face."""
        row = self._stats_row(token) if token.known_word else None
        english = self._weave.show_english(token, row)
        if english:
            text = " " + token.meaning.split(",")[0].split(";")[0] + " "
            colour = theme.ACCENT
        else:
            text = token.text
            colour = theme.TEXT if token.known_word else theme.MUTED
        if english or self._weave.holds.get(token.key):
            # One crutch turn per word per PAGE (see WeaveState.consume): a
            # page with six appearances must not spend six turns, and a word
            # continued across columns is still one word.
            if spent_here is not None and token.key not in spent_here                     and getattr(token, "start", 0) == 0:
                spent_here.add(token.key)
                self._weave.consume(token)
        return text, colour

    def _layout_tokens(self, cx, top, s) -> None:
        """Draw exactly one measured page — no clipping, no scrolling."""
        self._clear_tokens()
        if self.book is None:
            return
        pages = self._pages(s)
        if not pages:
            return
        self._page_index = min(max(0, self._page_index), len(pages) - 1)
        page = pages[self._page_index]
        state = self.app.state
        varied = state.setting("read_fonts", "single") == "random"
        fs = max(10, round(font_size_of(state) * s))
        aw, _ah = self._page_area(s)
        spent_here: set = set()     #: words that already paid on THIS page

        def mk(text, colour, face, x, y, anchor_x="left"):
            return Label(text, font_name=face, font_size=fs,
                         color=theme.with_alpha(colour, 255),
                         anchor_x=anchor_x, anchor_y="center",
                         batch=self.batch, group=self.g_text)

        if self.vertical_mode():
            # 縦書き: characters run down a column, columns run leftward from
            # the right edge — so the page starts where a Japanese book does.
            cell, pitch = self._vertical_metrics(fs)
            col_x = cx + aw / 2 - pitch / 2
            top = top - cell / 2
            for n, line in enumerate(page.lines):
                face = self._token_face(JP_FONTS, page.first_sentence + n,
                                        varied)
                y = top
                for token in line.tokens:
                    text, colour = self._draw_token(token, s, spent_here)
                    chars = [c for c in text.strip()] or [" "]
                    labels = []
                    y_top = y
                    for ch in chars:
                        lb = mk(ch, colour, face, col_x, y, anchor_x="center")
                        lb.x, lb.y = col_x, y
                        labels.append(lb)
                        y -= cell
                    self._tokens.append(
                        (token, labels, col_x - pitch / 2, y + cell / 2,
                         pitch, y_top - y))
                col_x -= pitch
            return

        line_h = fs * 1.9
        x0 = cx - aw / 2
        # Labels are anchored centre-y, so the first line's centre sits half a
        # line INSIDE the area — otherwise the text creeps into the header.
        y = top - line_h / 2
        for n, line in enumerate(page.lines):
            face = self._token_face(JP_FONTS, page.first_sentence + n, varied)
            x = x0
            for token in line.tokens:
                text, colour = self._draw_token(token, s, spent_here)
                label = mk(text, colour, face, x, y)
                label.x, label.y = x, y
                w = label.content_width
                self._tokens.append((token, [label], x, y - line_h / 2, w,
                                     line_h))
                x += w
            y -= line_h

    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        self.nav.set_scale(s)
        self.subtabs.set_scale(s)
        cx = width / 2
        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)
        self.title.x, self.title.y = cx, height - 92 * s
        self.subtabs.set_rect(cx - 130 * s, height - 152 * s, 260 * s, 30 * s)
        for b in self._all_buttons():
            b.set_scale(s)
        for w in self.inputs:
            w.set_scale(s)

        if self.view != "add":
            self.counts.text = ""
        if self.view == "library":
            self.subtitle.x, self.subtitle.y = cx, height - 196 * s
            self.add_btn.set_rect(cx - 110 * s, height - 200 * s, 220 * s,
                                  30 * s)
            y = height - 248 * s
            for _i, btn, rm in self._book_btns:
                btn.set_rect(cx - 220 * s, y, 380 * s, 34 * s)
                rm.set_rect(cx + 168 * s, y, 40 * s, 34 * s)
                y -= 42 * s
            return

        if self.view == "add":
            fw = min(width - 80 * s, 760 * s)
            self.title_in.set_rect(cx - fw / 2, height - 212 * s, fw, 32 * s)
            self.body_in.set_rect(cx - fw / 2, height - 272 * s, fw, 46 * s)
            self.paste_btn.set_rect(cx - fw / 2, height - 322 * s, 220 * s,
                                    30 * s)
            self.counts.anchor_x = "left"
            self.counts.x = cx - fw / 2 + 234 * s
            self.counts.y = height - 307 * s
            self.cancel_btn.set_rect(cx - 210 * s, 140 * s, 190 * s, 34 * s)
            self.save_btn.set_rect(cx + 20 * s, 140 * s, 190 * s, 34 * s)
            return

        total = (self.book or {}).get("n_sentences") or 1
        pages = self._pages(s)
        self._page_index = page_of_sentence(pages, self._pos)
        pct = int(self._pos / total * 100)
        self.subtitle.text = (f'{tr("LIB_COUNTS_READ", pct=pct)}   '
                              f'{self._page_index + 1}/{max(1, len(pages))}')
        self.subtitle.x, self.subtitle.y = cx, height - 176 * s
        self.back_btn.set_rect(24 * s, height - 190 * s, 150 * s, 30 * s)
        # Display options, top-right: the page below is their preview.
        self.size_btn.set_text(f'{tr("READ_SIZE_BTN")} {font_size_of(self.app.state)}')
        self.font_btn.set_text(
            tr("FONT_RANDOM")
            if self.app.state.setting("read_fonts", "single") == "random"
            else tr("FONT_SINGLE"))
        vertical = self.vertical_mode()
        self.orient_btn.set_text(tr("READ_VERT") if vertical
                                 else tr("READ_HORIZ"))
        bw, bx = 96 * s, self.width - 24 * s
        for btn in (self.orient_btn, self.font_btn, self.size_btn):
            bx -= bw + 6 * s
            btn.set_rect(bx, height - 190 * s, bw, 30 * s)
        self._layout_tokens(cx, height - 226 * s, s)
        # In vertical writing the page turns leftward, so the buttons swap.
        self.prev_btn.set_text(tr("LIB_PREV_V") if vertical else tr("LIB_PREV"))
        self.next_btn.set_text(tr("LIB_NEXT_V") if vertical else tr("LIB_NEXT"))
        left, right = ((self.next_btn, self.prev_btn) if vertical
                       else (self.prev_btn, self.next_btn))
        left.set_rect(cx - 210 * s, 70 * s, 190 * s, 34 * s)
        right.set_rect(cx + 20 * s, 70 * s, 190 * s, 34 * s)
        self.prev_btn.enabled = self._page_index > 0
        self.next_btn.enabled = self._page_index + 1 < len(pages)

    # ---- events -------------------------------------------------------- #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.nav.on_mouse_press(x, y):
            return
        if self.subtabs.on_mouse_press(x, y):
            return
        if self.view == "add":
            for w in self.inputs:
                if w.on_mouse_press(x, y, button, modifiers):
                    for other in self.inputs:
                        if other is not w:
                            other.unfocus()
                    return
        for b in self._all_buttons():
            if b.visible and b.enabled and b.contains(x, y):
                b.click()
                return
        if self.view == "read":
            for token, _labels, tx, ty, tw, th in self._tokens:
                if token.known_word and tx <= x <= tx + tw \
                        and ty <= y <= ty + th:
                    self._tap(token)
                    return

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        self.subtabs.on_mouse_motion(x, y)
        for b in self._all_buttons():
            b.set_hover(b.visible and b.contains(x, y))

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

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key
        for w in self.inputs:
            if w.focused and w.on_key_press(symbol, modifiers):
                return
        # Ctrl+V in the body field, for people who never look for a button.
        if symbol == key.V and (modifiers & key.MOD_CTRL) and \
                self.view == "add":
            self._paste()
            return
        if symbol == key.ESCAPE:
            if self.view == "read":
                self._leave()
            elif self.view == "add":
                self.show_library()
            else:
                self.app.go_reading()

    def draw(self) -> None:
        h = round(64 * getattr(self, "_s", 1.0))
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        self._save_pos()
        self._clear_tokens()
        self.nav.delete()
        self.subtabs.delete()
        for b in self._all_buttons():
            b.delete()
        for w in self.inputs:
            w.delete()
