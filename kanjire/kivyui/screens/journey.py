"""Journey on Kivy: the frequency-ordered road of 15-word stations.

Same rules as desktop: a station clears at 12/15 known (however you learned
them), every fifth is a 鬼 boss (hearts over the last five stations' hardest
words), everything stays clickable. The whole road scrolls; the view opens
at the frontier.
"""
from __future__ import annotations

from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from kanjire.data import db
from kanjire.data.stats import classify, knowledge_score
from kanjire.game.config import GameConfig
from kanjire.i18n import tr
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, ThemedButton

STATION_SIZE = 15
CLEAR_AT = 12
BOSS_EVERY = 5


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
        self._build()

    def _build(self) -> None:
        self.clear_widgets()
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        root.add_widget(JPLabel(text=tr("JOURNEY_TITLE"), bold=True,
                                font_size=sp(20), size_hint_y=None,
                                height=dp(30)))
        cleared = sum(1 for n in self._known_counts if n >= CLEAR_AT)
        prog = JPLabel(
            text=tr("JOURNEY_PROGRESS", cleared=cleared,
                    total=len(self.stations),
                    words=sum(self._known_counts)),
            color=rgba(theme.DIM), font_size=sp(11.5), halign="left",
            size_hint_y=None, height=dp(20))
        prog.bind(size=prog.setter("text_size"))
        root.add_widget(prog)

        self._scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        grid = GridLayout(cols=4, spacing=dp(8), size_hint_y=None,
                          padding=[0, dp(4)])
        grid.bind(minimum_height=grid.setter("height"))
        self._frontier_btn = None
        for i in range(len(self.stations)):
            n_known = self._known_counts[i]
            done = n_known >= CLEAR_AT
            boss = self._is_boss(i)
            if done:
                fill, text = theme.SUCCESS, f"★ {i + 1}"
            elif boss:
                fill, text = theme.DANGER, f"鬼 {i + 1}"
            elif i == self.frontier:
                fill, text = theme.GOLD, f"● {i + 1}"
            else:
                fill, text = theme.PANEL_HI, str(i + 1)
            b = ThemedButton(text=text, fill=fill, height=dp(52),
                             font_size=sp(14),
                             text_color=None if (done or boss
                                                 or i == self.frontier)
                             else theme.MUTED)
            b.bind(on_release=lambda w, i=i: self._play_station(i))
            grid.add_widget(b)
            if i == self.frontier:
                self._frontier_btn = b
        self._scroll.add_widget(grid)
        self._grid = grid
        root.add_widget(self._scroll)
        self.add_widget(root)
        # Open the view at the frontier (after layout settles).
        Clock.schedule_once(lambda *_: self._scroll_to_frontier(), 0)

    def _scroll_to_frontier(self) -> None:
        if self._frontier_btn is not None and self._grid.height > 0:
            try:
                self._scroll.scroll_to(self._frontier_btn, padding=dp(60),
                                       animate=False)
            except Exception:
                pass

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
