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
from kanjire.data.weave import Lexicon, clipboard_text, describe
from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar
from kanjire.ui.widgets.textinput import TextInput

#: Sentences rendered per page.
WINDOW = 40


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
        self._pos = 0
        self._tokens: list[tuple] = []     # (token, Label, x, y, w, h)

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
        self.prev_btn = self._btn(tr("LIB_PREV"), lambda: self._page(-WINDOW),
                                  theme.DIM)
        self.next_btn = self._btn(tr("LIB_NEXT"), lambda: self._page(WINDOW),
                                  theme.ACCENT)
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
                 self.back_btn, self.prev_btn, self.next_btn]
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
            for b in (self.back_btn, self.prev_btn, self.next_btn):
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
        self._pos = int(book["position"] or 0)
        if self._pos >= (book["n_sentences"] or 0):
            self._pos = 0
        self.title.text = book["title"]
        self._set_view("read")

    def _stats_row(self, token):
        try:
            return self.app.stats.get_for(token.expression, token.reading)
        except Exception:  # noqa: BLE001
            return None

    def _clear_tokens(self) -> None:
        for _t, label, *_rest in self._tokens:
            label.delete()
        self._tokens.clear()

    def _page(self, delta: int) -> None:
        total = self.book["n_sentences"] or 0
        self._pos = max(0, min(max(0, total - 1), self._pos + delta))
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
        row = self._stats_row(token)
        self._weave.tapped(token, row)
        try:
            self.app.stats.reading_lookup(token.expression, token.reading,
                                          token.meaning)
        except Exception:  # noqa: BLE001
            pass
        self._save_pos()
        self.app.confirm(
            f"{token.expression}  ({token.reading})\n{token.meaning}",
            lambda: None, danger=False)
        self.on_resize(self.width, self.height)

    # ---- layout -------------------------------------------------------- #
    def _layout_tokens(self, cx, top, s) -> None:
        self._clear_tokens()
        if self.book is None:
            return
        lex = self.lexicon()
        width = min(self.width - 80 * s, 900 * s)
        x0 = cx - width / 2
        x, y = x0, top
        line_h = 34 * s
        fs = max(10, round(17 * s))
        for sentence in self.library.sentences(self.book["id"], self._pos,
                                               WINDOW):
            for token in lex.tokenize(sentence):
                row = self._stats_row(token) if token.known_word else None
                english = self._weave.show_english(token, row)
                if english:
                    text = " " + token.meaning.split(",")[0].split(";")[0] + " "
                    colour = theme.ACCENT
                else:
                    text = token.text
                    colour = theme.TEXT if token.known_word else theme.MUTED
                label = Label(text, font_name=JP_FONT, font_size=fs,
                              color=theme.with_alpha(colour, 255),
                              anchor_x="left", anchor_y="center",
                              batch=self.batch, group=self.g_text)
                w = label.content_width
                if x + w > x0 + width:
                    x = x0
                    y -= line_h
                if y < 120 * s:          # page is full; the rest waits
                    label.delete()
                    break
                label.x, label.y = x, y
                self._tokens.append((token, label, x, y - line_h / 2, w,
                                     line_h))
                x += w
                if english or self._weave.holds.get(token.key):
                    self._weave.consume(token)

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
        self.subtitle.text = tr("LIB_COUNTS_READ",
                                pct=int(self._pos / total * 100))
        self.subtitle.x, self.subtitle.y = cx, height - 176 * s
        self.back_btn.set_rect(24 * s, height - 190 * s, 150 * s, 30 * s)
        self._layout_tokens(cx, height - 226 * s, s)
        self.prev_btn.set_rect(cx - 210 * s, 70 * s, 190 * s, 34 * s)
        self.next_btn.set_rect(cx + 20 * s, 70 * s, 190 * s, 34 * s)
        self.prev_btn.enabled = self._pos > 0
        self.next_btn.enabled = self._pos + WINDOW < total

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
            for token, _label, tx, ty, tw, th in self._tokens:
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
