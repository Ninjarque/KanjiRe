"""Reading Room: real sentences at your level, one at a time.

Sentences are picked by the *i+1 rule*: every kanji word in them is one you
already know, except at most one. Tap any word chip for its reading and
meaning; tap **+ learn** on an unknown word to queue it for Today's
Training. Reading volume (sentences / characters) is tracked as a
first-class stat — reading is the outcome this whole app exists for.
"""
from __future__ import annotations

import random

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data import coverage as coverage_mod
from kanjire.data import kanjidata
from kanjire.data import reading_level
from kanjire.i18n import tr
from kanjire.jputil import has_kanji, uncovered_kanji
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar

#: Refill the queue when it runs this low.
_REFILL_AT = 5

#: How many words in a sentence may be new (unknown), i.e. the i+1 dial.
#: (state value, translation key).
NEW_WORD_OPTIONS = ((0, "READ_NEW_0"), (1, "READ_NEW_1"), (2, "READ_NEW_2"))

#: Difficulty preference: how the readable pool is ordered/biased. "easy"
#: serves the gentlest sentences first, "challenging" the hardest (highest
#: average, spikes welcome), "comfortable" aims at the middle of your range.
DIFFICULTY_OPTIONS = (("easy", "READ_DIFF_EASY"),
                      ("comfortable", "READ_DIFF_MID"),
                      ("challenging", "READ_DIFF_HARD"))


class ReadingScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.g_pop_bg = OrderedGroup(2)
        self.g_pop_fg = OrderedGroup(3)

        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_JOURNEY"),  lambda: self.app.go_journey()),
             (tr("NAV_READ"),     lambda: None),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: self.app.go_friends()),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_READ"))

        self.rng = random.Random()
        self._known = {
            expr for expr, _r in coverage_mod.known_keys(app.stats)
            if has_kanji(expr)
        }
        # Curriculum controls (persisted). new_words = how many unknown words a
        # sentence may have (the i+1 dial); difficulty = how the readable pool
        # is ordered by intrinsic hardness.
        self.new_words = int(app.state.setting("read_new_words", "1") or 1)
        if self.new_words not in {v for v, _ in NEW_WORD_OPTIONS}:
            self.new_words = 1
        self.difficulty = app.state.setting("read_difficulty", "comfortable")
        if self.difficulty not in {v for v, _ in DIFFICULTY_OPTIONS}:
            self.difficulty = "comfortable"
        # Intrinsic word difficulty (JLPT + frequency), loaded once.
        self._word_diff = reading_level.load_word_difficulty(app.con)
        # Sources: the built-in Tanaka corpus plus any imported deck that
        # captured sentences (imports made before v0.7 have none).
        self.sources: list[tuple[str, str]] = [("tanaka", tr("READ_SRC_GENERAL"))]
        try:
            for r in app.con.execute(
                    "SELECT deck, COUNT(*) AS n FROM corpus_sentences "
                    "GROUP BY deck HAVING n > 0"):
                name = r["deck"][len("corpus:"):].replace("-", " ").title() \
                    if r["deck"].startswith("corpus:") else r["deck"]
                self.sources.append((r["deck"], name))
        except Exception:
            pass
        self.source = "tanaka"
        self._read_ids = app.stats.read_sentence_ids(self.source)
        self._queue: list[dict] = []
        self.current: dict | None = None
        self._translation_shown = False

        def lbl(size, color, *, bold=False, anchor_x="center",
                multiline=False, width=100):
            kw = (dict(multiline=True, width=width, align="center")
                  if multiline else {})
            out = Label("", font_name=JP_FONT, font_size=size, bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x=anchor_x, anchor_y="center",
                        batch=self.batch, group=self.g_text, **kw)
            out._base_fs = size
            return out

        self.title = lbl(16, theme.MUTED, bold=True)
        self.title.text = tr("READ_TITLE")
        self.totals = lbl(12, theme.DIM, anchor_x="right")
        self.sentence = lbl(22, theme.TEXT, multiline=True, width=800)
        self.translation = lbl(13, theme.MUTED, multiline=True, width=760)
        self.level_note = lbl(11, theme.DIM)
        self.empty_hint = lbl(14, theme.DIM, multiline=True, width=640)
        self.labels = [self.title, self.totals, self.sentence,
                       self.translation, self.level_note, self.empty_hint]

        self.next_btn = Button(tr("READ_NEXT"), self._next, self.batch,
                               self.g_bg, self.g_text,
                               accent=theme.SUCCESS, font_size=15)
        self.trans_btn = Button(tr("READ_TRANSLATE"), self._toggle_translation,
                                self.batch, self.g_bg, self.g_text,
                                accent=theme.ACCENT, font_size=12)
        self.buttons = [self.next_btn, self.trans_btn]
        # Source selector (shown only when imported corpora exist).
        self.source_btns: list[tuple[str, Button]] = []
        if len(self.sources) > 1:
            for key, name in self.sources:
                b = Button(name, lambda k=key: self._set_source(k),
                           self.batch, self.g_bg, self.g_text,
                           accent=theme.GOLD, font_size=11)
                b.set_selected(key == self.source)
                self.buttons.append(b)
                self.source_btns.append((key, b))

        # Curriculum control rows: how many new words, and how hard.
        self.lbl_new = lbl(10, theme.MUTED, anchor_x="right")
        self.lbl_new.text = tr("READ_NEW_LABEL")
        self.lbl_diff = lbl(10, theme.MUTED, anchor_x="right")
        self.lbl_diff.text = tr("READ_DIFF_LABEL")
        self.labels += [self.lbl_new, self.lbl_diff]
        self.new_btns: list[tuple[int, Button]] = []
        for val, key in NEW_WORD_OPTIONS:
            b = Button(tr(key), lambda v=val: self._set_new_words(v),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.ACCENT, font_size=10)
            b.set_selected(val == self.new_words)
            self.buttons.append(b)
            self.new_btns.append((val, b))
        self.diff_btns: list[tuple[str, Button]] = []
        for val, key in DIFFICULTY_OPTIONS:
            b = Button(tr(key), lambda v=val: self._set_difficulty(v),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.GOLD, font_size=10)
            b.set_selected(val == self.difficulty)
            self.buttons.append(b)
            self.diff_btns.append((val, b))

        # Word chips for the current sentence (rebuilt per sentence).
        self.chips: list[tuple[Button, dict]] = []
        # Popup widgets for a tapped chip.
        self._pop_widgets: list = []
        self._pop_open = False

        self._advance(log=False)
        self._update_totals()

    # ------------------------------------------------------------------ #
    # Queue / flow
    # ------------------------------------------------------------------ #
    def _set_source(self, key: str) -> None:
        if key == self.source:
            return
        self.source = key
        for k, b in self.source_btns:
            b.set_selected(k == key)
        self._read_ids = self.app.stats.read_sentence_ids(key)
        self._queue.clear()
        self.current = None
        self._advance(log=False)

    def _set_new_words(self, n: int) -> None:
        self.new_words = int(n)
        self.app.state.set_setting("read_new_words", str(n))
        for v, b in self.new_btns:
            b.set_selected(v == n)
        self._requeue()

    def _set_difficulty(self, pref: str) -> None:
        self.difficulty = pref
        self.app.state.set_setting("read_difficulty", pref)
        for v, b in self.diff_btns:
            b.set_selected(v == pref)
        self._requeue()

    def _requeue(self) -> None:
        """A control changed: rebuild the queue and show a fresh first sentence
        so the change is felt immediately, not five sentences later."""
        self._queue.clear()
        self.current = None
        self._advance(log=False)

    def _order_pool(self, pool: list[dict]) -> list[dict]:
        """Rate a readable shortlist and order it by the difficulty preference.

        Every sentence keeps its own difficulty (``avg``/``peak``); unrated ones
        (no placeable words) sort to the middle so they neither lead nor vanish.
        """
        mids = []
        for s in pool:
            heads = [h for (h, _r, _g) in self._words_of(s["id"])]
            r = reading_level.rate_from_heads(heads, self._word_diff)
            s["avg"] = r.average if r else None
            s["peak"] = r.peak if r else None
        rated = [s for s in pool if s["avg"] is not None]
        if not rated:
            return pool
        avgs = sorted(s["avg"] for s in rated)
        median = avgs[len(avgs) // 2]

        def key(s):
            a = s["avg"]
            if a is None:
                a = median          # unrated: neutral
            if self.difficulty == "easy":
                return (a, s["peak"] or a)                # gentlest first
            if self.difficulty == "challenging":
                return (-a, -(s["peak"] or a))            # hardest first
            return (abs(a - median), s["peak"] or a)      # comfortable: middle

        return sorted(pool, key=key)

    def _words_of(self, sentence_id: int):
        """Indexed (headword, reading, good) for any source."""
        if self.source == "tanaka":
            return kanjidata.words_of(sentence_id)
        try:
            return [(r["headword"], r["reading"], False)
                    for r in self.app.con.execute(
                        "SELECT headword, reading FROM corpus_sentence_words "
                        "WHERE sentence_id=?", (sentence_id,))]
        except Exception:
            return []

    def _corpus_sentences(self, max_unknown: int,
                          exclude: set[int]) -> list[dict]:
        """i+1 filter over an imported deck's captured sentences.

        Corpus decks are small (hundreds of sentences), so the density check
        just walks each sentence's word rows. No translation exists for the
        player's own text — ``en`` stays empty and the button disables.
        """
        out: list[dict] = []
        try:
            rows = self.app.con.execute(
                "SELECT id, ja FROM corpus_sentences "
                "WHERE deck=? AND n_kanji_words > 0", (self.source,)
            ).fetchall()
            for r in rows:
                if r["id"] in exclude:
                    continue
                words = self.app.con.execute(
                    "SELECT headword FROM corpus_sentence_words "
                    "WHERE sentence_id=?", (r["id"],)).fetchall()
                heads = [w["headword"] for w in words]
                kanji_words = [h for h in heads if has_kanji(h)]
                if not kanji_words:
                    continue
                unknown = sum(1 for h in kanji_words if h not in self._known)
                # Kanji the indexer dropped (names like 竹内, unresolved tokens)
                # count as unknown too - otherwise a name-heavy sentence claims
                # you know every word on the strength of one common one.
                if uncovered_kanji(r["ja"], heads):
                    unknown += 1
                if unknown <= max_unknown:
                    out.append({"id": r["id"], "ja": r["ja"], "en": "",
                                "unknown": unknown})
        except Exception:
            return []
        out.sort(key=lambda s: (s["unknown"], self.rng.random()))
        return out[:40]

    def _refill(self) -> None:
        exclude = self._read_ids | {s["id"] for s in self._queue}
        if self.current:
            exclude.add(self.current["id"])
        # Try the player's chosen new-word budget first, then loosen by one so
        # the room never runs dry (an advanced reader on "known only" still gets
        # served if their exact setting is momentarily empty).
        budgets = [self.new_words]
        if self.new_words < 2:
            budgets.append(self.new_words + 1)
        for max_unknown in budgets:
            if self.source == "tanaka":
                got = kanjidata.readable_sentences(
                    self._known, max_unknown=max_unknown, limit=60,
                    exclude_ids=exclude, rng=self.rng)
            else:
                got = self._corpus_sentences(max_unknown, exclude)
            if got:
                self._queue.extend(self._order_pool(got))
                return

    def _advance(self, log: bool = True) -> None:
        if log and self.current is not None:
            try:
                self.app.stats.log_read(self.current["id"],
                                        len(self.current["ja"]),
                                        source=self.source)
            except Exception:
                pass
            self._read_ids.add(self.current["id"])
            self._update_totals()
        if len(self._queue) <= _REFILL_AT:
            self._refill()
        self._close_popup()
        self.current = self._queue.pop(0) if self._queue else None
        self._translation_shown = False
        self._show_current()

    def _show_current(self) -> None:
        for b, _info in self.chips:
            b.delete()
        self.chips.clear()
        if self.current is None:
            self.sentence.text = ""
            self.translation.text = ""
            self.level_note.text = ""
            self.empty_hint.text = tr("READ_EMPTY")
            self.next_btn.enabled = False
            self.trans_btn.enabled = False
            self.next_btn._refresh()
            self.trans_btn._refresh()
            return
        self.empty_hint.text = ""
        self.next_btn.enabled = True
        self.trans_btn.enabled = bool(self.current.get("en"))
        self.trans_btn._refresh()
        self.sentence.text = self.current["ja"]
        self.translation.text = ""
        note = (tr("READ_ALL_KNOWN") if self.current["unknown"] == 0
                else tr("READ_ONE_NEW", n=self.current["unknown"]))
        avg = self.current.get("avg")
        if avg is not None:
            # avg difficulty 1..5 maps back to N5..N1; show it so the reader
            # can feel the level, and how hard they've set it.
            lvl = max(1, min(5, 6 - round(avg)))
            note = f"{note}   ·   {tr('READ_LEVEL_TAG', lvl=lvl)}"
        self.level_note.text = note
        # chips: kanji-bearing words only, known ones green, unknown gold
        for head, reading, _good in self._current_words():
            if not has_kanji(head):
                continue
            known = head in self._known
            b = Button(head, lambda h=head, r=reading: self._open_popup(h, r),
                       self.batch, self.g_bg, self.g_text,
                       accent=theme.SUCCESS if known else theme.GOLD,
                       font_size=13)
            if not known:
                b.set_selected(True)     # highlighted: the new word
            self.chips.append((b, {"head": head, "reading": reading,
                                   "known": known}))
        self.on_resize(self.width, self.height)

    def _current_words(self) -> list[tuple[str, str | None, bool]]:
        if self.current is None:
            return []
        return self._words_of(self.current["id"])

    def _toggle_translation(self) -> None:
        if self.current is None:
            return
        self._translation_shown = not self._translation_shown
        self.translation.text = (self.current["en"]
                                 if self._translation_shown else "")

    def _next(self) -> None:
        self._advance(log=True)

    def _update_totals(self) -> None:
        t = self.app.stats.reading_totals()
        self.totals.text = tr("READ_TOTALS", sentences=t["sentences"],
                              chars=t["chars"])

    # ------------------------------------------------------------------ #
    # Word popup
    # ------------------------------------------------------------------ #
    def _vocab_word(self, head: str, reading: str | None):
        from kanjire.data import db as _db
        try:
            q = "SELECT * FROM words WHERE expression=?"
            args = [head]
            if reading:
                q += " AND reading=?"
                args.append(reading)
            q += " ORDER BY CASE WHEN deck='jlpt' THEN 0 ELSE 1 END LIMIT 1"
            row = self.app.con.execute(q, args).fetchone()
            if row is None and reading:
                row = self.app.con.execute(
                    "SELECT * FROM words WHERE expression=? LIMIT 1",
                    (head,)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def _open_popup(self, head: str, reading: str | None) -> None:
        self._close_popup()
        s = self._s
        info = self._vocab_word(head, reading)
        reading_txt = (info or {}).get("reading") or reading or ""
        meaning = (info or {}).get("meaning") or ""
        try:
            accent = kanjidata.pitch_of(head, reading_txt)
        except Exception:
            accent = None
        w, h = 380 * s, 170 * s
        px = min(max(20 * s, self.width / 2 - w / 2), self.width - w - 20 * s)
        py = self.height / 2 - 40 * s
        panel = shapes.BorderedRectangle(
            px, py, w, h, border=2,
            color=theme.lerp(theme.BG, theme.PANEL, 0.9),
            border_color=theme.GOLD,
            batch=self.batch, group=self.g_pop_bg)
        self._pop_widgets.append(panel)

        def plbl(text, size, color, dy, *, bold=False):
            out = Label(text, font_name=JP_FONT,
                        font_size=max(8, round(size * s)), bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x="left", anchor_y="center",
                        x=px + 20 * s, y=py + h - dy * s,
                        batch=self.batch, group=self.g_pop_fg)
            self._pop_widgets.append(out)
            return out

        plbl(head, 24, theme.TEXT, 34, bold=True)
        plbl(reading_txt + (f"  [{accent}]" if accent else ""), 14,
             theme.ACCENT, 66)
        plbl((meaning or "?")[:46], 12, theme.TEXT, 92)
        if info and head not in self._known:
            learn = Button(tr("READ_LEARN"), lambda: self._enqueue(head,
                                                                   reading_txt),
                           self.batch, self.g_pop_bg, self.g_pop_fg,
                           accent=theme.GOLD, font_size=12)
            learn.set_rect(px + 20 * s, py + 14 * s, 150 * s, 28 * s)
            self._pop_widgets.append(learn)
        self._pop_open = True

    def _enqueue(self, head: str, reading: str) -> None:
        try:
            if self.app.stats.srs is not None:
                self.app.stats.srs.enqueue_new(head, reading)
        except Exception:
            pass
        self._close_popup()

    def _close_popup(self) -> None:
        for w in self._pop_widgets:
            try:
                w.delete()
            except Exception:
                pass
        self._pop_widgets.clear()
        self._pop_open = False

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self._pop_open:
            for w in self._pop_widgets:
                if isinstance(w, Button) and w.enabled and w.contains(x, y):
                    w.click()
                    return
            self._close_popup()
            return
        if self.nav.on_mouse_press(x, y):
            return
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return
        for b, _info in self.chips:
            if b.enabled and b.contains(x, y):
                b.click()
                return

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))
        for b, _info in self.chips:
            b.set_hover(b.enabled and b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol == key.ESCAPE:
            if self._pop_open:
                self._close_popup()
            else:
                self.app.go_menu()
        elif symbol in (key.N, key.SPACE, key.ENTER, key.RETURN):
            if self.next_btn.enabled:
                self._next()
        elif symbol == key.T:
            self._toggle_translation()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        self.nav.set_scale(s)
        for b in self.buttons:
            b.set_scale(s)
        cx = width / 2
        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)
        self.title.x, self.title.y = cx, height - 92 * s
        self.totals.x, self.totals.y = width - 40 * s, height - 92 * s
        if self.source_btns:
            n = len(self.source_btns)
            bw, gap = 130 * s, 10 * s
            x0 = cx - (n * bw + (n - 1) * gap) / 2
            for i, (_k, b) in enumerate(self.source_btns):
                b.set_rect(x0 + i * (bw + gap), height - 132 * s, bw, 26 * s)

        # Two curriculum rows tucked into the top-left, out of the reading line.
        row_y = height - (160 if self.source_btns else 132) * s
        for lbl_ref, btns, bw in ((self.lbl_new, self.new_btns, 92 * s),
                                  (self.lbl_diff, self.diff_btns, 120 * s)):
            total = len(btns) * bw + (len(btns) - 1) * 8 * s
            rx = 150 * s
            lbl_ref.x, lbl_ref.y = rx - 12 * s, row_y
            for i, (_v, b) in enumerate(btns):
                b.set_scale(s)
                b.set_rect(rx + i * (bw + 8 * s), row_y - 12 * s, bw, 24 * s)
            row_y -= 34 * s
        self.sentence.width = min(860 * s, width - 120 * s)
        self.sentence.x, self.sentence.y = cx, height - 190 * s
        self.level_note.x, self.level_note.y = cx, height - 250 * s
        self.translation.width = min(820 * s, width - 160 * s)
        self.translation.x, self.translation.y = cx, height - 300 * s
        self.empty_hint.x, self.empty_hint.y = cx, height / 2

        # chip row(s), centred, wrapping
        if self.chips:
            per_row = max(1, int((width - 120 * s) // (110 * s)))
            ch_w, ch_h = 100 * s, 30 * s
            gap = 10 * s
            for i, (b, _info) in enumerate(self.chips):
                b.set_scale(s)
                r, c = divmod(i, per_row)
                n_in_row = min(per_row, len(self.chips) - r * per_row)
                row_w = n_in_row * ch_w + (n_in_row - 1) * gap
                x0 = cx - row_w / 2
                b.set_rect(x0 + c * (ch_w + gap),
                           height - 380 * s - r * (ch_h + 8 * s), ch_w, ch_h)

        by = 56 * s
        self.next_btn.set_rect(cx - 110 * s, by, 220 * s, 46 * s)
        self.trans_btn.set_rect(cx + 130 * s, by + 8 * s, 160 * s, 30 * s)

    def draw(self) -> None:
        h = round(64 * getattr(self, "_s", 1.0))
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        self._close_popup()
        self.nav.delete()
        for b in self.buttons:
            b.delete()
        for b, _info in self.chips:
            b.delete()
