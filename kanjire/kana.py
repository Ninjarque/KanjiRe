"""Kana training: synthesize matching "words" from kana syllables.

The "Kana" deck is synthetic — words don't live in the SQLite vocab database.
Each call to :func:`sample` invents a fresh batch of words by stitching together
1, 2 or 3 random kana sounds. Each :class:`Word` carries three views of the
same syllables so the existing matching engine works unchanged:

* ``expression`` - the **primary script** shown on the "kanji" face card
  (hiragana, katakana, or mixed depending on ``script``).
* ``reading``    - the **secondary script** shown on the "reading" face card
  in 3-face mode (the other kana script).
* ``meaning``    - the **romaji** shown on the "meaning" face card, the
  romanisation the player is learning to read.

The player drills reading whichever script(s) they pick; TTS reads the kana
(SAPI pronounces hiragana and katakana identically).
"""
from __future__ import annotations

import random

from kanjire.model.vocab import Word

#: Master table of kana sounds: ``(romaji, hiragana, katakana)``.  Covers gojuuon,
#: dakuten, handakuten and yoon — every legal mora a beginner needs to recognise.
KANA_SOUNDS: tuple[tuple[str, str, str], ...] = (
    # Vowels
    ("a", "あ", "ア"), ("i", "い", "イ"), ("u", "う", "ウ"),
    ("e", "え", "エ"), ("o", "お", "オ"),
    # K
    ("ka", "か", "カ"), ("ki", "き", "キ"), ("ku", "く", "ク"),
    ("ke", "け", "ケ"), ("ko", "こ", "コ"),
    # S
    ("sa", "さ", "サ"), ("shi", "し", "シ"), ("su", "す", "ス"),
    ("se", "せ", "セ"), ("so", "そ", "ソ"),
    # T
    ("ta", "た", "タ"), ("chi", "ち", "チ"), ("tsu", "つ", "ツ"),
    ("te", "て", "テ"), ("to", "と", "ト"),
    # N
    ("na", "な", "ナ"), ("ni", "に", "ニ"), ("nu", "ぬ", "ヌ"),
    ("ne", "ね", "ネ"), ("no", "の", "ノ"),
    # H
    ("ha", "は", "ハ"), ("hi", "ひ", "ヒ"), ("fu", "ふ", "フ"),
    ("he", "へ", "ヘ"), ("ho", "ほ", "ホ"),
    # M
    ("ma", "ま", "マ"), ("mi", "み", "ミ"), ("mu", "む", "ム"),
    ("me", "め", "メ"), ("mo", "も", "モ"),
    # Y
    ("ya", "や", "ヤ"), ("yu", "ゆ", "ユ"), ("yo", "よ", "ヨ"),
    # R
    ("ra", "ら", "ラ"), ("ri", "り", "リ"), ("ru", "る", "ル"),
    ("re", "れ", "レ"), ("ro", "ろ", "ロ"),
    # W
    ("wa", "わ", "ワ"), ("wo", "を", "ヲ"),
    # Final n
    ("n", "ん", "ン"),

    # --- dakuten ---
    # G
    ("ga", "が", "ガ"), ("gi", "ぎ", "ギ"), ("gu", "ぐ", "グ"),
    ("ge", "げ", "ゲ"), ("go", "ご", "ゴ"),
    # Z / J
    ("za", "ざ", "ザ"), ("ji", "じ", "ジ"), ("zu", "ず", "ズ"),
    ("ze", "ぜ", "ゼ"), ("zo", "ぞ", "ゾ"),
    # D
    ("da", "だ", "ダ"), ("de", "で", "デ"), ("do", "ど", "ド"),
    # B
    ("ba", "ば", "バ"), ("bi", "び", "ビ"), ("bu", "ぶ", "ブ"),
    ("be", "べ", "ベ"), ("bo", "ぼ", "ボ"),
    # --- handakuten ---
    ("pa", "ぱ", "パ"), ("pi", "ぴ", "ピ"), ("pu", "ぷ", "プ"),
    ("pe", "ぺ", "ペ"), ("po", "ぽ", "ポ"),

    # --- yoon (palatalised) ---
    ("kya", "きゃ", "キャ"), ("kyu", "きゅ", "キュ"), ("kyo", "きょ", "キョ"),
    ("sha", "しゃ", "シャ"), ("shu", "しゅ", "シュ"), ("sho", "しょ", "ショ"),
    ("cha", "ちゃ", "チャ"), ("chu", "ちゅ", "チュ"), ("cho", "ちょ", "チョ"),
    ("nya", "にゃ", "ニャ"), ("nyu", "にゅ", "ニュ"), ("nyo", "にょ", "ニョ"),
    ("hya", "ひゃ", "ヒャ"), ("hyu", "ひゅ", "ヒュ"), ("hyo", "ひょ", "ヒョ"),
    ("mya", "みゃ", "ミャ"), ("myu", "みゅ", "ミュ"), ("myo", "みょ", "ミョ"),
    ("rya", "りゃ", "リャ"), ("ryu", "りゅ", "リュ"), ("ryo", "りょ", "リョ"),
    ("gya", "ぎゃ", "ギャ"), ("gyu", "ぎゅ", "ギュ"), ("gyo", "ぎょ", "ギョ"),
    ("ja",  "じゃ", "ジャ"), ("ju",  "じゅ", "ジュ"), ("jo",  "じょ", "ジョ"),
    ("bya", "びゃ", "ビャ"), ("byu", "びゅ", "ビュ"), ("byo", "びょ", "ビョ"),
    ("pya", "ぴゃ", "ピャ"), ("pyu", "ぴゅ", "ピュ"), ("pyo", "ぴょ", "ピョ"),
)

