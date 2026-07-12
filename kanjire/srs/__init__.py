"""Spaced-repetition layer: FSRS-scheduled per-word memory state.

The store lives in the same per-user ``stats.db`` as ``word_stats`` and is
fed by the same recorder events, so abandoning a round still updates the
schedule honestly. See :mod:`kanjire.srs.store`.
"""
from kanjire.srs.store import SrsStore

__all__ = ["SrsStore"]
