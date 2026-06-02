"""End-of-game screen: score, stats, and a review of the words you matched."""
from __future__ import annotations

import pyglet
from pyglet.graphics import OrderedGroup
from pyglet.text import Label

from kanjire.i18n import tr
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.button import Button

MAX_REVIEW = 18


class ResultsScene(Scene):
    def __init__(self, app, engine, config) -> None:
        super().__init__(app)
        self.engine = engine
        self.config = config
        self.is_record = app.state.record_score(config.name, engine.score)

        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_text = OrderedGroup(1)
        self.buttons: list[Button] = []
        self.labels: list[Label] = []
        self._build()

    # ------------------------------------------------------------------ #
    def _lbl(self, text, size, color, x=0, y=0, bold=False, anchor_x="center") -> Label:
        lbl = Label(
            text, font_name=JP_FONT, font_size=size, bold=bold,
            color=theme.with_alpha(color, 255),
            anchor_x=anchor_x, anchor_y="center", x=x, y=y,
            batch=self.batch, group=self.g_text,
        )
        lbl._base_fs = size  # for resolution rescaling in on_resize
        self.labels.append(lbl)
        return lbl

    def _build(self) -> None:
        e = self.engine
        self.title = self._lbl(
            tr("RESULTS_TIME") if self.config.timed else tr("RESULTS_OVER"),
            30, theme.TEXT, bold=True,
        )
        self.score = self._lbl(f"{e.score:,}", 64, theme.GOLD, bold=True)
        self.record = self._lbl(
            tr("RESULTS_NEW_BEST") if self.is_record else "",
            18, theme.SUCCESS, bold=True,
        )

        acc = f"{e.accuracy * 100:.0f}%"
        self.stats = [
            (tr("STAT_ROUNDS"),     str(e.rounds_completed)),
            (tr("STAT_MATCHES"),    str(e.matches)),
            (tr("STAT_BEST_COMBO"), f"{e.best_combo}x"),
            (tr("STAT_ACCURACY"),   acc),
            (tr("STAT_MISTAKES"),   str(e.mistakes)),
            (tr("STAT_LEARNED"),    str(e.words_learned)),
        ]
        self.stat_labels = [
            (self._lbl(v, 26, theme.TEXT, bold=True), self._lbl(k, 12, theme.MUTED))
            for k, v in self.stats
        ]

        self.review_title = self._lbl(tr("RESULTS_REVIEW"), 13, theme.MUTED, bold=True)
        self.review_labels = []
        for w in e.seen_words[:MAX_REVIEW]:
            meaning = w.meaning if len(w.meaning) <= 26 else w.meaning[:25] + "…"
            self.review_labels.append(
                self._lbl(f"{w.expression}  ·  {w.reading}", 15, theme.ACCENT,
                          anchor_x="left")
            )
            self.review_labels.append(
                self._lbl(meaning, 12, theme.MUTED, anchor_x="left")
            )
        if e.words_learned > MAX_REVIEW:
            self.more_label = self._lbl(
                tr("RESULTS_MORE", n=e.words_learned - MAX_REVIEW), 12, theme.DIM
            )
        else:
            self.more_label = None

        self.again_btn = Button(tr("BTN_AGAIN"), self._again, self.batch,
                                self.g_bg, self.g_text, accent=theme.SUCCESS, font_size=17)
        self.menu_btn = Button(tr("BTN_MENU"), lambda: self.app.go_menu(), self.batch,
                               self.g_bg, self.g_text, accent=theme.ACCENT, font_size=17)
        self.buttons += [self.again_btn, self.menu_btn]

    def _again(self) -> None:
        self.app.go_game(self.config)

    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                break

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        for b in self.buttons:
            b.set_hover(b.contains(x, y))

    def on_key_press(self, symbol, modifiers) -> None:
        from pyglet.window import key

        if symbol in (key.ENTER, key.RETURN, key.SPACE):
            self._again()
        elif symbol == key.ESCAPE:
            self.app.go_menu()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        for lbl in self.labels:
            lbl.font_size = max(8, round(lbl._base_fs * s))
        for b in self.buttons:
            b.set_scale(s)
        cx = width / 2
        y = height - 70 * s
        self.title.x, self.title.y = cx, y
        y -= 64 * s
        self.score.x, self.score.y = cx, y
        y -= 44 * s
        self.record.x, self.record.y = cx, y

        # stats row (6 across)
        y -= 56 * s
        n = len(self.stat_labels)
        col_w = min(180 * s, (width - 80 * s) / n)
        x0 = cx - col_w * n / 2 + col_w / 2
        for i, (val, key_lbl) in enumerate(self.stat_labels):
            x = x0 + i * col_w
            val.x, val.y = x, y
            key_lbl.x, key_lbl.y = x, y - 24 * s

        # review block
        y -= 70 * s
        self.review_title.x, self.review_title.y = cx, y
        y -= 30 * s
        cols = 3
        per_col = (MAX_REVIEW + cols - 1) // cols
        col_w = min(300 * s, (width - 80 * s) / cols)
        block_x0 = cx - col_w * cols / 2
        line_h = 44 * s
        pairs = list(zip(self.review_labels[0::2], self.review_labels[1::2]))
        for idx, (top, bottom) in enumerate(pairs):
            col = idx // per_col
            row = idx % per_col
            lx = block_x0 + col * col_w + 16 * s
            ly = y - row * line_h
            top.x, top.y = lx, ly
            bottom.x, bottom.y = lx, ly - 17 * s
        if self.more_label is not None:
            self.more_label.x = cx
            self.more_label.y = y - per_col * line_h - 6 * s

        # buttons
        by = 56 * s
        self.again_btn.set_rect(cx - 230 * s, by, 220 * s, 50 * s)
        self.menu_btn.set_rect(cx + 20 * s, by, 150 * s, 50 * s)

    # ------------------------------------------------------------------ #
    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        self.batch.draw()

    def on_exit(self) -> None:
        for b in self.buttons:
            b.delete()
