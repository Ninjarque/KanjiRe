"""Rectangular-grid preference + recall 'both' prompt policy."""
from kanjire.game.recall import prompt_for
from kanjire.ui.layout import choose_grid


def test_prefer_exact_gives_full_rectangles():
    # Every solo board size (words 4/6/8/12/24 × faces 2/3/4) must lay out
    # as a full rectangle — no short last row to visually re-scan.
    for words in (4, 6, 8, 12, 24):
        for faces in (2, 3, 4):
            n = words * faces
            for w, h in ((396, 800), (880, 342), (760, 780), (340, 790)):
                cols, rows, cw, ch = choose_grid(n, w, h, gap=8,
                                                 prefer_exact=True)
                assert n % cols == 0, (n, w, h, cols)
                assert cols * rows == n, (n, w, h, cols, rows)
                assert cw > 0 and ch > 0


def test_default_behaviour_unchanged_without_flag():
    got = choose_grid(14, 880, 342, gap=10)
    assert got[0] * got[1] >= 14  # partial last row still allowed


def test_prompt_for_both():
    assert prompt_for(0, "both", tts_ok=True) == "both"
    assert prompt_for(3, "both", tts_ok=True) == "both"
    # No TTS: falls back to typed like 'listen' does.
    assert prompt_for(0, "both", tts_ok=False) == "typed"
    # 'mixed' still alternates typed/listen, never 'both'.
    assert prompt_for(1, "mixed", tts_ok=True) == "listen"
    assert prompt_for(2, "mixed", tts_ok=True) == "typed"
