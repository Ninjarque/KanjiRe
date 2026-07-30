"""The diglot-weave reader: your own text, mostly in Japanese.

Read a passage in Japanese; tap a word you don't know and it opens up
(reading + meaning) *and* every later appearance of that word switches to
English for a while, then quietly switches back once you've had enough
exposure. Words that are both unfamiliar and hard start in English without
being asked.

Three pieces live here, all toolkit-free so both UIs share them:

* **Tokenising** — splitting a passage into sentences and words. NOT with
  MeCab: the Android package is ``python3, kivy, paho-mqtt, certifi,
  pynacl``, so a morphological analyser simply is not there. Instead a
  longest-match pass over the bundled vocabulary finds the words we have
  entries for — which is exactly the set we could gloss or swap anyway.
* **The library** — passages the player added, with where they left off, so
  a novel survives closing the app.
* **The weave rule** — how many future appearances of a word stay English,
  derived from what the stats layer already knows about it.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from kanjire.data.stats import classify
from kanjire.jputil import has_kanji

#: Sentence enders. Newlines split too — pasted prose often has no 。 at all.
_END = "。．.!?！？\n"
#: The longest headword we will try to match at one position. The vocabulary
#: tops out well below this; it only bounds the inner loop.
MAX_WORD = 12
#: A passage longer than this is truncated on import — a whole novel would
#: make the reader unusable long before it made it slow.
MAX_CHARS = 200_000


# --------------------------------------------------------------------------- #
# Tokenising
# --------------------------------------------------------------------------- #
#: Invisible passengers that ride along with copied web text: ZWSP, ZWNJ,
#: ZWJ and a BOM. Built from code points rather than written as a literal —
#: the font guard scans string *values* across the package, and a literal
#: here would look exactly like UI text that renders as tofu.
_ZERO_WIDTH = tuple(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF))


def normalize_text(text: str) -> str:
    """Clean a pasted passage: one newline convention, no control junk.

    Defensive, not a fix for anything specific. (An earlier version of this
    docstring blamed Kivy's ``TextInput.paste()`` for dropping ``\\r\\n``
    content — that was wrong: it handles CRLF itself via ``replace_crlf``,
    and the repro that "proved" otherwise used a detached widget, whose empty
    ``_lines`` makes ``insert_text`` return immediately.)

    Still worth doing: it gives sentence splitting one newline convention to
    reason about, and drops the zero-width characters and full-width spaces
    that copied web text hides.
    """
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = out.replace("　", " ").replace("\xa0", " ")
    for ch in _ZERO_WIDTH:
        out = out.replace(ch, "")
    # Keep newlines and tabs; drop the rest of the C0 controls.
    return "".join(c for c in out if c == "\n" or c == "\t" or ord(c) >= 0x20)


def split_sentences(text: str) -> list[str]:
    """A passage as sentences: split on punctuation AND on the player's own
    line breaks, since pasted prose often has neither reliably."""
    out: list[str] = []
    buf: list[str] = []
    for ch in normalize_text(text)[:MAX_CHARS]:
        buf.append(ch)
        if ch in _END:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def clipboard_text() -> str:
    """The clipboard, normalised — or "" if there is nothing usable.

    Backs the explicit "Paste" button. That button is a *convenience*, not
    the primary path: the platform's own paste gesture works and should be
    what people reach for — it is also the only one that can offer Android's
    clipboard history, which this can never see (it reads the primary clip
    only). The pyglet field has no paste of its own, so there it is the way in.
    """
    text = ""
    try:                                    # Kivy is present on both today
        from kivy.core.clipboard import Clipboard
        text = Clipboard.paste() or ""
    except Exception:                       # noqa: BLE001
        text = ""
    if not text:
        try:
            import tkinter                  # desktop fallback
            root = tkinter.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
        except Exception:                   # noqa: BLE001
            text = ""
    return normalize_text(text)


#: Reader type sizes offered by the slider (sp / pt).
FONT_SIZES = (15, 17, 19, 22, 26, 30)
DEFAULT_FONT_SIZE = 19
#: Writing directions. "vertical" is 縦書き: characters down a column,
#: columns right-to-left, and the page turning leftward.
WRITING_MODES = ("horizontal", "vertical")


def font_size_of(state) -> int:
    """The reader's type size, clamped to something we offer."""
    try:
        want = int(state.setting("read_font_size", DEFAULT_FONT_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE
    return want if want in FONT_SIZES else DEFAULT_FONT_SIZE


def sentence_font(fonts, index: int, varied: bool):
    """Which face this sentence is set in.

    Varying it per *sentence* (never per word) trains reading for meaning
    instead of for a familiar shape, the same idea as Familiarize mode —
    but a sentence is the smallest unit that stays comfortable to read.
    Deterministic in the index so re-rendering a page doesn't reshuffle it.
    """
    if not varied or not fonts:
        return fonts[0] if fonts else None
    return fonts[index % len(fonts)]


def describe(text: str) -> tuple[int, int]:
    """``(characters, sentences)`` for a passage — what the Add screen shows
    so you can see at a glance that the paste actually landed."""
    clean = normalize_text(text)
    return len(clean), len(split_sentences(clean))


@dataclass
class Token:
    """One run of text, either a known word or a stretch we can't name."""
    text: str                       #: exactly as it appears in the passage
    expression: str = ""            #: dictionary form, when we matched one
    reading: str = ""
    meaning: str = ""
    jlpt: int | None = None
    #: "sentence:index" — WHICH appearance of the word this is. Stamped when
    #: a passage is tokenised, so a verdict can be recorded per appearance
    #: rather than per word (see :meth:`WeaveState.flip`).
    slot: str = ""

    @property
    def known_word(self) -> bool:
        return bool(self.expression)

    @property
    def key(self) -> tuple[str, str]:
        return (self.expression, self.reading)


#: godan endings → (masu-stem row, negative row, past, gerund).
_GODAN = {
    "う": ("い", "わ", "った", "って"),
    "く": ("き", "か", "いた", "いて"),
    "ぐ": ("ぎ", "が", "いだ", "いで"),
    "す": ("し", "さ", "した", "して"),
    "つ": ("ち", "た", "った", "って"),
    "ぬ": ("に", "な", "んだ", "んで"),
    "ぶ": ("び", "ば", "んだ", "んで"),
    "む": ("み", "ま", "んだ", "んで"),
    "る": ("り", "ら", "った", "って"),
}
#: What follows a masu-stem, a て-form and a negative stem.
_AFTER_STEM = ("ます", "ました", "ません", "ませんでした", "たい", "たく",
               "たくない", "ながら", "そう")
_AFTER_TE = ("", "いる", "います", "いた", "いました", "いない", "から",
             "ください", "しまった", "みる")
_AFTER_NEG = ("ない", "なかった", "なくて", "ず")


def inflections(expr: str) -> set[str]:
    """Plausible surface forms of a dictionary entry.

    The tokeniser matches text against the vocabulary, and text is *inflected*:
    遊んでいる, 読みました and 食べたくない were all missed, which is most of
    what a real page is made of. Generating forms up front turns matching back
    into a dictionary lookup — no morphological analyser required, which is
    the constraint on Android.

    Deliberately generous. A form that never occurs simply never matches, and
    longest-match keeps a spurious short form from beating a real long one. For
    る-verbs both the ichidan and godan conjugations are produced, because
    telling 食べる from 帰る needs data the deck doesn't carry.
    """
    out: set[str] = set()
    if len(expr) < 2:
        return out
    last, body = expr[-1], expr[:-1]

    if expr.endswith("する"):
        stem = expr[:-2]
        for suffix in ("します", "しました", "しません", "した", "して",
                       "しない", "しなかった", "したい", "できる"):
            out.add(stem + suffix)
        return out

    if last == "い":            # i-adjective
        for suffix in ("かった", "くない", "くて", "く", "かったら",
                       "くなかった", "さ"):
            out.add(body + suffix)
        return out

    if last in _GODAN:
        i_row, a_row, past, te = _GODAN[last]
        # 行く is the classic irregular: 行った, not 行いた.
        if expr.endswith("行く"):
            past, te = "った", "って"
        for suffix in _AFTER_STEM:
            out.add(body + i_row + suffix)
        out.add(body + i_row)            # bare stem (a noun, often)
        out.add(body + past)
        for suffix in _AFTER_TE:
            out.add(body + te + suffix)
        for suffix in _AFTER_NEG:
            out.add(body + a_row + suffix)
        if last == "る":                 # ichidan reading of the same word
            for suffix in _AFTER_STEM:
                out.add(body + suffix)
            out.add(body + "た")
            for suffix in _AFTER_TE:
                out.add(body + "て" + suffix)
            for suffix in _AFTER_NEG:
                out.add(body + suffix)
            out.add(body + "られる")
    return out


class Lexicon:
    """Longest-match word index over the vocabulary DB.

    Built once and reused: a passage is re-tokenised whenever the reader
    reopens it, and rebuilding this per sentence made that visibly slow.
    """

    def __init__(self, con: sqlite3.Connection, decks=None,
                 inflect: bool = True) -> None:
        self._by_len: dict[int, dict[str, tuple]] = {}
        self._max = 1
        try:
            rows = con.execute(
                "SELECT expression, reading, meaning, jlpt FROM words"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            expr = row["expression"] if hasattr(row, "keys") else row[0]
            if not expr or len(expr) > MAX_WORD:
                continue
            # Entries carry decorations the passage never will (～, spaces,
            # parenthesised notes); those can't be matched, so skip them.
            if any(c in expr for c in "～~ ()（）"):
                continue
            data = (expr,
                    row["reading"] if hasattr(row, "keys") else row[1],
                    row["meaning"] if hasattr(row, "keys") else row[2],
                    row["jlpt"] if hasattr(row, "keys") else row[3])
            # The dictionary form, then every inflection we can generate. The
            # inflected key maps back to the SAME entry, so a tapped 遊んでいる
            # is recorded against 遊ぶ and glossed from it.
            surfaces = [expr]
            if inflect:
                surfaces.extend(s for s in inflections(expr)
                                if 1 < len(s) <= MAX_WORD)
            for surface in surfaces:
                slot = self._by_len.setdefault(len(surface), {})
                # Prefer the easier (higher JLPT number) reading of a
                # homograph: a beginner meeting 生 wants "life", not the N1
                # sense. A real dictionary form always beats an inflection.
                prev = slot.get(surface)
                if prev is None or (surface == expr and prev[0] != surface) \
                        or (prev[0] != surface and (data[3] or 0) > (prev[3] or 0)):
                    slot[surface] = data
                self._max = max(self._max, len(surface))
        self._load_kanji(con)

    def _load_kanji(self, con) -> None:
        """Per-character meanings, for kanji no *word* covers.

        A novel is full of vocabulary the JLPT deck has never heard of (片,
        悲嘆, 外套 …). Without this they are simply dead text — and "the word
        I need is the one I can't tap" is the worst possible failure for a
        reading aid. Falling back to the character's own meaning is how a
        learner reads an unknown compound anyway.
        """
        self._kanji: dict[str, str] = {}
        try:
            for row in con.execute(
                "SELECT char, meanings FROM kanji WHERE meanings IS NOT NULL"
            ):
                ch = row["char"] if hasattr(row, "keys") else row[0]
                meanings = row["meanings"] if hasattr(row, "keys") else row[1]
                if ch and meanings and ch not in self._kanji:
                    self._kanji[ch] = meanings
        except sqlite3.Error:
            pass

    def kanji_token(self, ch: str) -> "Token | None":
        """A one-character fallback token, or None if we know nothing."""
        meaning = getattr(self, "_kanji", {}).get(ch)
        if not meaning:
            return None
        return Token(ch, ch, "", meaning, None)

    def match_at(self, text: str, i: int) -> tuple | None:
        """The longest entry (or inflection) starting at *i*, or None.

        Returns ``(expression, reading, meaning, jlpt, matched_length)`` — the
        length matters because an inflected surface is longer than the
        dictionary form it maps to.
        """
        for span in range(min(self._max, len(text) - i), 0, -1):
            hit = self._by_len.get(span, {}).get(text[i:i + span])
            if hit is not None:
                return (*hit, span)
        return None

    def tokenize(self, text: str) -> list[Token]:
        """*text* as tokens: matched words, and the gaps between them.

        Gaps are kept as plain tokens (particles, inflections, punctuation)
        so the passage still reads as itself — this is a reading aid, not a
        parse tree.
        """
        out: list[Token] = []
        gap: list[str] = []
        i, n = 0, len(text)
        while i < n:
            hit = self.match_at(text, i)
            # A single kana "word" (と, に, の …) is almost always a particle
            # here, not the vocabulary entry of the same shape — matching it
            # would make every sentence a wall of tappable noise.
            if hit is not None and (hit[4] > 1 or has_kanji(hit[0])):
                if gap:
                    out.append(Token("".join(gap)))
                    gap = []
                # `surface` is what the page shows; hit[0] is the dictionary
                # form the gloss and the stats belong to.
                surface = text[i:i + hit[4]]
                out.append(Token(surface, hit[0], hit[1] or "", hit[2] or "",
                                 hit[3]))
                i += hit[4]
                continue
            solo = self.kanji_token(text[i]) if has_kanji(text[i]) else None
            if solo is not None:
                if gap:
                    out.append(Token("".join(gap)))
                    gap = []
                out.append(solo)
                i += 1
                continue
            gap.append(text[i])
            i += 1
        if gap:
            out.append(Token("".join(gap)))
        return out


# --------------------------------------------------------------------------- #
# The weave rule
# --------------------------------------------------------------------------- #
#: How many later appearances stay English, by knowledge bucket. A word you
#: have never seen needs the crutch for longer than one you merely lapsed.
_BASE = {"unknown": 6, "less_known": 3, "known": 1}
#: Harder words (lower JLPT number) hold the crutch longer.
_JLPT_BONUS = {1: 4, 2: 3, 3: 2, 4: 1, 5: 0, None: 2}
#: Every re-tap means it didn't stick — extend, up to a ceiling.
_RETAP_BONUS = 2
MAX_HOLD = 14


def hold_for(row: dict | None, jlpt: int | None, taps: int = 0) -> int:
    """How many future appearances of a word should read as English.

    Driven entirely by what we already record about the word: its knowledge
    bucket, its JLPT level, and how many times the player has had to tap it.
    """
    base = _BASE.get(classify(row), 6)
    hold = base + _JLPT_BONUS.get(jlpt, 2) + _RETAP_BONUS * max(0, taps)
    return max(1, min(MAX_HOLD, hold))


def starts_in_english(row: dict | None, jlpt: int | None) -> bool:
    """True for words that are both unfamiliar AND hard.

    These get the crutch without being asked for it, so a passage above the
    player's level is readable from the first line instead of a wall of taps.
    """
    return classify(row) == "unknown" and (jlpt or 5) <= 2


def stamp_slots(groups) -> list:
    """Label every token with the appearance it is: "sentence:index".

    Stable for a given passage, which is what lets a verdict be remembered
    per appearance across sessions and across devices.
    """
    for s_index, tokens in enumerate(groups):
        for t_index, token in enumerate(tokens):
            token.slot = f"{s_index}:{t_index}"
    return groups


@dataclass
class WeaveState:
    """Per-word crutch counters for one reading session.

    Persisted through :class:`Library` so a novel resumed tomorrow remembers
    which words were still in English.
    """
    holds: dict[tuple[str, str], int] = field(default_factory=dict)
    taps: dict[tuple[str, str], int] = field(default_factory=dict)
    #: slot ("sentence:index") -> "learned" | "missed". One verdict per
    #: APPEARANCE, for ever: see :meth:`flip`.
    scored: dict[str, str] = field(default_factory=dict)
    #: Appearances whose crutch turn has already been spent: see
    #: :meth:`consume`. Drawing a page is not the same as reading past it.
    spent: set = field(default_factory=set)

    def tapped(self, token: Token, row: dict | None) -> int:
        """Register a tap; returns how long the word now stays English."""
        key = token.key
        self.taps[key] = self.taps.get(key, 0) + 1
        hold = hold_for(row, token.jlpt, self.taps[key] - 1)
        self.holds[key] = hold
        return hold

    def flip(self, token: Token, row: dict | None) -> tuple[bool, str]:
        """Toggle one word between Japanese and English.

        Returns ``(now_english, verdict)`` where the verdict is ``"missed"``
        (turned to English — they needed the gloss), ``"learned"`` (turned
        back to Japanese unaided) or ``""`` when this appearance has already
        been counted.

        **One verdict per appearance, permanently.** A novel contains the same
        word dozens of times, and tapping one of them back and forth would
        otherwise let anybody drive their own knowledge buckets anywhere they
        liked. The word can still be flipped as often as the reader wants —
        it simply stops being evidence after the first time.
        """
        slot = str(getattr(token, "slot", "") or "")
        counted = slot in self.scored
        if self.show_english(token, row):
            # Dropping the crutch: later appearances stay Japanese, so the
            # reader can be surprised by it again further down the chapter.
            self.holds.pop(token.key, None)
            self.taps.setdefault(token.key, 1)
            verdict = "" if counted else "learned"
        else:
            self.tapped(token, row)
            verdict = "" if counted else "missed"
        if verdict and slot:
            self.scored[slot] = verdict
        return self.show_english(token, row), verdict

    def show_english(self, token: Token, row: dict | None) -> bool:
        if not token.known_word:
            return False
        if self.holds.get(token.key, 0) > 0:
            return True
        return (token.key not in self.taps
                and starts_in_english(row, token.jlpt))

    def consume(self, token: Token) -> None:
        """Spend one crutch turn for a word, at most once per appearance.

        Callers spend **one turn per word per page**, not one per appearance
        drawn: a page holding six appearances of the same word would otherwise
        exhaust a six-turn crutch the moment it was drawn, and the word would
        never read as English again.

        The per-appearance guard is what makes redrawing free. A page is
        rebuilt on every tap, every return to it and every change of text
        size, and each of those used to spend the crutch again — so a word
        tapped once was back in Japanese a few taps later however badly it was
        known.
        """
        slot = str(getattr(token, "slot", "") or "")
        if slot:
            if slot in self.spent:
                return
            self.spent.add(slot)
        key = token.key
        left = self.holds.get(key)
        if left:
            if left <= 1:
                self.holds.pop(key, None)
            else:
                self.holds[key] = left - 1
