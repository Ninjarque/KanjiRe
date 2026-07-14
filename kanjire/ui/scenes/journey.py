"""Journey mode: the long road from zero to reader, one station at a time.

The JLPT deck, sorted by real-world frequency, becomes a road of *stations*
of 15 words each. A station is **cleared** when you know 12 of its 15 words
(the classifier decides — however you learned them). Every fifth station is
a **boss**: a hearts-on survival session over the hardest words of the last
five stations. Everything is always clickable — the road suggests an order,
it never locks you in.

Clicking a station starts a finite session (session_mode) over its words,
with the usual typed-recall epilogue.
"""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.data import db
from kanjire.data.stats import classify, knowledge_score
from kanjire.game.config import GameConfig
from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button
from kanjire.ui.widgets.tabs import TabBar

STATION_SIZE = 15
CLEAR_AT = 12          # known words needed to clear a station
BOSS_EVERY = 5
COLS = 6
ROWS_VISIBLE = 5


class JourneyScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)

        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_JOURNEY"),  lambda: None),
             (tr("NAV_READ"),     lambda: self.app.go_reading()),
             (tr("NAV_STATS"),    lambda: self.app.go_stats()),
             (tr("NAV_FRIENDS"),  lambda: self.app.go_friends()),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_JOURNEY"))

        # The road: frequency-ordered JLPT words in stations of 15.
        try:
            pool = db.load_words(app.con, decks=["jlpt"], require_kanji=True)
        except Exception:
            pool = []
        pool.sort(key=lambda w: -w.freq)
        self.stations = [pool[i:i + STATION_SIZE]
                         for i in range(0, len(pool), STATION_SIZE)]
        if self.stations and len(self.stations[-1]) < 4:
            self.stations.pop()

        # Per-word bucket snapshot (one pass over stats).
        rows = {(r["expression"], r["reading"]): r
                for r in app.stats.all_rows()}
        self._known_counts: list[int] = []
        for st in self.stations:
            n = sum(1 for w in st
                    if classify(rows.get((w.expression, w.reading))) == "known")
            self._known_counts.append(n)
        self._stats_rows = rows
        self.frontier = next(
            (i for i, n in enumerate(self._known_counts) if n < CLEAR_AT),
            max(0, len(self.stations) - 1),
        )
        # Start the window a row above the frontier.
        self.scroll_row = max(0, self.frontier // COLS - 1)

        def lbl(size, color, *, bold=False, anchor_x="center"):
            out = Label("", font_name=JP_FONT, font_size=size, bold=bold,
                        color=theme.with_alpha(color, 255),
                        anchor_x=anchor_x, anchor_y="center",
                        batch=self.batch, group=self.g_text)
            out._base_fs = size
            return out

        self.title = lbl(16, theme.MUTED, bold=True)
        self.title.text = tr("JOURNEY_TITLE")
        self.progress = lbl(12, theme.DIM)
        self.hover_info = lbl(13, theme.GOLD)
        self.labels = [self.title, self.progress, self.hover_info]
        cleared = sum(1 for n in self._known_counts if n >= CLEAR_AT)
        known_total = sum(self._known_counts)
        self.progress.text = tr("JOURNEY_PROGRESS", cleared=cleared,
                                total=len(self.stations), words=known_total)

        self._node_buttons: list[tuple[int, Button]] = []
        self._rebuild_nodes()

    # ------------------------------------------------------------------ #
    def _is_boss(self, i: int) -> bool:
        return (i + 1) % BOSS_EVERY == 0

    def _rows_fit(self) -> int:
        """How many station rows the current window height can hold."""
        s = scale_for(self.width, self.height)
        return max(3, int((self.height - 240 * s) // (68 * s)))

    def _rebuild_nodes(self) -> None:
        for _i, b in self._node_buttons:
            b.delete()
        self._node_buttons.clear()
        start = self.scroll_row * COLS
        for i in range(start, min(start + COLS * self._rows_fit(),
                                  len(self.stations))):
            n_known = self._known_counts[i]
            cleared = n_known >= CLEAR_AT
            boss = self._is_boss(i)
            if cleared:
                accent, text = theme.SUCCESS, f"★ {i + 1}"
            elif boss:
                accent, text = theme.DANGER, f"鬼 {i + 1}"
            elif i == self.frontier:
                accent, text = theme.GOLD, f"● {i + 1}"
            else:
                accent, text = theme.DIM, str(i + 1)
            b = Button(text, lambda i=i: self._play_station(i),
                       self.batch, self.g_bg, self.g_text,
                       accent=accent, font_size=14)
            if i == self.frontier:
                b.set_selected(True)
            self._node_buttons.append((i, b))
        self.on_resize(self.width, self.height)

    def _play_station(self, i: int) -> None:
        words = list(self.stations[i])
        if self._is_boss(i):
            # Boss: the hardest words of the last five stations, with hearts.
            lo = max(0, i - BOSS_EVERY + 1)
            candidates = [w for st in self.stations[lo:i + 1] for w in st]
            candidates.sort(key=lambda w: knowledge_score(
                self._stats_rows.get((w.expression, w.reading)) or {}))
            words = candidates[:20]
            cfg = GameConfig(
                name=f"Journey boss {i + 1}",
                decks=("jlpt",), levels=(),
                words_per_round=5, duration=None, max_mistakes=None,
                mismatch_penalty=0, repetitions=1, session_mode=True,
                lives_mode=True, start_lives=3, max_lives=5, heart_chance=0.5,
            )
        else:
            cfg = GameConfig(
                name=f"Journey {i + 1}",
                decks=("jlpt",), levels=(),
                words_per_round=5, duration=None, max_mistakes=None,
                mismatch_penalty=0, repetitions=1, session_mode=True,
            )
        # Typed-recall epilogue over the station's trickiest known words.
        hard = sorted(words, key=lambda w: knowledge_score(
            self._stats_rows.get((w.expression, w.reading)) or {}))
        self.app.go_game(cfg, pool=words, recall_words=hard[:5])

    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.nav.on_mouse_press(x, y):
            return
        for _i, b in self._node_buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        hover = None
        for i, b in self._node_buttons:
            over = b.contains(x, y)
            b.set_hover(over)
            if over:
                hover = i
        if hover is None:
            self.hover_info.text = ""
        else:
            n = self._known_counts[hover]
            kind = (tr("JOURNEY_BOSS") if self._is_boss(hover)
                    else tr("JOURNEY_STATION"))
            self.hover_info.text = tr(
                "JOURNEY_NODE", kind=kind, n=hover + 1,
                known=n, total=len(self.stations[hover]))

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        max_row = max(0, (len(self.stations) - 1) // COLS
                      - self._rows_fit() + 1)
        self.scroll_row = max(0, min(max_row,
                                     self.scroll_row - int(scroll_y)))
        self._rebuild_nodes()

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key
        if symbol == key.ESCAPE:
            self.app.go_menu()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        # More (or fewer) rows fit after a resize: rebuild the node window.
        want = min(COLS * self._rows_fit(),
                   max(0, len(self.stations) - self.scroll_row * COLS))
        if want != len(self._node_buttons):
            self._rebuild_nodes()   # re-enters on_resize once, then stable
            return
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        self.nav.set_scale(s)
        cx = width / 2
        self.nav.set_rect(cx - 350 * s, height - 50 * s, 700 * s, 36 * s)
        self.title.x, self.title.y = cx, height - 92 * s
        self.progress.x, self.progress.y = cx, height - 118 * s

        bw, bh = 96 * s, 46 * s
        gapx, gapy = 18 * s, 22 * s
        grid_w = COLS * bw + (COLS - 1) * gapx
        x0 = cx - grid_w / 2
        y0 = height - 170 * s
        for idx, (i, b) in enumerate(self._node_buttons):
            r, c = divmod(idx, COLS)
            b.set_scale(s)
            b.set_rect(x0 + c * (bw + gapx), y0 - r * (bh + gapy) - bh,
                       bw, bh)
        self.hover_info.x, self.hover_info.y = cx, 48 * s

    def draw(self) -> None:
        h = round(64 * getattr(self, "_s", 1.0))
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        self.nav.delete()
        for _i, b in self._node_buttons:
            b.delete()
