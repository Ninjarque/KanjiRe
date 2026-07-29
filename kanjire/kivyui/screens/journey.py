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
from kivy.uix.gridlayout import GridLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from kanjire.data import db
from kanjire.data.genres import BY_KEY, GENRES
from kanjire.data.stats import classify, knowledge_score
from kanjire.game import genreprogress
from kanjire.game.config import GameConfig
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, ThemedButton

STATION_SIZE = 15
CLEAR_AT = 12
BOSS_EVERY = 5
#: Five wide on every platform, deliberately: with BOSS_EVERY = 5 the 鬼
#: bosses then stack in one column instead of drifting diagonally across
#: the grid. Keep this in sync with kanjire.ui.scenes.journey.COLS.
COLS = 5


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
        #: "road" = stations along the frequency line; "genres" = the
        #: same progress idea over forty topics, split by JLPT level.
        self.tab = "road"
        self.sel_genre = None

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
        self._genre_progress = genreprogress.build(pool, rows)
        if not self._built:
            self._build()
        self._refill()

    def _build(self) -> None:
        self._built = True
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        self._title = JPLabel(text=tr("JOURNEY_TITLE"), bold=True,
                              font_size=sp(20), size_hint_y=None,
                              height=dp(30))
        root.add_widget(self._title)
        self._prog = JPLabel(text="", color=rgba(theme.DIM),
                             font_size=sp(11.5), halign="left",
                             size_hint_y=None, height=dp(20))
        self._prog.bind(size=self._prog.setter("text_size"))
        root.add_widget(self._prog)

        # Road / Genres switch.
        tabs = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self._tab_btns = {}
        for key, tkey in (("road", "JOURNEY_TAB_ROAD"),
                          ("genres", "JOURNEY_TAB_GENRES")):
            b = ThemedButton(text=tr(tkey), font_size=sp(13), height=dp(38))
            b.bind(on_release=lambda w, k=key: self._set_tab(k))
            self._tab_btns[key] = b
            tabs.add_widget(b)
        root.add_widget(tabs)

        # The genre views live in their own scroller, shown *in place of* the
        # station grid. Swapped in and out of the tree rather than hidden:
        # an invisible-but-present panel still swallows every tap under it.
        self._genre_box = ScrollView(bar_width=dp(3))
        self._genre_body = GridLayout(cols=1, spacing=dp(6), size_hint_y=None,
                                      padding=[0, dp(4)])
        self._genre_body.bind(minimum_height=self._genre_body.setter("height"))
        self._genre_box.add_widget(self._genre_body)
        self._root = root

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

    # -- genres ---------------------------------------------------------- #
    def _set_tab(self, tab: str) -> None:
        self.tab = tab
        self.sel_genre = None
        self._refill()

    def _open_genre(self, key) -> None:
        self.sel_genre = key
        self._refill()

    def _use_genre(self) -> None:
        """Make this genre the Play tab's filter."""
        if not self.sel_genre:
            return
        state = self._app.state
        mode = state.last_mode or "Time Attack"
        settings = dict(state.last_for_mode(mode) or {})
        settings["genres"] = [self.sel_genre]
        state.set_last_for_mode(mode, settings)
        self._app.sm.current = "play"

    def _genre_fill(self, cell):
        """Base colour. How FAR you are is the progress bar's job — gold for
        "started" read as "done", so 5 of 30 looked finished."""
        return theme.PANEL if not cell.playable else theme.PANEL_HI

    def _genre_accent(self, cell):
        return theme.SUCCESS if cell.complete else theme.GOLD

    def _play_genre(self, cell) -> None:
        if not cell.playable:
            return
        words = list(cell.words)
        cfg = GameConfig(
            name=f"{cell.genre} N{cell.level}",
            decks=("jlpt",), levels=(),
            words_per_round=min(6, len(words)),
            duration=None, max_mistakes=None, mismatch_penalty=0,
            repetitions=1, session_mode=True, genres=(cell.genre,),
        )
        hard = sorted(words, key=lambda w: knowledge_score(
            self._stats_rows.get((w.expression, w.reading)) or {}))
        self._app.go_game(cfg, pool=words, recall_words=hard[:5])

    def _fill_genres(self) -> None:
        body = self._genre_body
        body.clear_widgets()
        if self.sel_genre is None:
            started, complete, known = genreprogress.totals(
                self._genre_progress)
            self._prog.text = tr("GENRES_PROGRESS", started=started,
                                 complete=complete, total=len(GENRES),
                                 words=known)
            grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
            grid.bind(minimum_height=grid.setter("height"))
            for g in GENRES:
                cell = self._genre_progress[g.key][None]
                fill = self._genre_fill(cell)
                b = ThemedButton(text=f"{g.icon} {tr(g.tr)}",
                                 font_size=sp(12.5), height=dp(46), fill=fill,
                                 text_color=theme.readable_on(fill))
                b.set_progress(cell.ratio, self._genre_accent(cell))
                b.disabled = not cell.playable
                b.bind(on_release=lambda w, k=g.key: self._open_genre(k))
                grid.add_widget(b)
            body.add_widget(grid)
            return

        g = BY_KEY[self.sel_genre]
        total = self._genre_progress[g.key][None]
        self._prog.text = tr("GENRE_NODE", known=total.known,
                             total=total.total)
        body.add_widget(JPLabel(text=f"{g.icon}  {tr(g.tr)}", bold=True,
                                font_size=sp(16), size_hint_y=None,
                                height=dp(30)))
        for lvl in genreprogress.LEVELS:
            cell = self._genre_progress[g.key][lvl]
            fill = self._genre_fill(cell)
            b = ThemedButton(text=f"N{lvl}   {cell.known}/{cell.total}",
                             font_size=sp(15), height=dp(48), fill=fill,
                             text_color=theme.readable_on(fill))
            b.set_progress(cell.ratio, self._genre_accent(cell))
            b.disabled = not cell.playable
            b.bind(on_release=lambda w, c=cell: self._play_genre(c))
            body.add_widget(b)
        use = ThemedButton(text=tr("GENRE_USE"), font_size=sp(13),
                           height=dp(42), fill=theme.PANEL_HI,
                           text_color=theme.GOLD)
        use.bind(on_release=lambda *_: self._use_genre())
        body.add_widget(use)
        back = ThemedButton(text=tr("GENRE_BACK"), font_size=sp(13),
                            height=dp(42), fill=theme.PANEL_HI,
                            text_color=theme.MUTED)
        back.bind(on_release=lambda *_: self._set_tab("genres"))
        body.add_widget(back)

    def _refill(self) -> None:
        for key, b in self._tab_btns.items():
            active = key == self.tab
            # set_fill, not `b.fill = ...`: the fill is plain state behind a
            # canvas instruction, so assigning the attribute repaints nothing.
            b.set_fill(theme.GOLD if active else theme.PANEL_HI,
                       None if active else theme.GOLD)
        showing_genres = self.tab == "genres"
        if showing_genres and self._genre_box.parent is None:
            self._root.remove_widget(self._rv)
            self._root.add_widget(self._genre_box)
        elif not showing_genres and self._rv.parent is None:
            self._root.remove_widget(self._genre_box)
            self._root.add_widget(self._rv)
        if showing_genres:
            self._title.text = tr("GENRES_TITLE")
            self._fill_genres()
            return
        self._title.text = tr("JOURNEY_TITLE")
        self._fill_road()

    def _fill_road(self) -> None:
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