"""The two readers must page identically — same core, same settings keys.

Nothing here draws anything: it checks that the Kivy and pyglet readers both
delegate to :mod:`kanjire.data.paginate` and both read the *same* settings, so
a passage opened on the phone and on the desktop breaks at the same place and
a synced setting means the same thing on both.

A reader that grew its own private pagination would still pass its own tests
while silently disagreeing with the other device — hence this file.
"""
from __future__ import annotations

import inspect

from kanjire.data.paginate import page_of_sentence, paginate
from kanjire.ui.scenes.weaveread import WeaveScene

#: Settings that move the page breaks. Both readers must honour all of them.
DISPLAY_KEYS = ("read_orientation", "read_fonts", "read_font_size")


def _kivy_source() -> str:
    # Importing the Kivy screen pulls in a window; the source is enough.
    from pathlib import Path

    import kanjire
    return (Path(kanjire.__file__).parent / "kivyui" / "screens"
            / "weaveread.py").read_text(encoding="utf-8")


def _pyglet_source() -> str:
    return inspect.getsource(inspect.getmodule(WeaveScene))


def test_both_readers_use_the_shared_pagination_core():
    for name, src in (("kivy", _kivy_source()), ("pyglet", _pyglet_source())):
        assert "from kanjire.data.paginate import" in src, (
            f"the {name} reader stopped using the shared paginator — the two "
            "devices would break pages differently")
        assert "paginate(" in src and "page_of_sentence(" in src, name


def test_both_readers_read_the_same_display_settings():
    kivy, pyg = _kivy_source(), _pyglet_source()
    for key in DISPLAY_KEYS:
        assert key in kivy, f"kivy reader ignores {key}"
        assert key in pyg, f"desktop reader ignores {key}"


def test_neither_reader_stores_a_page_number_as_the_cursor():
    """The saved position is a SENTENCE index.

    Storing the page would teleport the reader whenever the text size,
    orientation or screen changed — and two synced devices with different
    screens would disagree about where they are.
    """
    for name, src in (("kivy", _kivy_source()), ("pyglet", _pyglet_source())):
        assert "save_position" in src, name
        assert "save_position(self.book[\"id\"], self._page_index" not in src, (
            f"{name} reader is persisting a page number")


def test_vertical_columns_cannot_collide():
    """Column pitch must exceed the in-column character advance.

    Kanji are drawn to the full em, so a pitch equal to the cell size makes
    neighbouring columns touch — which is exactly what the first vertical
    build did.
    """
    cell, pitch = WeaveScene._vertical_metrics(20)
    assert pitch > cell > 0


def test_a_page_turn_lands_on_the_page_it_reports():
    """The round trip the readers rely on: index -> sentence -> index."""
    from kanjire.data.weave import Token

    groups = [[Token(f"文{i}", "", "", "", 5)] for i in range(25)]
    pages = paginate(groups, lambda t: float(len(t)), main_axis=4,
                     cross_axis=30, line_extent=10)
    assert len(pages) > 1
    for i, page in enumerate(pages):
        assert page_of_sentence(pages, page.first_sentence) == i
