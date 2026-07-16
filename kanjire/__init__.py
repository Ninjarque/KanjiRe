"""KanjiRe - a Japanese kanji matching mini-game.

The package is split into three layers:

* :mod:`kanjire.model` - vocabulary data types and weighted sampling.
* :mod:`kanjire.game`  - pure, GUI-free game rules (testable in isolation).
* :mod:`kanjire.ui`    - the pyglet front-end (window, scenes, widgets).

The :mod:`kanjire.data` package holds the SQLite access layer and the corpus
ingestion pipeline used by the build scripts.
"""

__version__ = "0.22.0"
