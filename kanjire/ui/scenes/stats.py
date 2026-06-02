"""Stats screen: Overview / Words / Kanji tabs, with search + reset.

The Words and Kanji tabs share a row-list layout (sortable, scrollable, with a
search box). Right-click a Words row to reset that word's stats. The Kanji tab
aggregates the existing word_stats data per-character so we don't need another
schema for it.
"""
from __future__ import annotations

import pyglet
from pyglet import shapes
from pyglet.graphics import OrderedGroup
from pyglet.text import Label
from pyglet.window import key, mouse

from kanjire.data.stats import classify, knowledge_score
from kanjire.i18n import tr
from kanjire.jputil import capitalize_first, kanji_chars
from kanjire.ui import theme
from kanjire.ui.fonts import JP_FONT
from kanjire.ui.gfx import fill_quad
from kanjire.ui.metrics import scale_for
from kanjire.ui.scene import Scene
from kanjire.ui.widgets.panel import Panel
from kanjire.ui.widgets.tabs import TabBar
from kanjire.ui.widgets.textinput import TextInput

ROW_H = 28

# Column tuples: (data key, translation key for header label, alignment)
WORD_COLUMNS = (
    ("expression",       "COL_WORD",         "left"),
    ("reading",          "COL_READING",      "left"),
    ("meaning",          "COL_MEANING",      "left"),
    ("seen",             "COL_SEEN",         "right"),
    ("matches",          "COL_MATCH",        "right"),
    ("mistakes_kanji",   "COL_KANJI_BANG",   "right"),
    ("mistakes_reading", "COL_READING_BANG", "right"),
    ("mistakes_meaning", "COL_MEANING_BANG", "right"),
    ("score",            "COL_SCORE",        "right"),
    ("bucket",           "COL_BUCKET",       "left"),
)
KANJI_COLUMNS = (
    ("char",             "COL_KANJI",        "left"),
    ("words",            "COL_WORDS",        "right"),
    ("seen",             "COL_SEEN",         "right"),
    ("matches",          "COL_MATCH",        "right"),
    ("mistakes_kanji",   "COL_KANJI_BANG",   "right"),
    ("mistakes_reading", "COL_READING_BANG", "right"),
    ("mistakes_meaning", "COL_MEANING_BANG", "right"),
    ("score",            "COL_SCORE",        "right"),
    ("bucket",           "COL_BUCKET",       "left"),
)

BUCKET_KEYS = {
    "known":      "BUCKET_KNOWN",
    "less_known": "BUCKET_LESS_KNOWN",
    "unknown":    "BUCKET_UNKNOWN",
}


def _aggregate_kanji(word_rows: list[dict]) -> list[dict]:
    """Sum per-word stats into per-kanji rows."""
    by_char: dict[str, dict] = {}
    for r in word_rows:
        chars = set(kanji_chars(r.get("expression") or ""))
        for ch in chars:
            entry = by_char.setdefault(ch, {
                "char": ch, "words": 0, "seen": 0, "matches": 0,
                "mistakes_kanji": 0, "mistakes_reading": 0,
                "mistakes_meaning": 0,
            })
            entry["words"]            += 1
            entry["seen"]             += r.get("seen") or 0
            entry["matches"]          += r.get("matches") or 0
            entry["mistakes_kanji"]   += r.get("mistakes_kanji") or 0
            entry["mistakes_reading"] += r.get("mistakes_reading") or 0
            entry["mistakes_meaning"] += r.get("mistakes_meaning") or 0
    for entry in by_char.values():
        entry["score"]  = knowledge_score(entry)
        entry["bucket"] = classify(entry) if entry["seen"] else "unknown"
    return list(by_char.values())


def _matches_query(row: dict, q: str, fields: tuple[str, ...]) -> bool:
    if not q:
        return True
    q = q.lower()
    for f in fields:
        v = row.get(f)
        if v is not None and q in str(v).lower():
            return True
    return False


