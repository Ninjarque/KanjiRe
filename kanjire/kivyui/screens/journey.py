"""Journey on Kivy: the frequency-ordered road of 15-word stations.

Same rules as desktop: a station clears at 12/15 known (however you learned
them), every fifth is a 鬼 boss (hearts over the last five stations' hardest
words), everything stays clickable. The road renders through a RecycleView —
building ~540 live buttons made fast scrolling freeze on phones; recycled
cells keep only a screenful of widgets alive.
"""
from __future__ import annotations

from kivy.clock import Clock
from kivy.factory import Factory
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.screenmanager import Screen

from kanjire.data import db
from kanjire.data.stats import classify, knowledge_score
from kanjire.game.config import GameConfig
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel

STATION_SIZE = 15
CLEAR_AT = 12
BOSS_EVERY = 5
COLS = 4


class JourneyCell(ButtonBehavior, JPLabel):
    """One recycled station button: rounded fill + label, data-driven."""

    def __init__(self, **kw):
        kw.setdefault("font_name", UI_FONT)
        kw.setdefault("font_size", sp(14))
        super().__init__(**kw)
        self.station = -1
        with self.canvas.before:
            self._col = Color(*rgba(theme.PANEL_HI))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[dp(10)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    # RecycleView data adapters
    fill = property(fset=lambda self, v: setattr(self._col, "rgba", v))

    def on_release(self):
        if self.station < 0:
            return
        from kivy.app import App
        App.get_running_app().sm.get_screen("journey") \
            ._play_station(self.station)


Factory.register("JourneyCell", cls=JourneyCell)


class JourneyScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._built = False

    def on_pre_enter(self, *_):
        # Rebuilt on every visit: station states move with the player's stats.
        self._reload()

    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        app = self._app
        try:
            pool = db.load_words(app.con, decks=["jlpt"], require_kanji=True)
        except Exception:
            pool = []
        pool.sort(key=lambda w: -w.freq)
        self.stations = [pool[i:i + STATION_SIZE]
                         for i in range(0, len(pool), STATION_SIZE)]
        if self.stations and len(self.stations[-1]) < 4:
            self.stations.pop()

        rows = {(r["expression"], r["reading"]): r
                for r in app.stats.all_rows()}
        self._stats_rows = rows
        self._known_counts = [
            sum(1 for w in st
                if classify(rows.get((w.expression, w.reading))) == "known")
            for st in self.stations
        ]
        self.frontier = next(
            (i for i, n in enumerate(self._known_counts) if n < CLEAR_AT),
            max(0, len(self.stations) - 1),
        )
        if not self._built:
            self._build()
        self._refill()

    def _build(self) -> None:
        self._built = True
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        root.add_widget(JPLabel(text=tr("JOURNEY_TITLE"), bold=True,
                                font_size=sp(20), size_hint_y=None,
                                height=dp(30)))
        self._prog = JPLabel(text="", color=rgba(theme.DIM),
                             font_size=sp(11.5), halign="left",
                             size_hint_y=None, height=dp(20))
        self._prog.bind(size=self._prog.setter("text_size"))
        root.add_widget(self._prog)

        self._rv = RecycleView(bar_width=dp(3))
        from kivy.uix.recyclegridlayout import RecycleGridLayout
        layout = RecycleGridLayout(cols=COLS, default_size=(None, dp(52)),
                                   default_size_hint=(1, None),
                                   spacing=dp(8), size_hint_y=None,
                                   padding=[0, dp(4)])
        layout.bind(minimum_height=layout.setter("height"))
        self._rv.add_widget(layout)
        self._rv.viewclass = "JourneyCell"   # AFTER the layout manager
        root.add_widget(self._rv)
        self.add_widget(root)

    def _refill(self) -> None:
        cleared = sum(1 for n in self._known_counts if n >= CLEAR_AT)
        self._prog.text = tr("JOURNEY_PROGRESS", cleared=cleared,
                             total=len(self.stations),
                             words=sum(self._known_counts))
        data = []
        for i in range(len(self.stations)):
            done = self._known_counts[i] >= CLEAR_AT
            boss = self._is_boss(i)
            if done:
                fill, text, fg = theme.SUCCESS, f"★ {i + 1}", None
            elif boss:
                fill, text, fg = theme.DANGER, f"鬼 {i + 1}", None
            elif i == self.frontier:
                fill, text, fg = theme.GOLD, f"● {i + 1}", None
            else:
                fill, text, fg = theme.PANEL_HI, str(i + 1), theme.MUTED
            data.append({
                "text": text,
                "fill": rgba(fill),
                "color": rgba(fg if fg is not None
                              else theme.readable_on(fill)),
                "station": i,
            })
        self._rv.data = data
        # Open the view at the frontier (after the layout settles).
        Clock.schedule_once(lambda *_: self._scroll_to_frontier(), 0)

    def _scroll_to_frontier(self) -> None:
        total_rows = max(1, (len(self.stations) + COLS - 1) // COLS)
        row = self.frontier // COLS
        # scroll_y: 1 = top. Aim a bit above the frontier row.
        frac = max(0, row - 1) / max(1, total_rows - 1)
        self._rv.scroll_y = max(0.0, min(1.0, 1.0 - frac))

    # ------------------------------------------------------------------ #
    def _is_boss(self, i: int) -> bool:
        return (i + 1) % BOSS_EVERY == 0

    def _play_station(self, i: int) -> None:
        words = list(self.stations[i])
        if self._is_boss(i):
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
                lives_mode=True, start_lives=3, max_lives=5,
                heart_chance=0.5,
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
        self._app.go_game(cfg, pool=words, recall_words=hard[:5])