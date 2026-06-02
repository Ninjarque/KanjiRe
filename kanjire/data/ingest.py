"""Corpus ingestion: turn real Japanese text into a playable vocabulary deck.

Pipeline
--------
1. **Tokenise** the text with fugashi (MeCab + UniDic), giving each token its
   part of speech and dictionary (base) form.
2. **Keep content words** (nouns / verbs / adjectives / adverbs) whose base form
   contains kanji, and count how often each appears - that count *is* the
   in-corpus frequency the game weights by.
3. **Resolve** each word's hiragana reading and English meaning with jamdict
   (JMdict), and each kanji's grade / JLPT / meanings with KanjiDic2.

The heavy NLP libraries are imported lazily so importing this module never costs
anything until you actually ingest text.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from kanjire.jputil import capitalize_first, has_kanji, kata_to_hira

# UniDic part-of-speech (pos1) values we treat as "vocabulary".
CONTENT_POS = {"名詞", "代名詞", "動詞", "形容詞", "形状詞", "副詞"}
# pos2 values to drop even within the above (names, numbers).
SKIP_POS2 = {"固有名詞", "数詞"}

ProgressCb = Callable[[int, int, str], None]


@dataclass
class WordRecord:
    expression: str
    reading: str
    meaning: str
    pos: str
    count: int
    freq: float = 0.0
    meaning_fr: str | None = None


@dataclass
class KanjiRecord:
    char: str
    count: int
    grade: int | None = None
    jlpt: int | None = None
    meanings: str | None = None
    freq: float = 0.0


@dataclass
class CorpusResult:
    words: list[WordRecord] = field(default_factory=list)
    kanji: list[KanjiRecord] = field(default_factory=list)
    total_tokens: int = 0
    candidate_words: int = 0       # unique kanji content words found
    resolved_words: int = 0        # of those, found in the dictionary

    @property
    def summary(self) -> str:
        return (
            f"{self.total_tokens} tokens, {self.candidate_words} candidate words, "
            f"{self.resolved_words} resolved, {len(self.kanji)} unique kanji"
        )


# --------------------------------------------------------------------------- #
# Lazy singletons for the NLP backends
# --------------------------------------------------------------------------- #
_tagger = None
_jam = None


def get_tagger():
    global _tagger
    if _tagger is None:
        import fugashi

        _tagger = fugashi.Tagger()
    return _tagger


def get_jamdict():
    global _jam
    if _jam is None:
        from jamdict import Jamdict

        _jam = Jamdict()
    return _jam


# --------------------------------------------------------------------------- #
# Tokenisation
# --------------------------------------------------------------------------- #
def _base_form(token) -> str | None:
    f = token.feature
    for attr in ("orthBase", "lemma"):
        val = getattr(f, attr, None)
        if val and val != "*":
            return val.split("-")[0]  # UniDic lemmas can carry a "-pos" suffix
    return token.surface or None


def _reading_hint(token) -> str | None:
    """A hiragana hint for the token's *base* reading, from UniDic features.

    Used to disambiguate dictionary entries (e.g. 本 -> ほん vs もと)."""
    f = token.feature
    for attr in ("pronBase", "kanaBase", "lForm", "pron", "kana"):
        val = getattr(f, attr, None)
        if val and val != "*":
            return kata_to_hira(val)
    return None


def count_tokens(
    text: str, tagger=None
) -> tuple[Counter, dict[str, str], dict[str, str], Counter, int]:
    """Return (word_counts, pos_by_word, reading_by_word, kanji_counts, total)."""
    tagger = tagger or get_tagger()
    word_counts: Counter = Counter()
    pos_by_word: dict[str, str] = {}
    reading_by_word: dict[str, str] = {}
    total = 0

    for token in tagger(text):
        total += 1
        f = token.feature
        pos1 = getattr(f, "pos1", None)
        if pos1 not in CONTENT_POS:
            continue
        if getattr(f, "pos2", None) in SKIP_POS2:
            continue
        base = _base_form(token)
        if not base or not has_kanji(base):
            continue
        word_counts[base] += 1
        pos_by_word.setdefault(base, pos1)
        if base not in reading_by_word:
            hint = _reading_hint(token)
            if hint:
                reading_by_word[base] = hint

    # True per-kanji frequency comes straight from the characters in the text.
    kanji_counts = Counter(ch for ch in text if has_kanji(ch))
    return word_counts, pos_by_word, reading_by_word, kanji_counts, total


# --------------------------------------------------------------------------- #
# Dictionary resolution
# --------------------------------------------------------------------------- #
def _entry_reading(entry) -> str | None:
    if not entry.kana_forms:
        return None
    return kata_to_hira(entry.kana_forms[0].text)


def _entry_meaning(entry, max_glosses: int = 2) -> str | None:
    for sense in entry.senses:
        glosses = [g.text for g in sense.gloss][:max_glosses]
        if glosses:
            return "; ".join(glosses)
    return None


def _resolve_entry(result, key: str, hint: str | None):
    """Pick (reading, meaning) for *key*, preferring an entry whose reading
    matches the contextual *hint* from the tokenizer."""
    if not result.entries:
        return None, None
    key_entries = [
        e for e in result.entries if any(k.text == key for k in e.kanji_forms)
    ]
    entries = key_entries or result.entries

    if hint:
        for entry in entries:
            if any(kata_to_hira(kf.text) == hint for kf in entry.kana_forms):
                meaning = _entry_meaning(entry)
                if meaning:
                    return hint, meaning

    entry = entries[0]
    return _entry_reading(entry), _entry_meaning(entry)


def resolve_words(
    word_counts: Counter,
    pos_by_word: dict[str, str],
    reading_by_word: dict[str, str],
    total_tokens: int,
    jam=None,
    progress: ProgressCb | None = None,
) -> list[WordRecord]:
    jam = jam or get_jamdict()
    items = word_counts.most_common()
    records: list[WordRecord] = []

    # Optional French gloss sidecar
    fr_con = None
    fr_lookup = None
    try:
        import importlib, sys as _sys, os as _os
        _scripts_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(__file__))), "scripts")
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        _fr = importlib.import_module("fetch_jmdict_multilang")
        fr_con = _fr.open_for_lookup()
        fr_lookup = _fr.lookup_fr if fr_con else None
    except Exception:
        pass

    n = len(items)
    for i, (word, count) in enumerate(items):
        if progress and (i % 25 == 0 or i == n - 1):
            progress(i + 1, n, word)
        result = jam.lookup(word)
        reading, meaning = _resolve_entry(result, word, reading_by_word.get(word))
        if not reading or not meaning:
            continue
        rel = count / total_tokens if total_tokens else 0.0
        freq = math.log10(rel) + 9.0 if rel > 0 else 0.0
        meaning_fr = fr_lookup(fr_con, word, reading) if fr_lookup else None
        records.append(
            WordRecord(
                expression=word,
                reading=reading,
                meaning=capitalize_first(meaning) or meaning,
                pos=pos_by_word.get(word, ""),
                count=count,
                freq=freq,
                meaning_fr=capitalize_first(meaning_fr),
            )
        )
    if fr_con is not None:
        try: fr_con.close()
        except Exception: pass
    return records


def resolve_kanji(kanji_counts: Counter, jam=None) -> list[KanjiRecord]:
    jam = jam or get_jamdict()
    total = sum(kanji_counts.values()) or 1
    records: list[KanjiRecord] = []
    for ch, count in kanji_counts.most_common():
        grade = jlpt = None
        meanings = None
        try:
            res = jam.lookup(ch)
            if res.chars:
                c = res.chars[0]
                grade = getattr(c, "grade", None)
                jlpt = getattr(c, "jlpt", None)
                if c.rm_groups:
                    ms = [m.value for m in c.rm_groups[0].meanings if not m.m_lang]
                    meanings = ", ".join(ms[:3]) or None
        except Exception:  # noqa: BLE001 - dictionary lookups are best-effort
            pass
        rel = count / total
        freq = math.log10(rel) + 9.0 if rel > 0 else 0.0
        records.append(
            KanjiRecord(char=ch, count=count, grade=grade, jlpt=jlpt,
                        meanings=meanings, freq=freq)
        )
    return records


def analyze(text: str, *, progress: ProgressCb | None = None) -> CorpusResult:
    """Full analysis of *text* into word and kanji records (no DB writes).

    A fresh Jamdict instance is created and closed here so the underlying SQLite
    connections stay bound to a single thread (matters for background ingestion).
    """
    from jamdict import Jamdict

    word_counts, pos_by_word, reading_by_word, kanji_counts, total = count_tokens(text)
    jam = Jamdict()
    try:
        words = resolve_words(
            word_counts, pos_by_word, reading_by_word, total,
            jam=jam, progress=progress,
        )
        kanji = resolve_kanji(kanji_counts, jam=jam)
    finally:
        try:
            jam.close()
        except Exception:
            pass
    return CorpusResult(
        words=words,
        kanji=kanji,
        total_tokens=total,
        candidate_words=len(word_counts),
        resolved_words=len(words),
    )


# --------------------------------------------------------------------------- #
# Writing a deck
# --------------------------------------------------------------------------- #
def write_deck(
    con,
    deck_name: str,
    result: CorpusResult,
    *,
    description: str = "",
    source: str = "",
    created_at: str = "",
) -> None:
    from kanjire.data import db

    db.init_db(con)
    db.upsert_deck(
        con, deck_name, "corpus",
        description=description, source=source, created_at=created_at,
    )
    for w in result.words:
        db.upsert_word(
            con, deck=deck_name, expression=w.expression, reading=w.reading,
            meaning=w.meaning, meaning_fr=w.meaning_fr, jlpt=None,
            freq=w.freq, pos=w.pos, count=w.count,
        )
    for k in result.kanji:
        db.upsert_kanji(
            con, deck=deck_name, char=k.char, count=k.count, freq=k.freq,
            grade=k.grade, jlpt=k.jlpt, meanings=k.meanings,
        )
    db.refresh_deck_counts(con)
    con.commit()