#: Stable identifier the rest of the app uses to recognise the synthetic deck.
KANA_DECK = "kana"

# --------------------------------------------------------------------------- #
# Romaji -> hiragana conversion (typed-recall input, no IME needed)
# --------------------------------------------------------------------------- #
#: Full conversion table: the game table above plus IME-style aliases and the
#: rarer kana the synthetic deck doesn't drill (ぢ づ, loan-word combos).
_R2H: dict[str, str] = {r: h for r, h, _k in KANA_SOUNDS}
_R2H.update({
    # kunrei / IME aliases
    "si": "し", "ti": "ち", "tu": "つ", "hu": "ふ", "zi": "じ",
    "sya": "しゃ", "syu": "しゅ", "syo": "しょ",
    "tya": "ちゃ", "tyu": "ちゅ", "tyo": "ちょ",
    "zya": "じゃ", "zyu": "じゅ", "zyo": "じょ",
    "jya": "じゃ", "jyu": "じゅ", "jyo": "じょ",
    "cya": "ちゃ", "cyu": "ちゅ", "cyo": "ちょ",
    # rare-but-real kana
    "di": "ぢ", "du": "づ", "dzu": "づ", "dja": "ぢゃ", "dju": "ぢゅ",
    "vu": "ゔ",
    # loan-word combinations (readings of katakana words, stored as hiragana)
    "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ", "fo": "ふぉ",
    "va": "ゔぁ", "vi": "ゔぃ", "ve": "ゔぇ", "vo": "ゔぉ",
    "wi": "うぃ", "we": "うぇ", "who": "うぉ",
    "she": "しぇ", "che": "ちぇ", "je": "じぇ",
    "thi": "てぃ", "dhi": "でぃ", "thu": "てゅ", "dhu": "でゅ",
    "twu": "とぅ", "dwu": "どぅ",
    # small kana (IME x/l prefixes)
    "xa": "ぁ", "xi": "ぃ", "xu": "ぅ", "xe": "ぇ", "xo": "ぉ",
    "la": "ぁ", "li": "ぃ", "lu": "ぅ", "le": "ぇ", "lo": "ぉ",
    "xya": "ゃ", "xyu": "ゅ", "xyo": "ょ", "xtsu": "っ", "ltsu": "っ",
})
_VOWELS = "aeiou"

#: Reverse table for displaying romaji under kana: hiragana -> Hepburn-ish
#: romaji. Built from the game table plus the rarer kana it doesn't drill.
_H2R: dict[str, str] = {}
for _r, _h, _k in KANA_SOUNDS:
    _H2R.setdefault(_h, _r)
_H2R.update({
    "ぢ": "ji", "づ": "zu", "ゔ": "vu",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "しぇ": "she", "ちぇ": "che", "じぇ": "je",
    "てぃ": "ti", "でぃ": "di", "てゅ": "tyu", "でゅ": "dyu",
    "とぅ": "tu", "どぅ": "du",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
})