# --------------------------------------------------------------------------- #
class StatsScene(Scene):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.batch = pyglet.graphics.Batch()
        self.g_bg = OrderedGroup(0)
        self.g_panel = OrderedGroup(1)
        self.g_text = OrderedGroup(2)
        self.g_overlay = OrderedGroup(3)

        # Top + inner tabs
        self.nav = TabBar(
            [(tr("NAV_PLAY"),     lambda: self.app.go_menu()),
             (tr("NAV_STATS"),    lambda: None),
             (tr("NAV_SETTINGS"), lambda: self.app.go_settings())],
            self.batch, self.g_bg, self.g_text,
            accent=theme.ACCENT, font_size=14,
        )
        self.nav.set_active(tr("NAV_STATS"))
        self.inner = TabBar(
            [(tr("INNER_OVERVIEW"), lambda: self._set_tab("Overview")),
             (tr("INNER_WORDS"),    lambda: self._set_tab("Words")),
             (tr("INNER_KANJI"),    lambda: self._set_tab("Kanji"))],
            self.batch, self.g_bg, self.g_text,
            accent=theme.GOLD, font_size=13,
        )
        self.active_tab = "Overview"
        self.inner.set_active(0)  # Overview is at index 0

        # Load data once on entry.
        self._all_words = app.stats.all_rows()
        for r in self._all_words:
            r["score"]  = knowledge_score(r)
            r["bucket"] = classify(r) if r.get("seen") else "unknown"
        self._all_kanji = _aggregate_kanji(self._all_words)

        # Per-tab list-display state
        self._sort_col: dict[str, str] = {"Words": "score", "Kanji": "score"}
        self._sort_dir: dict[str, str] = {"Words": "asc",   "Kanji": "asc"}
        self._scroll:   dict[str, int] = {"Words": 0,        "Kanji": 0}
        self._query:    dict[str, str] = {"Words": "",       "Kanji": ""}
        self._filtered: dict[str, list[dict]] = {"Words": [], "Kanji": []}

        # Live list-display widgets (rebuilt on filter/sort/scroll/tab change)
        self._row_labels: list[Label] = []
        self._stripes: list[shapes.Rectangle] = []
        self._header_labels: dict[str, list[Label]] = {"Words": [], "Kanji": []}

        # Search inputs (one per list tab so each remembers its query)
        self._search: dict[str, TextInput] = {
            "Words": TextInput(self.batch, self.g_panel, self.g_text, self.g_text,
                               placeholder=tr("SEARCH_WORDS"),
                               on_change=lambda q: self._on_search("Words", q)),
            "Kanji": TextInput(self.batch, self.g_panel, self.g_text, self.g_text,
                               placeholder=tr("SEARCH_KANJI"),
                               on_change=lambda q: self._on_search("Kanji", q)),
        }

        # A framing card behind the tab content (purely decorative; sits in the
        # bg group so stripes/text/search render on top of it).
        self.content_panel = Panel(self.batch, self.g_bg, self.g_text)

        # Resolution scale (refreshed in on_resize); drives fonts + row height.
        self._s = 1.0
        self._row_h = ROW_H

        self._build_overview()
        self._build_headers()
        self._apply_sort_and_filter("Words")
        self._apply_sort_and_filter("Kanji")

    # ------------------------------------------------------------------ #
    # Overview tab
    # ------------------------------------------------------------------ #
    def _label(self, text, size, color, *, bold=False, anchor_x="center",
                multiline=False, width=10, group=None):
        kw = dict(multiline=multiline, width=width, align="left") if multiline else {}
        lbl = Label(
            text, font_name=JP_FONT, font_size=max(8, round(size * self._s)), bold=bold,
            color=theme.with_alpha(color, 255),
            anchor_x=anchor_x, anchor_y="center",
            batch=self.batch, group=group or self.g_text, **kw,
        )
        lbl._base_fs = size  # remembered so on_resize can rescale the font
        return lbl

    def _build_overview(self) -> None:
        ov = self.app.stats.overview()
        bk = self.app.stats.bucket_counts()
        total_m = (ov.get("m_kanji") or 0) + (ov.get("m_reading") or 0) + (ov.get("m_meaning") or 0)
        total_match = ov.get("total_matches") or 0
        total_seen = ov.get("total_seen") or 0
        acc = (total_match / (total_match + total_m) * 100) if (total_match + total_m) else 0.0
        self._overview = ov

        self._ov_widgets: list = []
        def reg(w):
            self._ov_widgets.append(w)
            return w

        self._ov_title = reg(self._label(tr("STATS_TITLE"), 22, theme.TEXT,
                                          bold=True, anchor_x="left"))
        self._ov_sub = reg(self._label(
            tr("STATS_SUB"),
            12, theme.MUTED, anchor_x="left"))

        # 4 big-number tiles
        self._tiles = []
        for value, label, color in (
            (str(ov.get("total_words") or 0), tr("TILE_WORDS"),      theme.ACCENT),
            (str(bk["known"]),                tr("TILE_KNOWN"),      theme.SUCCESS),
            (str(bk["less_known"]),           tr("TILE_STRUGGLING"), theme.GOLD),
            (str(bk["unknown"]),              tr("TILE_UNKNOWN"),    theme.DIM),
        ):
            v = reg(self._label(value, 36, color, bold=True))
            l = reg(self._label(label, 11, theme.MUTED))
            self._tiles.append((v, l))

        # Mistake bars (per-face)
        self._face_title = reg(self._label(tr("WHERE_TITLE"), 16, theme.TEXT,
                                            bold=True, anchor_x="left"))
        self._face_bars = []
        for face, label in (("kanji",   tr("WHERE_KANJI")),
                             ("reading", tr("WHERE_READING")),
                             ("meaning", tr("WHERE_MEANING"))):
            color = theme.FACE_COLORS[face]
            bar = shapes.Rectangle(0, 0, 1, 8, color=color,
                                    batch=self.batch, group=self.g_panel)
            self._ov_widgets.append(bar)
            self._face_bars.append((
                reg(self._label(label, 12, theme.TEXT, anchor_x="left")),
                reg(self._label(str(ov.get(f"m_{face}") or 0), 13, color,
                                bold=True, anchor_x="right")),
                bar, face,
            ))

        self._accuracy = reg(self._label(
            tr("ACCURACY_LINE", acc=int(acc), match=total_match,
               miss=total_m, seen=total_seen),
            12, theme.MUTED,
        ))
        self._empty_hint = reg(self._label(
            tr("STATS_EMPTY"),
            14, theme.DIM,
        )) if (ov.get("total_words") or 0) == 0 else None

    # ------------------------------------------------------------------ #
    # List tabs (Words, Kanji)
    # ------------------------------------------------------------------ #
    def _columns_for(self, tab: str) -> tuple:
        return WORD_COLUMNS if tab == "Words" else KANJI_COLUMNS

    def _build_headers(self) -> None:
        for tab in ("Words", "Kanji"):
            for col_key, header_key, _align in self._columns_for(tab):
                lbl = self._label(tr(header_key), 11, theme.MUTED, bold=True,
                                   anchor_x="left")
                lbl.opacity = 0
                self._header_labels[tab].append(lbl)

    def _on_search(self, tab: str, q: str) -> None:
        self._query[tab] = q
        self._scroll[tab] = 0
        self._apply_sort_and_filter(tab)

    def _apply_sort_and_filter(self, tab: str) -> None:
        source = self._all_words if tab == "Words" else self._all_kanji
        q = self._query[tab]
        if tab == "Words":
            fields = ("expression", "reading", "meaning")
        else:
            fields = ("char",)
        rows = [r for r in source if _matches_query(r, q, fields)]
        key_col = self._sort_col[tab]
        rev = (self._sort_dir[tab] == "desc")
        rows.sort(key=lambda r: (r.get(key_col) if r.get(key_col) is not None else 0,
                                 r.get("expression") or r.get("char") or ""),
                  reverse=rev)
        self._filtered[tab] = rows
        if self.active_tab == tab:
            self._rebuild_rows()
            self._refresh_visibility()

    def _visible_rows(self) -> int:
        content_top = self.height - 200 * self._s
        content_bottom = 40 * self._s
        return max(1, int((content_top - content_bottom) // self._row_h))

    def _rebuild_rows(self) -> None:
        for lbl in self._row_labels:
            lbl.delete()
        self._row_labels.clear()
        for r in self._stripes:
            r.delete()
        self._stripes.clear()
        tab = self.active_tab
        if tab not in ("Words", "Kanji"):
            return
        n_visible = self._visible_rows()
        rows = self._filtered[tab]
        start = max(0, min(self._scroll[tab], max(0, len(rows) - n_visible)))
        self._scroll[tab] = start
        visible = rows[start: start + n_visible]
        cols = self._columns_for(tab)
        for i, row in enumerate(visible):
            stripe = shapes.Rectangle(
                0, 0, 1, self._row_h,
                color=(theme.PANEL if i % 2 else theme.BG),
                batch=self.batch, group=self.g_panel,
            )
            self._stripes.append(stripe)
            for col_key, _, _align in cols:
                val = row.get(col_key)
                if col_key == "score":
                    text = f"{(val or 0) * 100:.0f}"
                elif col_key == "bucket":
                    text = tr(BUCKET_KEYS.get(val, "")) if val in BUCKET_KEYS else (val or "")
                elif col_key == "meaning":
                    s = capitalize_first(val) or (val or "")
                    text = s if len(s) <= 26 else s[:25] + "…"
                else:
                    text = "" if val is None else str(val)
                self._row_labels.append(self._label(text, 11, theme.TEXT,
                                                     anchor_x="left"))

    # ------------------------------------------------------------------ #
    # Tab visibility
    # ------------------------------------------------------------------ #
    def _set_tab(self, name: str) -> None:
        if name == self.active_tab:
            return
        # Unfocus old search box when leaving its tab.
        for s in self._search.values():
            s.unfocus()
        self.active_tab = name
        self.inner.set_active({"Overview": 0, "Words": 1, "Kanji": 2}[name])
        if name in ("Words", "Kanji"):
            self._apply_sort_and_filter(name)
        else:
            self._rebuild_rows()
        self._refresh_visibility()
        self.on_resize(self.width, self.height)

    def _refresh_visibility(self) -> None:
        showing_overview = self.active_tab == "Overview"
        op_ov = 255 if showing_overview else 0
        for w in self._ov_widgets:
            if isinstance(w, Label):
                w.opacity = op_ov
            else:
                w.visible = showing_overview

        for tab in ("Words", "Kanji"):
            visible = self.active_tab == tab
            op = 255 if visible else 0
            for lbl in self._header_labels[tab]:
                lbl.opacity = op
            # Search inputs: keep present always, but hide non-active by moving
            # off-screen and clearing focus.
            si = self._search[tab]
            if visible:
                pass
            else:
                si.unfocus()
                si.set_rect(-4000, -4000, 1, 1)

        # Row labels and stripes are rebuilt per-tab, always visible when present
        for lbl in self._row_labels:
            lbl.opacity = 255 if self.active_tab in ("Words", "Kanji") else 0
        for r in self._stripes:
            r.visible = self.active_tab in ("Words", "Kanji")

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def on_mouse_press(self, x, y, button, modifiers) -> None:
        if self.nav.on_mouse_press(x, y):
            return
        if self.inner.on_mouse_press(x, y):
            return
        tab = self.active_tab
        if tab in ("Words", "Kanji"):
            if self._search[tab].on_mouse_press(x, y, button, modifiers):
                return
            # Header click -> sort
            header_y = self.height - 152 * self._s
            if abs(y - header_y) < 12 * self._s:
                col_idx = self._column_at_x(x)
                if col_idx is not None:
                    cols = self._columns_for(tab)
                    key_col = cols[col_idx][0]
                    if key_col == self._sort_col[tab]:
                        self._sort_dir[tab] = "desc" if self._sort_dir[tab] == "asc" else "asc"
                    else:
                        self._sort_col[tab] = key_col
                        self._sort_dir[tab] = "asc" if key_col in (
                            "expression", "reading", "meaning", "char", "bucket"
                        ) else "desc"
                    self._apply_sort_and_filter(tab)
                    return
            # Right-click row -> reset word (Words tab only)
            if button == mouse.RIGHT and tab == "Words":
                row_idx = self._row_at_y(y)
                if row_idx is not None:
                    row = self._filtered["Words"][row_idx]
                    self._confirm_reset_word(row["expression"], row["reading"])
                    return

    def _row_at_y(self, y: float) -> int | None:
        header_y = self.height - 152 * self._s
        first_row_y = header_y - 28 * self._s
        idx_visible = int((first_row_y - y + self._row_h / 2) // self._row_h)
        if idx_visible < 0:
            return None
        absolute = self._scroll[self.active_tab] + idx_visible
        rows = self._filtered.get(self.active_tab, [])
        if absolute >= len(rows):
            return None
        return absolute

    def _confirm_reset_word(self, expression: str, reading: str) -> None:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        try:
            ok = messagebox.askyesno(
                tr("RESET_WORD_TITLE"),
                tr("RESET_WORD_MSG", expr=expression, reading=reading),
                parent=root,
            )
        finally:
            try: root.destroy()
            except Exception: pass
        if not ok:
            return
        self.app.stats.reset_word(expression, reading)
        # Refresh in-memory data + redraw
        self._all_words = self.app.stats.all_rows()
        for r in self._all_words:
            r["score"]  = knowledge_score(r)
            r["bucket"] = classify(r) if r.get("seen") else "unknown"
        self._all_kanji = _aggregate_kanji(self._all_words)
        self._apply_sort_and_filter("Words")
        self._apply_sort_and_filter("Kanji")

    def _column_at_x(self, x: float) -> int | None:
        cols = self._columns_for(self.active_tab)
        for i, (cx, cw, _a) in enumerate(self._col_geometry(cols)):
            if cx <= x < cx + cw:
                return i
        return None

    def on_mouse_motion(self, x, y, dx, dy) -> None:
        self.nav.on_mouse_motion(x, y)
        self.inner.on_mouse_motion(x, y)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        tab = self.active_tab
        if tab not in ("Words", "Kanji"):
            return
        n_visible = self._visible_rows()
        max_scroll = max(0, len(self._filtered[tab]) - n_visible)
        self._scroll[tab] = max(0, min(max_scroll,
                                        self._scroll[tab] - int(scroll_y) * 3))
        self._rebuild_rows()
        self._layout_rows()

    def on_text(self, text) -> None:
        tab = self.active_tab
        if tab in ("Words", "Kanji"):
            self._search[tab].on_text(text)

    def on_text_motion(self, motion) -> None:
        tab = self.active_tab
        if tab in ("Words", "Kanji"):
            self._search[tab].on_text_motion(motion)

    def on_text_motion_select(self, motion) -> None:
        tab = self.active_tab
        if tab in ("Words", "Kanji"):
            self._search[tab].on_text_motion_select(motion)

    def on_key_press(self, symbol, modifiers) -> None:
        if symbol == key.ESCAPE:
            tab = self.active_tab
            if tab in ("Words", "Kanji") and self._search[tab].focused:
                self._search[tab].unfocus()
                return
            self.app.go_menu()

    # ------------------------------------------------------------------ #
    def on_resize(self, width, height) -> None:
        s = scale_for(width, height)
        self._s = s
        self._row_h = max(16, round(ROW_H * s))
        self.nav.set_scale(s)
        self.inner.set_scale(s)
        self.content_panel.set_scale(s)
        # Rescale every overview + header label font from its stored base.
        for w in self._ov_widgets:
            if isinstance(w, Label) and hasattr(w, "_base_fs"):
                w.font_size = max(8, round(w._base_fs * s))
        for t in ("Words", "Kanji"):
            for lbl in self._header_labels[t]:
                if hasattr(lbl, "_base_fs"):
                    lbl.font_size = max(8, round(lbl._base_fs * s))

        self.nav.set_rect(width / 2 - 240 * s, height - 50 * s, 480 * s, 36 * s)
        self.inner.set_rect(width / 2 - 200 * s, height - 100 * s, 400 * s, 32 * s)
        # Frame the content area (below the inner tabs, above the bottom margin).
        self.content_panel.set_rect(32 * s, 28 * s, width - 64 * s, height - 148 * s)
        # Rebuild table rows so their count + font track the new scale.
        if self.active_tab in ("Words", "Kanji"):
            self._rebuild_rows()
        self._layout_overview()
        self._layout_rows()
        # Ensure per-tab visibility is correct even on the Overview tab (where
        # _layout_rows returns early), so the inactive search boxes stay hidden.
        self._refresh_visibility()

    def _layout_overview(self) -> None:
        s = self._s
        cx = self.width / 2
        left = 60 * s
        y = self.height - 160 * s
        self._ov_title.x, self._ov_title.y = left, y
        self._ov_sub.x, self._ov_sub.y = left, y - 24 * s
        y -= 90 * s
        n = len(self._tiles)
        tile_w = min(220 * s, (self.width - 120 * s) / n)
        gap = (self.width - 120 * s - tile_w * n) / max(1, n - 1) if n > 1 else 0
        for i, (val, label) in enumerate(self._tiles):
            tx = left + i * (tile_w + gap) + tile_w / 2
            val.x, val.y = tx, y
            label.x, label.y = tx, y - 30 * s
        y -= 90 * s
        self._face_title.x, self._face_title.y = left, y
        max_m = max(self._overview.get("m_kanji") or 0,
                    self._overview.get("m_reading") or 0,
                    self._overview.get("m_meaning") or 0, 1)
        bar_x = 180 * s
        bar_max_w = self.width - 240 * s
        for i, (name_lbl, count_lbl, bar, face) in enumerate(self._face_bars):
            yy = y - 28 * s - i * 30 * s
            name_lbl.x, name_lbl.y = left, yy
            ratio = (self._overview.get(f"m_{face}") or 0) / max_m
            bar.x = bar_x
            bar.y = yy - 4 * s
            bar.height = max(4, round(8 * s))
            bar.width = max(1, int(bar_max_w * ratio))
            count_lbl.x, count_lbl.y = bar_x + bar_max_w, yy
        y -= 28 * s + len(self._face_bars) * 30 * s + 16 * s
        self._accuracy.x, self._accuracy.y = cx, y
        if self._empty_hint is not None:
            self._empty_hint.x, self._empty_hint.y = cx, y - 30 * s

    def _col_geometry(self, cols) -> list:
        """(x, width, align) per column, scaled — shared by layout + hit-test."""
        s = self._s
        left = 40 * s
        w = self.width - 80 * s
        wide_keys = ("expression", "reading", "meaning")
        n_wide = sum(1 for k, *_ in cols if k in wide_keys)
        wide_share = w * (0.50 if n_wide else 0.0)
        wide_each = (wide_share / n_wide) if n_wide else 0
        narrow_each = (w - wide_share) / max(1, len(cols) - n_wide)
        cursor = left
        col_xs = []
        for k, _, align in cols:
            cw = wide_each if k in wide_keys else narrow_each
            col_xs.append((cursor, cw, align))
            cursor += cw
        return col_xs

    def _layout_rows(self) -> None:
        tab = self.active_tab
        if tab not in ("Words", "Kanji"):
            return
        s = self._s
        # Search input at top
        self._search[tab].set_rect(40 * s, self.height - 138 * s, 360 * s, 28 * s)
        cols = self._columns_for(tab)
        col_xs = self._col_geometry(cols)

        header_y = self.height - 152 * s
        for i, lbl in enumerate(self._header_labels[tab]):
            x, cw, align = col_xs[i]
            lbl.x = x if align == "left" else x + cw - 6 * s
            lbl.y = header_y
            lbl.anchor_x = "left" if align == "left" else "right"
            # decorate with sort arrow
            base_text = tr(cols[i][1])
            key_col = cols[i][0]
            if key_col == self._sort_col[tab]:
                arrow = " ▲" if self._sort_dir[tab] == "asc" else " ▼"
            else:
                arrow = ""
            lbl.text = base_text + arrow

        # Row labels
        per_row = len(cols)
        n_visible = self._visible_rows()
        rows_to_show = min(len(self._row_labels) // per_row, n_visible)
        for row_idx in range(rows_to_show):
            row_y = header_y - 28 * s - row_idx * self._row_h
            stripe = self._stripes[row_idx] if row_idx < len(self._stripes) else None
            if stripe is not None:
                stripe.x = 40 * s
                stripe.y = row_y - self._row_h / 2 + 2 * s
                stripe.width = self.width - 80 * s
            for col_idx, (k, _, align) in enumerate(cols):
                lbl_idx = row_idx * per_row + col_idx
                if lbl_idx >= len(self._row_labels):
                    continue
                lbl = self._row_labels[lbl_idx]
                x, cw, _ = col_xs[col_idx]
                lbl.x = x if align == "left" else x + cw - 6 * s
                lbl.y = row_y
                lbl.anchor_x = "left" if align == "left" else "right"
        self._refresh_visibility()

    # ------------------------------------------------------------------ #
    def draw(self) -> None:
        # Flat background painted by window.clear() (glClearColor).
        h = round(64 * self._s)
        fill_quad(0, self.height - h, self.width, h, theme.PANEL)
        fill_quad(0, self.height - h - 2, self.width, 2, theme.PANEL_HI)
        self.batch.draw()

    def on_exit(self) -> None:
        self.nav.delete()
        self.inner.delete()
        self.content_panel.delete()
        for w in self._ov_widgets:
            try: w.delete()
            except Exception: pass
        for tab in ("Words", "Kanji"):
            for lbl in self._header_labels[tab]:
                lbl.delete()
        for lbl in self._row_labels:
            lbl.delete()
        for r in self._stripes:
            r.delete()
        for s in self._search.values():
            s.delete()
