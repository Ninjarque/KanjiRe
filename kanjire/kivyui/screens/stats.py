"""Stats tab: overview tiles + searchable per-word list.

Reads the same ``word_stats`` rows the desktop Stats scene shows; word rows
render in a RecycleView so scrolling 8k+ words stays smooth on a phone.
"""
from __future__ import annotations

from kivy.factory import Factory
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.screenmanager import Screen
from kivy.uix.textinput import TextInput

from kanjire.data.stats import classify, knowledge_score
from kanjire.i18n import tr
from kanjire.kivyui.fonts import UI_FONT
from kanjire.kivyui.theming import rgba, theme
from kanjire.kivyui.widgets import JPLabel, Panel, SectionLabel

_BUCKET_COL = {"known": "SUCCESS", "less_known": "GOLD", "unknown": "DANGER"}


class WordRow(BoxLayout):
    """One word_stats row: expression+reading | bucket dot | matches/misses."""

    def __init__(self, **kw):
        kw.setdefault("orientation", "horizontal")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(44))
        kw.setdefault("padding", [dp(6), 0])
        super().__init__(**kw)
        self.lbl_word = JPLabel(halign="left", valign="middle",
                                font_size=sp(15), size_hint_x=0.62)
        self.lbl_word.bind(size=self.lbl_word.setter("text_size"))
        self.lbl_counts = JPLabel(halign="right", valign="middle",
                                  font_size=sp(12.5),
                                  color=rgba(theme.MUTED), size_hint_x=0.38)
        self.lbl_counts.bind(size=self.lbl_counts.setter("text_size"))
        self.add_widget(self.lbl_word)
        self.add_widget(self.lbl_counts)

    # RecycleView sets attributes named after `data` keys; forward them.
    word = property(fset=lambda self, v: setattr(self.lbl_word, "text", v))
    counts = property(fset=lambda self, v: setattr(self.lbl_counts, "text", v))
    word_color = property(
        fset=lambda self, v: setattr(self.lbl_word, "color", v))


Factory.register("WordRow", cls=WordRow)


class StatsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self._app = app
        self._rows: list[dict] = []
        self._build()

    def on_pre_enter(self, *_):
        self._reload()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self.clear_widgets()
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                         spacing=dp(8))
        root.add_widget(JPLabel(text=tr("STATS_TITLE"), bold=True,
                                font_size=sp(22), size_hint_y=None,
                                height=dp(34)))

        # ---- overview tiles --------------------------------------------- #
        self._tiles = GridLayout(cols=4, spacing=dp(6), size_hint_y=None,
                                 height=dp(64))
        root.add_widget(self._tiles)
        self._lbl_acc = JPLabel(color=rgba(theme.MUTED), font_size=sp(12),
                                halign="left", size_hint_y=None,
                                height=dp(20))
        self._lbl_acc.bind(size=self._lbl_acc.setter("text_size"))
        root.add_widget(self._lbl_acc)

        # ---- search + list ----------------------------------------------- #
        self._search = TextInput(
            hint_text=tr("SEARCH_WORDS"), multiline=False,
            font_name=UI_FONT, font_size=sp(15),
            size_hint_y=None, height=dp(42),
            background_color=rgba(theme.PANEL),
            foreground_color=rgba(theme.TEXT),
            hint_text_color=rgba(theme.DIM),
            cursor_color=rgba(theme.ACCENT),
            padding=[dp(10), dp(10)])
        self._search.bind(text=lambda *_: self._refill())
        root.add_widget(self._search)

        self._rv = RecycleView(bar_width=dp(3))
        from kivy.uix.recycleboxlayout import RecycleBoxLayout
        layout = RecycleBoxLayout(orientation="vertical",
                                  default_size=(None, dp(44)),
                                  default_size_hint=(1, None),
                                  size_hint_y=None)
        layout.bind(minimum_height=layout.setter("height"))
        self._rv.add_widget(layout)
        # viewclass forwards to the layout manager — set it only AFTER the
        # RecycleBoxLayout is attached, or the assignment is silently lost.
        self._rv.viewclass = "WordRow"
        root.add_widget(self._rv)

        self._empty = JPLabel(text=tr("STATS_EMPTY"),
                              color=rgba(theme.DIM), font_size=sp(13),
                              size_hint_y=None, height=dp(0), opacity=0)
        root.add_widget(self._empty)
        self.add_widget(root)

    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        stats = self._app.stats
        ov = stats.overview()
        buckets = stats.bucket_counts()
        self._tiles.clear_widgets()
        for key, value in (
            ("TILE_WORDS", ov.get("total_words", 0)),
            ("TILE_KNOWN", buckets.get("known", 0)),
            ("TILE_STRUGGLING", buckets.get("less_known", 0)),
            ("TILE_UNKNOWN", buckets.get("unknown", 0)),
        ):
            self._tiles.add_widget(_Tile(tr(key), f"{value:,}"))

        seen = ov.get("total_seen", 0)
        matches = ov.get("total_matches", 0)
        miss = (ov.get("m_kanji", 0) + ov.get("m_reading", 0)
                + ov.get("m_meaning", 0))
        acc = int(matches / (matches + miss) * 100) if (matches + miss) else 100
        self._lbl_acc.text = tr("ACCURACY_LINE", acc=acc, match=matches,
                                miss=miss, seen=seen)

        rows = stats.all_rows()
        rows.sort(key=lambda r: knowledge_score(r))
        self._rows = rows
        self._refill()

        empty = not rows
        self._empty.opacity = 1 if empty else 0
        self._empty.height = dp(40) if empty else 0

    def _refill(self) -> None:
        q = (self._search.text or "").strip().lower()
        data = []
        for r in self._rows:
            expr = r.get("expression", "")
            read = r.get("reading", "")
            if q and q not in expr.lower() and q not in read.lower():
                continue
            bucket = classify(r)
            miss = (r.get("mistakes_kanji", 0) + r.get("mistakes_reading", 0)
                    + r.get("mistakes_meaning", 0))
            # 正/誤 (correct/wrong): guaranteed glyphs in the bundled JP fonts,
            # unlike ✓/✕ which Zen Maru Gothic lacks.
            data.append({
                "word": f"{expr}  {read}",
                "counts": f"正{r.get('matches', 0)}  誤{miss}",
                "word_color": rgba(getattr(theme, _BUCKET_COL[bucket])),
            })
        self._rv.data = data


class _Tile(Panel):
    def __init__(self, title: str, value: str, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", [dp(4), dp(6)])
        super().__init__(**kw)
        v = JPLabel(text=value, bold=True, font_size=sp(18))
        t = JPLabel(text=title, color=rgba(theme.DIM), font_size=sp(9))
        self.add_widget(v)
        self.add_widget(t)