def hira_to_romaji(text: str) -> str:
    """Kana -> romaji for display hints ("がっこう" -> "gakkou").

    Handles yōon digraphs, っ (doubles the next consonant; っち -> tchi),
    ー (repeats the previous vowel) and ん. Katakana is folded to hiragana
    first; anything unknown passes through unchanged.
    """
    from kanjire.jputil import kata_to_hira

    s = kata_to_hira(text)
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "っ":
            # find the next mora's romaji and double its first consonant
            nxt = ""
            for ln in (2, 1):
                frag = s[i + 1:i + 1 + ln]
                if frag in _H2R:
                    nxt = _H2R[frag]
                    break
            if nxt and nxt[0] not in _VOWELS:
                out.append("t" if nxt.startswith("ch") else nxt[0])
            i += 1
            continue
        if ch == "ー":
            prev = out[-1] if out else ""
            if prev and prev[-1] in _VOWELS:
                out.append(prev[-1])
            else:
                out.append("-")
            i += 1
            continue
        for ln in (2, 1):
            frag = s[i:i + ln]
            if frag in _H2R:
                out.append(_H2R[frag])
                i += ln
                break
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def romaji_to_hira(text: str) -> str:
    """IME-style romaji -> hiragana ("kyou" -> きょう, "gakkou" -> がっこう).

    Standard conventions: doubled consonants make っ, ``nn``/``n'`` make ん
    (a lone *n* before a non-vowel also resolves to ん), ``-`` makes ー.
    Kana characters pass straight through (katakana folded to hiragana), so
    a player typing with a real IME is fine too. Unknown characters survive
    unchanged, which keeps the comparison honest (a wrong answer stays wrong).
    """
    from kanjire.jputil import is_kana, kata_to_hira

    s = text.strip().lower()
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if is_kana(ch) or ch in "ーっ":
            out.append(kata_to_hira(ch))
            i += 1
            continue
        if ch == "-":
            out.append("ー")
            i += 1
            continue
        # ん: "nn" always, "n'" explicitly, lone n before non-vowel/non-y/end.
        if ch == "n":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt == "n":
                out.append("ん")
                i += 2
                continue
            if nxt == "'":
                out.append("ん")
                i += 2
                continue
            if nxt == "" or (nxt not in _VOWELS and nxt != "y"
                             and not is_kana(nxt)):
                out.append("ん")
                i += 1
                continue
        # っ: doubled consonant (kk, tt, ss, pp...) plus the "tch" cluster.
        if (ch.isalpha() and ch not in _VOWELS and ch != "n"
                and i + 1 < len(s)
                and (s[i + 1] == ch or (ch == "t" and s[i + 1] == "c"))):
            out.append("っ")
            i += 1
            continue
        for ln in (4, 3, 2, 1):
            frag = s[i:i + ln]
            if frag in _R2H:
                out.append(_R2H[frag])
                i += ln
                break
        else:
            out.append(ch)
            i += 1
    return "".join(out)

SCRIPTS = ("hira", "kata", "both")
LENGTHS = (1, 2, 3)


def _new_word(sounds, script: str) -> Word:
    """Bundle a list of ``(romaji, hira, kata)`` rows into one :class:`Word`."""
    romaji = " ".join(s[0] for s in sounds)
    hira = "".join(s[1] for s in sounds)
    kata = "".join(s[2] for s in sounds)
    if script == "kata":
        expression, reading = kata, kata
    elif script == "hira":
        expression, reading = hira, hira
    else:  # both
        expression, reading = hira, kata
    return Word(
        id=hash((expression, reading, romaji)) & 0x7FFFFFFF,
        expression=expression,
        reading=reading,
        meaning=romaji,
        jlpt=None,
        freq=0.0,
        deck=KANA_DECK,
    )


def sample(
    n: int,
    *,
    length: int = 1,
    script: str = "both",
    rng: random.Random | None = None,
) -> list[Word]:
    """Return *n* freshly-invented kana "words" of the requested length.

    The same syllable can appear in different words within a round - that's
    desirable for drilling - but the generator dedupes by primary script so
    no two cards on the board ever look identical.
    """
    rng = rng or random
    if script not in SCRIPTS:
        script = "both"
    length = max(1, min(3, int(length)))
    out: list[Word] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = n * 40  # plenty of safety even for length=1, n=24
    while len(out) < n and attempts < max_attempts:
        attempts += 1
        sounds = rng.sample(KANA_SOUNDS, length)
        w = _new_word(sounds, script)
        if w.expression in seen:
            continue
        seen.add(w.expression)
        out.append(w)
    return out
