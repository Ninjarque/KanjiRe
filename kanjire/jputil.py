"""Small, dependency-free helpers for Japanese text.

Kept free of third-party imports so the *runtime* game never needs the heavy
NLP stack - those are only used by the offline build scripts.
"""
from __future__ import annotations

# CJK ranges that we treat as "kanji" for gameplay purposes.
_KANJI_RANGES = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
)

_HIRAGANA = (0x3041, 0x3096)
_KATAKANA = (0x30A1, 0x30FA)


def is_kanji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _KANJI_RANGES)


def has_kanji(text: str) -> bool:
    """True if *text* contains at least one kanji character."""
    return any(is_kanji(ch) for ch in text)


def kanji_chars(text: str) -> list[str]:
    """The kanji characters in *text*, in order, with duplicates kept."""
    return [ch for ch in text if is_kanji(ch)]


def uncovered_kanji(sentence: str, indexed_heads) -> list[str]:
    """Kanji in *sentence* that appear in none of the *indexed_heads*.

    The reading room only knows a sentence through its build-time word index,
    which drops proper nouns, numerals and anything the dictionary couldn't
    resolve. So a name-heavy sentence can look "fully known" on the strength of
    one common word. These are the kanji on screen that no indexed word
    accounts for - if there are any, the sentence is NOT one you know every word
    of, whatever the indexed words say.
    """
    covered: set[str] = set()
    for h in indexed_heads:
        covered.update(kanji_chars(h))
    return [k for k in dict.fromkeys(kanji_chars(sentence)) if k not in covered]


def is_kana(ch: str) -> bool:
    cp = ord(ch)
    return _HIRAGANA[0] <= cp <= _HIRAGANA[1] or _KATAKANA[0] <= cp <= _KATAKANA[1]


def kata_to_hira(text: str) -> str:
    """Convert katakana to hiragana, leaving everything else untouched.

    MeCab/UniDic returns readings in katakana; the game shows hiragana, so we
    normalise here.  The prolonged-sound mark (``ー``) and small kana are mapped
    correctly because the katakana and hiragana blocks are offset by 0x60.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x30A1 <= cp <= 0x30F6:  # standard katakana -> hiragana
            out.append(chr(cp - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def is_mostly_japanese(text: str) -> bool:
    """Heuristic: does *text* contain any kana or kanji at all?"""
    return any(is_kanji(ch) or is_kana(ch) for ch in text)


def capitalize_first(text: str | None) -> str | None:
    """Upper-case the first *letter* of *text*, leaving punctuation alone.

    Many JMdict entries are prefixed with ``"--"`` or ``"(...) "`` or
    whitespace, so a naive ``text[0].upper()`` would leave the actual word
    starting lower-case. We scan to the first alphabetic character and
    capitalise it in place, so ``"-- honorific form"`` becomes
    ``"-- Honorific form"`` (which then capitalises correctly even after the
    display layer trims the leading ``--``)."""
    if not text:
        return text
    for i, ch in enumerate(text):
        if ch.isalpha():
            if ch.islower():
                return text[:i] + ch.upper() + text[i + 1 :]
            return text
    return text
