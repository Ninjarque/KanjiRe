"""Measured pagination: pages that fit, and breaks that never move.

The load-bearing property is the last one: a page is laid out at each token's
WORST case (Japanese vs its English gloss), so tapping words can only ever
make a page shorter. If that ever regresses, page breaks will start shifting
under the reader mid-sentence.
"""
from __future__ import annotations

from kanjire.data.paginate import (Page, english_of, measure_tokens,
                                   page_of_sentence, paginate)
from kanjire.data.weave import Token


def W(surface, expr="", meaning=""):
    return Token(surface, expr, "", meaning, 5)


#: One unit per character — makes every expectation countable by hand.
def measure(text: str) -> float:
    return float(len(text))


def test_english_gloss_matches_what_the_reader_draws():
    # The reader renders " gloss " with padding spaces; measuring the bare
    # word would under-measure every swapped token.
    assert english_of(W("大学", "大学", "College; university")) == " College "
    assert english_of(W("を")) == ""


def test_a_token_is_measured_at_its_worst_case():
    # 大学 is 2 chars, " College " is 9 — the wider one must win.
    widths = measure_tokens([W("大学", "大学", "College")], measure)
    assert widths[0] == len(" College ")


def test_unmatched_text_is_measured_as_itself():
    widths = measure_tokens([W("を")], measure)
    assert widths[0] == 1.0


def test_each_sentence_starts_its_own_line():
    groups = [[W("一")], [W("二")], [W("三")]]
    pages = paginate(groups, measure, main_axis=100, cross_axis=100,
                     line_extent=10)
    assert len(pages) == 1
    assert [len(line.tokens) for line in pages[0].lines] == [1, 1, 1]


def test_a_long_sentence_wraps_within_the_line_budget():
    groups = [[W("あ") for _ in range(25)]]
    pages = paginate(groups, measure, main_axis=10, cross_axis=1000,
                     line_extent=10)
    lines = pages[0].lines
    assert len(lines) == 3                      # 10 + 10 + 5
    assert all(line.extent <= 10 for line in lines)


def test_pages_hold_only_as_many_lines_as_fit():
    groups = [[W(str(i))] for i in range(10)]
    pages = paginate(groups, measure, main_axis=100, cross_axis=40,
                     line_extent=10)
    assert all(len(p.lines) <= 4 for p in pages)
    assert sum(len(p.lines) for p in pages) == 10


def test_nothing_is_lost_across_pages():
    groups = [[W(f"文{i}"), W("。")] for i in range(30)]
    pages = paginate(groups, measure, main_axis=6, cross_axis=30,
                     line_extent=10)
    got = [t.text for p in pages for t in p.tokens]
    want = [t.text for g in groups for t in g]
    assert got == want, "pagination dropped or duplicated text"


def test_a_token_wider_than_the_line_still_gets_placed():
    """A single enormous run must not loop forever or vanish."""
    groups = [[W("very-long-unbreakable-token")]]
    pages = paginate(groups, measure, main_axis=5, cross_axis=100,
                     line_extent=10)
    assert pages and pages[0].tokens[0].text.startswith("very-long")


def test_swapping_words_to_english_never_overflows_a_page():
    """The property the whole design rests on.

    Lay out with the worst case, then re-measure as if EVERY word had been
    tapped: no line may exceed the budget.
    """
    groups = [[W("大学", "大学", "College"), W("の"),
               W("本", "本", "Book"), W("を"),
               W("読む", "読む", "To read")] for _ in range(6)]
    main = 20.0
    pages = paginate(groups, measure, main_axis=main, cross_axis=200,
                     line_extent=10)
    for page in pages:
        for line in page.lines:
            english_width = sum(
                measure(english_of(t)) if t.expression else measure(t.text)
                for t in line.tokens)
            assert english_width <= main + 1e-9, (
                "a page overflows once its words are in English — page "
                "breaks would shift while reading")


def test_page_lookup_uses_the_sentence_cursor():
    groups = [[W(str(i))] for i in range(12)]
    pages = paginate(groups, measure, main_axis=100, cross_axis=30,
                     line_extent=10)
    assert page_of_sentence(pages, 0) == 0
    assert page_of_sentence(pages, 4) == 1
    assert page_of_sentence(pages, 11) == len(pages) - 1


def test_page_lookup_clamps_out_of_range():
    groups = [[W("一")]]
    pages = paginate(groups, measure, main_axis=100, cross_axis=100,
                     line_extent=10)
    assert page_of_sentence(pages, 999) == 0
    assert page_of_sentence([], 3) == 0


def test_vertical_is_the_same_call_with_the_axes_swapped():
    """縦書き is a parameter, not a second layout engine: the main axis is a
    column's height and the cross axis is the page's width."""
    groups = [[W("一"), W("二"), W("三")], [W("四")]]
    tall = paginate(groups, measure, main_axis=2, cross_axis=100,
                    line_extent=10)          # 2 chars per column
    assert [len(line.tokens) for line in tall[0].lines] == [2, 1, 1]


def test_an_empty_passage_paginates_to_nothing():
    assert paginate([], measure, main_axis=10, cross_axis=10,
                    line_extent=5) == []
    assert paginate([[]], measure, main_axis=10, cross_axis=10,
                    line_extent=5) == []


def test_a_tiny_page_still_yields_one_line_per_page():
    groups = [[W(str(i))] for i in range(4)]
    pages = paginate(groups, measure, main_axis=10, cross_axis=1,
                     line_extent=10)
    assert len(pages) == 4 and all(len(p.lines) == 1 for p in pages)
