"""Real pages for the reader: measure the text, then cut it to fit.

The reader used to show a fixed forty sentences and let you scroll. Measuring
instead turns "Back / More" into actual pages — nothing is cut off, nothing
scrolls, and the button simply advances in the direction the text runs.

Two things make this less trivial than it sounds.

**English is wider than Japanese.** 大学 is two glyphs; "College" is seven.
A page laid out in Japanese therefore *overflows the moment a word is tapped*
and swaps to its gloss. Repaginating on every tap would move the page break
under the reader's eyes mid-sentence, which is worse than a scrollbar. So
every token is measured in BOTH forms and laid out at its **worst case**:
pages can only ever get shorter as words flip, never overflow, and a break
once chosen stays put.

**Orientation is a parameter, not a second engine.** Horizontal prose fills
a line rightward and stacks lines downward; vertical (縦書き) fills a column
downward and stacks columns leftward. Both are "fill along the main axis,
advance along the cross axis", so one algorithm serves both — the caller
says which measurement is which.

This module is toolkit-free: the caller passes a function that measures a
string, so Kivy and pyglet paginate identically from their own text engines.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


def english_of(token) -> str:
    """The gloss a token shows when it is swapped to English.

    Must match what the reader renders, or the measurement is a lie.
    """
    if not getattr(token, "meaning", ""):
        return ""
    return " " + token.meaning.split(",")[0].split(";")[0] + " "


@dataclass
class Line:
    """One rendered line (horizontal) or column (vertical)."""
    tokens: list = field(default_factory=list)
    extent: float = 0.0          #: how much of the main axis it uses


@dataclass
class Page:
    lines: list[Line] = field(default_factory=list)
    #: Sentence indices this page covers, so a *sentence* cursor can find it.
    first_sentence: int = 0
    last_sentence: int = 0

    @property
    def tokens(self) -> list:
        return [t for line in self.lines for t in line.tokens]


def measure_tokens(tokens: Sequence, measure: Callable[[str], float],
                   ) -> dict[int, float]:
    """Worst-case extent per token position: max(Japanese, English).

    Keyed by index rather than by token so identical words in different
    places stay independent, and so the caller can cache it alongside the
    token list it was measured from.
    """
    out: dict[int, float] = {}
    for i, token in enumerate(tokens):
        ja = measure(token.text)
        en = measure(english_of(token)) if getattr(token, "expression", "") \
            else 0.0
        out[i] = max(ja, en)
    return out


def paginate(groups: Sequence[Sequence], measure: Callable[[str], float], *,
             main_axis: float, cross_axis: float, line_extent: float,
             sentence_per_line: bool = True) -> list[Page]:
    """Cut tokenised sentences into pages that genuinely fit.

    *groups* is one token list per sentence. *measure* returns a string's
    extent along the main axis (width for horizontal, height for vertical).
    *main_axis* is the usable line length, *cross_axis* the usable page
    depth, and *line_extent* the thickness of one line/column.

    Pages are never empty: a single token wider than the whole line still
    gets a line of its own rather than looping forever.
    """
    main_axis = max(1.0, float(main_axis))
    line_extent = max(1.0, float(line_extent))
    per_page = max(1, int(cross_axis // line_extent))

    pages: list[Page] = []
    page = Page(first_sentence=0, last_sentence=0)

    def flush_page(next_first: int) -> None:
        nonlocal page
        if page.lines:
            pages.append(page)
        page = Page(first_sentence=next_first, last_sentence=next_first)

    for s_index, tokens in enumerate(groups):
        widths = measure_tokens(tokens, measure)
        lines: list[Line] = []
        current = Line()
        for i, token in enumerate(tokens):
            w = widths[i]
            if current.tokens and current.extent + w > main_axis:
                lines.append(current)
                current = Line()
            current.tokens.append(token)
            current.extent += w
        if current.tokens:
            lines.append(current)
        if not lines:
            continue

        # A sentence longer than a whole page is split across pages rather
        # than dropped — a pasted chapter may have no punctuation for pages.
        for line in lines:
            if len(page.lines) >= per_page:
                flush_page(s_index)
            page.lines.append(line)
            page.last_sentence = s_index
        if not sentence_per_line:
            continue        # (kept for symmetry; lines already end sentences)

    if page.lines:
        pages.append(page)
    return pages


def page_of_sentence(pages: Sequence[Page], sentence: int) -> int:
    """Which page holds a sentence index — the cursor is stored that way.

    Storing a *page number* would teleport the reader whenever the text size
    or orientation changed, and two synced devices with different screens
    would disagree about where they are.
    """
    for i, page in enumerate(pages):
        if page.first_sentence <= sentence <= page.last_sentence:
            return i
    return max(0, len(pages) - 1) if pages else 0
