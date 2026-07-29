"""Build the clustering sidecar DB: genres, lookalikes, soundalikes.

Everything expensive about "which words belong together" is computed **here,
once, on the developer's machine**, and shipped as ``kanjire/data/clusters.db``
inside the bundle. The running game only ever reads small precomputed tables —
no NLP libraries, no model weights, no network on any player's device.

Three axes are produced:

* **Genres** (``word_genre``) — every word sorted into named topic buckets
  ("Food & Drink", "Weather & Sky") from :mod:`kanjire.data.genres`. Assigned
  by walking the WordNet hypernym tree upward from each English gloss until it
  hits one of a genre's *anchor* synsets, with hand-written keyword rules for
  the things WordNet can't know (Japanese counters, grammar words, politeness).
* **Lookalikes** (``kanji_shape``) — kanji that are genuinely easy to confuse
  by eye. Component overlap alone misses 土/士 and 未/末, so the score also
  compares *rendered glyphs*: each kanji is drawn to a bitmap and correlated
  against every other. That is the part that makes a "tell these apart" game
  work.
* **Soundalikes** (``word_sound``) — words whose readings are a near-miss of
  each other (病院/美容院, きって/きて), by edit distance over normalised kana.

Run after the vocabulary DB exists::

    python scripts/build_clusters.py [--force] [--report]

Requires (build-time only, never shipped): ``nltk`` + the WordNet corpus,
``jamdict`` (KanjiDic stroke counts), ``numpy`` and ``Pillow``.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from kanjire.data.genres import GENRES
from kanjire.jputil import kanji_chars
from kanjire.paths import DATA_DIR


class BuildError(RuntimeError):
    """A step could not run — reported to the caller, never a bare exit, so
    scripts/setup_data.py can carry on with the rest of the pipeline."""


OUT_PATH = DATA_DIR / "clusters.db"
VOCAB_PATH = DATA_DIR / "kanjire.db"
KANJIDATA_PATH = DATA_DIR / "kanjidata.db"
FONT_PATH = Path(__file__).resolve().parent.parent / "kanjire" / "fonts" / \
    "ZenMaruGothic-Regular.ttf"

SCHEMA = """
CREATE TABLE genres (
    key   TEXT PRIMARY KEY,
    ord   INTEGER NOT NULL,
    words INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE word_genre (
    expression TEXT NOT NULL,
    reading    TEXT NOT NULL,
    genre      TEXT NOT NULL,
    rank       INTEGER NOT NULL,        -- 0 = primary genre, 1 = secondary
    score      REAL NOT NULL,
    PRIMARY KEY (expression, reading, genre)
);
CREATE INDEX idx_word_genre_genre ON word_genre(genre, rank);
CREATE TABLE kanji_shape (
    kanji     TEXT NOT NULL,
    neighbour TEXT NOT NULL,
    score     REAL NOT NULL,            -- 0..1, higher = more confusable
    PRIMARY KEY (kanji, neighbour)
);
CREATE TABLE word_sound (
    expression   TEXT NOT NULL,
    reading      TEXT NOT NULL,
    n_expression TEXT NOT NULL,
    n_reading    TEXT NOT NULL,
    score        REAL NOT NULL,         -- 0..1, higher = more confusable
    PRIMARY KEY (expression, reading, n_expression, n_reading)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# --------------------------------------------------------------------------- #
# Genre rules
# --------------------------------------------------------------------------- #
#: Anchor synsets per genre. A word lands in a genre when its gloss's synset
#: reaches one of these by walking *up* the hypernym tree — and the closer the
#: anchor, the stronger the vote, so 教師 "teacher" prefers school (educator,
#: 2 hops) over people (person, 4 hops) without any special-casing.
ANCHORS: dict[str, tuple[str, ...]] = {
    "food": ("food.n.01", "food.n.02", "beverage.n.01", "meal.n.01",
             "dish.n.02", "cooking.n.01", "eating.n.01", "taste.n.01",
             "restaurant.n.01", "eat.v.01", "drink.v.01"),
    "animals": ("animal.n.01", "bird.n.01", "fish.n.01", "insect.n.01"),
    "plants": ("plant.n.02", "flower.n.01", "tree.n.01", "fruit.n.01",
               "vegetable.n.01", "seed.n.01"),
    "nature": ("natural_object.n.01", "body_of_water.n.01", "mountain.n.01",
               "rock.n.01", "soil.n.01", "fire.n.01", "mineral.n.01",
               "land.n.04", "forest.n.01", "star.n.01"),
    "weather": ("weather.n.01", "atmospheric_phenomenon.n.01",
                "precipitation.n.03", "wind.n.01", "sky.n.01", "cloud.n.01"),
    "body": ("body_part.n.01", "organ.n.01", "body.n.01", "skin.n.01"),
    "health": ("disease.n.01", "medicine.n.01", "medicine.n.02",
               "symptom.n.01", "hospital.n.01", "injury.n.01",
               "pain.n.01", "physical_condition.n.01"),
    "family": ("relative.n.01", "family.n.01", "parent.n.01", "child.n.01",
               "sibling.n.01"),
    "people": ("person.n.01", "people.n.01", "name.n.01"),
    "emotion": ("feeling.n.01", "emotion.n.01", "emotional_state.n.01"),
    "mind": ("cognition.n.01", "thinking.n.01", "knowledge.n.01",
             "memory.n.01", "idea.n.01", "think.v.03", "remember.v.01",
             "understand.v.01"),
    "speech": ("language.n.01", "word.n.01", "speech_act.n.01",
               "speech.n.02", "utterance.n.01", "sentence.n.01",
               "talk.v.02", "say.v.01"),
    "communication": ("message.n.02", "mail.n.01", "telephone.n.01",
                      "newspaper.n.01", "broadcasting.n.02", "letter.n.01",
                      "publication.n.01"),
    "school": ("education.n.01", "educational_institution.n.01",
               "student.n.01", "educator.n.01", "examination.n.02",
               "study.n.02", "learn.v.01", "teach.v.01", "textbook.n.01"),
    "work": ("occupation.n.01", "work.n.01", "business.n.01", "company.n.01",
             "employee.n.01", "factory.n.01", "work.v.02"),
    "money": ("money.n.01", "price.n.02", "commerce.n.01", "payment.n.01",
              "shop.n.01", "buy.v.01", "sell.v.01", "financial_gain.n.01",
              "tax.n.01"),
    "home": ("furniture.n.01", "house.n.01", "room.n.01",
             "home_appliance.n.01", "bedclothes.n.01", "housing.n.01"),
    "clothing": ("clothing.n.01", "footwear.n.02", "headdress.n.01",
                 "accessory.n.01", "fabric.n.01"),
    "tools": ("tool.n.01", "container.n.01", "implement.n.01",
              "utensil.n.01", "device.n.01", "weapon.n.01"),
    "technology": ("machine.n.01", "computer.n.01", "electronic_equipment.n.01",
                   "engine.n.01", "electricity.n.01", "technology.n.01"),
    "transport": ("vehicle.n.01", "conveyance.n.03", "road.n.01",
                  "public_transport.n.01", "aircraft.n.01", "ship.n.01"),
    "travel": ("travel.n.01", "journey.n.01", "hotel.n.01", "vacation.n.01",
               "tourist.n.01", "baggage.n.01"),
    "city": ("building.n.01", "town.n.01", "city.n.01", "structure.n.01",
             "district.n.01", "park.n.02"),
    "geography": ("country.n.02", "region.n.03", "continent.n.01",
                  "geographical_area.n.01", "island.n.01"),
    "time": ("time_period.n.01", "time_unit.n.01", "day.n.01", "month.n.01",
             "year.n.01", "hour.n.01", "season.n.02", "clock.n.01",
             "time.n.03"),
    "numbers": ("number.n.02", "integer.n.01", "digit.n.01",
                "arithmetic.n.01", "definite_quantity.n.01"),
    "quantity": ("magnitude.n.01", "quantity.n.01", "size.n.01",
                 "indefinite_quantity.n.01", "measure.n.02"),
    "colors": ("color.n.01", "shape.n.02", "pattern.n.01"),
    "position": ("direction.n.01", "position.n.07", "location.n.01"),
    "movement": ("motion.n.06", "travel.v.01", "run.v.01", "move.v.02"),
    "actions": ("touch.v.01", "make.v.03", "change.v.01", "hold.v.02",
                "put.v.01", "cut.v.01", "open.v.01", "clean.v.01"),
    "social": ("social_relation.n.01", "party.n.01", "marriage.n.01",
               "help.v.01", "meet.v.01", "ceremony.n.03", "gift.n.01"),
    "culture": ("religion.n.02", "deity.n.01", "tradition.n.01",
                "belief.n.01", "temple.n.01", "myth.n.01"),
    "arts": ("music.n.01", "art.n.01", "painting.n.01", "literature.n.01",
             "musical_instrument.n.01", "dancing.n.01", "movie.n.01",
             "photograph.n.01"),
    "sports": ("sport.n.01", "game.n.01", "athletics.n.01", "exercise.n.01",
               "play.v.01", "contest.n.01"),
    "society": ("politics.n.02", "government.n.01", "law.n.02", "war.n.01",
                "crime.n.01", "society.n.01", "organization.n.01",
                "military.n.01"),
    "qualities": ("property.n.02", "attribute.n.02", "quality.n.01"),
}

#: WordNet lexicographer file -> genre. A weak vote, used when no anchor is
#: reached: it still gets "noun.food" words into food when the specific
#: hypernym chain took an unexpected route.
LEXNAMES: dict[str, str] = {
    "noun.food": "food", "verb.consumption": "food",
    "noun.animal": "animals", "noun.plant": "plants",
    "noun.substance": "nature", "noun.phenomenon": "nature",
    "verb.weather": "weather",
    "noun.body": "body", "verb.body": "body",
    "noun.feeling": "emotion", "verb.emotion": "emotion",
    "noun.cognition": "mind", "verb.cognition": "mind",
    "verb.perception": "mind",
    "noun.communication": "communication", "verb.communication": "speech",
    "noun.possession": "money",
    # give / receive / lend / borrow are social acts far more often than
    # commercial ones; buying and selling have their own anchors.
    "verb.possession": "social",
    "noun.artifact": "tools",
    "noun.location": "position",
    "verb.motion": "movement",
    "verb.contact": "actions", "verb.change": "actions",
    "verb.creation": "actions", "verb.stative": "qualities",
    "verb.social": "social", "verb.competition": "sports",
    "noun.time": "time", "noun.quantity": "quantity",
    "noun.shape": "colors", "noun.person": "people",
    "noun.group": "society", "noun.act": "actions",
    "noun.attribute": "qualities", "noun.state": "qualities",
    "adj.all": "qualities", "adj.pert": "qualities",
}

#: Hand-written overrides, matched against the whole lowercased gloss. These
#: outrank WordNet (weight 4.0 vs ~1.0) and exist for two reasons: things
#: WordNet has no concept of (Japanese counters, honorifics, particles), and
#: the handful of places its tree is actively misleading.
KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("grammar", (r"\bcounter for\b", r"\bparticle\b", r"\bprefix\b",
                 r"\bsuffix\b", r"\bhonorific\b", r"\bpolite\b",
                 r"\bpronoun\b", r"\bconjunction\b", r"\binterjection\b",
                 r"\bauxiliary\b", r"\bexpressing\b",
                 r"^(this|that|these|those|it|he|she|they|we|you|i)$",
                 r"^(and|but|or|so|because|however|therefore|although)$",
                 r"^(very|quite|rather|already|still|also|only|about)$",
                 r"^(who|what|when|where|which|why|how|whose)\b",
                 r"\bso-called\b", r"\bnevertheless\b",
                 r"^(that|this) one\b", r"^(such|like that|like this)\b",
                 r"^not (very|much|at all)\b", r"\bkind of\b",
                 r"^(moreover|furthermore|besides|however|instead|anyway)\b",
                 r"^(maybe|perhaps|probably|certainly|of course)\b",
                 r"^(at least|at most|at all|by all means|and then|hence)\b",
                 r"^(as it is|without change|for that reason|as well)\b")),
    ("numbers", (r"\bcounter for\b", r"\bnumber of\b",
                 r"^(one|two|three|four|five|six|seven|eight|nine|ten)$",
                 r"\bhundred\b", r"\bthousand\b", r"\bmillion\b",
                 r"\bhalf\b", r"\bdozen\b", r"\bdigit\b")),
    ("time", (r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
              r"\b(january|february|march|april|may|june|july|august|"
              r"september|october|november|december)\b",
              r"\b(yesterday|today|tomorrow|tonight)\b",
              r"\b(morning|afternoon|evening|noon|midnight|dawn)\b",
              r"\b(spring|summer|autumn|fall|winter)\b",
              r"\b(week|month|year|day|hour|minute|second)s?\b",
              r"\bo'clock\b", r"\blast time\b", r"\bnext time\b")),
    ("position", (r"\b(above|below|under|beneath|behind|beside|between)\b",
                  r"\b(left|right|front|back|top|bottom|middle|centre|center)\b",
                  r"\b(north|south|east|west)\b",
                  r"\b(inside|outside|upward|downward|forward|backward)\b",
                  r"\bdirection\b", r"\bvicinity\b", r"\bopposite side\b",
                  r"^(here|there|everywhere|somewhere|anywhere)\b",
                  r"^here and there\b")),
    ("colors", (r"\b(red|blue|green|yellow|black|white|brown|purple|pink|"
                r"orange|grey|gray)\b", r"\bcolou?r\b",
                r"\b(round|square|triangle|circle|shape)\b")),
    ("family", (r"\b(mother|father|parent|child|son|daughter|brother|sister|"
                r"grandmother|grandfather|grandchild|uncle|aunt|cousin|"
                r"nephew|niece|husband|wife|spouse|relative|family)\b",)),
    ("body", (r"\b(head|face|eye|ear|nose|mouth|tooth|teeth|neck|shoulder|"
              r"arm|hand|finger|chest|stomach|back|waist|leg|knee|foot|feet|"
              r"hair|skin|blood|bone|heart|throat)\b",)),
    ("food", (r"\b(rice|bread|meat|fish dish|soup|tea|coffee|water to drink|"
              r"sugar|salt|egg|milk|fruit|vegetable|noodle|sake|alcohol|"
              r"breakfast|lunch|dinner|snack|sweets|cake|restaurant|menu|"
              r"delicious|tasty)\b", r"\bto eat\b", r"\bto drink\b")),
    ("weather", (r"\b(rain|snow|wind|cloud|storm|typhoon|fog|thunder|"
                 r"lightning|weather|climate|sunny|humid)\b",)),
    ("health", (r"\b(illness|disease|sick|fever|cold \(illness\)|injur|"
                r"medicine|hospital|doctor|nurse|dentist|pain|hurt|cure|"
                r"health|surgery|patient)\b",)),
    ("school", (r"\b(school|student|pupil|teacher|professor|classroom|"
                r"homework|lesson|textbook|exam|test|university|college|"
                r"kindergarten|study|learn|graduat)\b",)),
    ("money", (r"\b(money|yen|dollar|price|cost|cheap|expensive|pay|salary|"
               r"wage|bank|shop|store|buy|sell|purchase|change \(money\)|"
               r"wallet|bill|fee|tax|profit|budget)\b",)),
    ("transport", (r"\b(train|bus|car|taxi|bicycle|bike|airplane|plane|ship|"
                   r"boat|subway|station|platform|ticket|traffic|drive|"
                   r"railway|highway|road)\b",)),
    ("clothing", (r"\b(clothes|clothing|shirt|trousers|pants|skirt|dress|"
                  r"coat|jacket|hat|cap|shoe|sock|glove|tie|pocket|button|"
                  r"sleeve|wear|kimono)\b",)),
    ("home", (r"\b(house|home|room|kitchen|bathroom|toilet|bedroom|door|"
              r"window|wall|floor|ceiling|roof|stairs|garden|furniture|"
              r"table|chair|desk|bed|shelf|curtain|futon|tatami)\b",)),
    ("culture", (r"\b(shrine|temple|god|buddha|prayer|festival|ceremony|"
                 r"tradition|custom|religion|kimono|tatami|new year|"
                 r"tea ceremony|manners)\b",)),
    ("technology", (r"\b(computer|internet|telephone|machine|engine|"
                    r"electricity|battery|camera|television|radio|software|"
                    r"data|robot)\b",)),
    ("sports", (r"\b(sport|baseball|soccer|football|tennis|swim|ski|"
                r"marathon|match \(game\)|game|player|team|competition|"
                r"exercise|gym)\b",)),
    ("arts", (r"\b(music|song|sing|piano|guitar|drum|paint|picture|drawing|"
              r"photograph|movie|film|theatre|theater|dance|novel|poem|art)\b",)),
    ("society", (r"\b(government|politic|law|court|police|prison|crime|war|"
                 r"army|soldier|election|nation|society|citizen|rights)\b",)),
    ("work", (r"\b(work|job|office|company|employee|boss|colleague|meeting|"
              r"business|career|factory|manager|staff|overtime)\b",)),
    ("travel", (r"\b(travel|trip|journey|tour|sightseeing|hotel|inn|"
                r"souvenir|luggage|baggage|suitcase|passport|visa|"
                r"airport|itinerary|reservation|departure|arrival|"
                r"tourist|vacation|holiday|camping|voyage|excursion)\b",)),
    ("geography", (r"\b(country|countries|nation|world|foreign|abroad|"
                   r"overseas|continent|island|peninsula|prefecture|"
                   r"province|territory|border|capital city|globe|"
                   r"mainland|region)\b",
                   r"\b(japan|china|korea|america|europe|asia|africa|"
                   r"england|france|germany|russia|india)\b")),
    ("expressions", (r"\bthank you\b", r"\b(hello|goodbye|good-bye)\b",
                     r"\bgood (morning|evening|night|luck)\b",
                     r"\b(excuse me|i'm sorry|congratulations|welcome)\b",
                     r"\b(greeting|interjection|exclamation|set phrase)\b",
                     r"^(ah|oh|eh|hey|yes|no|well|hm+)$",
                     r"\bsaid when\b", r"\bexpression of\b",
                     r"\bexpression for\b", r"\b(humble|modest|honorific)\b")),
)

#: A gloss that is a single adverb ("Unfortunately", "Gradually") describes
#: *how*, not *what* — its own bucket, and a big one in Japanese. English
#: adverbs end in -ly, but so do plenty of nouns, hence the stoplist.
_ADVERB_STOP = frozenset((
    "family", "july", "reply", "supply", "apply", "imply", "multiply",
    "assembly", "jelly", "belly", "rally", "bully", "holy", "italy",
    "ugly", "silly", "lily", "ally", "folly", "gully", "melancholy",
))

#: Words whose gloss is only a grammar note can never carry a genre; better
#: no genre at all than a wrong one, so a word needs this much score.
MIN_SCORE = 0.55
#: A second genre is kept when it scores at least this fraction of the first.
SECONDARY_RATIO = 0.62
KEYWORD_WEIGHT = 4.0
#: Weight of a WordNet lexicographer-file vote — just over MIN_SCORE, so a
#: lexname can place a word by itself but any real anchor still outranks it.
LEXNAME_WEIGHT = 0.62


def _phrases(meaning: str) -> list[tuple[str, bool]]:
    """The gloss split into ``(phrase, is_verb)`` pairs, best first.

    The verb flag matters more than it looks: "To sink" and "To stop" become
    the bare words *sink* and *stop*, whose noun senses are a basin and a bus
    stop — which is how 沈める ended up filed under Tools and 止める under
    Position. Knowing the gloss was a verb keeps the lookup honest.
    """
    text = re.sub(r"\([^)]*\)", " ", meaning.lower())
    text = text.replace("~", " ").replace("～", " ")
    out: list[tuple[str, bool]] = []
    for part in re.split(r"[;,/]", text):
        part = part.strip(" -.!?—")
        is_verb = bool(re.match(r"^to\s+\w", part))
        part = re.sub(r"^(to be|to|the|a|an|be|one's|something|someone)\s+",
                      "", part).strip()
        if part:
            out.append((part, is_verb))
    return out[:4]


class _Assigner:
    """Scores a gloss against every genre using WordNet + the keyword rules."""

    def __init__(self, wn) -> None:
        self.wn = wn
        self.keywords = tuple(
            (genre, tuple(re.compile(p) for p in pats))
            for genre, pats in KEYWORDS
        )
        # Resolve anchors once; a typo'd synset name should be loud, not silent.
        self.anchors: dict[str, str] = {}
        missing: list[str] = []
        for genre, names in ANCHORS.items():
            for name in names:
                try:
                    self.anchors[self.wn.synset(name).name()] = genre
                except Exception:
                    missing.append(f"{genre}:{name}")
        if missing:
            print(f"  ! unknown anchor synsets ignored: {', '.join(missing)}")
        self._cache: dict[str, dict[str, float]] = {}

    def _synsets(self, phrase: str, is_verb: bool = False) -> list:
        """Synsets for a phrase, trying the whole thing before its head word.

        A verb gloss looks up verb senses only; if WordNet has none it falls
        back to any part of speech rather than giving up.
        """
        wn = self.wn
        pos = wn.VERB if is_verb else None

        def look(term: str) -> list:
            got = wn.synsets(term, pos=pos) if pos else wn.synsets(term)
            if not got and pos:
                got = wn.synsets(term)
            return got

        got = look(phrase.replace(" ", "_"))
        if got:
            return got[:3]
        words = phrase.split()
        if len(words) > 1:
            # English noun phrases are head-final ("telephone pole" -> pole);
            # verb phrases head-initial ("look up at" -> look).
            for cand in ((words[0], words[-1]) if is_verb
                         else (words[-1], words[0])):
                got = look(cand)
                if got:
                    return got[:2]
        return []

    def _synset_votes(self, syn) -> dict[str, float]:
        """Genre votes from one synset: anchors it reaches, then its lexname."""
        key = syn.name()
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        votes: dict[str, float] = defaultdict(float)
        # Breadth-first up the hypernym tree, nearest anchors weighing most.
        frontier, seen, depth = [syn], {syn.name()}, 0
        while frontier and depth <= 8:
            nxt = []
            for node in frontier:
                genre = self.anchors.get(node.name())
                if genre:
                    votes[genre] = max(votes[genre], 1.0 / (1.0 + depth))
                for up in node.hypernyms() + node.instance_hypernyms():
                    if up.name() not in seen:
                        seen.add(up.name())
                        nxt.append(up)
            frontier, depth = nxt, depth + 1
        lex = LEXNAMES.get(syn.lexname())
        if lex:
            # Strong enough to place a word on its own: WordNet's lexicographer
            # file is a real (if coarse) topic, and demanding an anchor as well
            # left 44% of the deck genre-less.
            votes[lex] = max(votes[lex], LEXNAME_WEIGHT)
        self._cache[key] = dict(votes)
        return self._cache[key]

    def score(self, meaning: str) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)
        phrases = _phrases(meaning)
        # Patterns are tested against the raw gloss *and* each cleaned phrase:
        # the raw text keeps punctuation the phrase split throws away ("Ah!"),
        # while the phrases are what the ^anchored^ patterns need.
        raw = meaning.lower().strip()
        texts = [ph for ph, _v in phrases]
        for genre, patterns in self.keywords:
            if any(p.search(raw) or any(p.search(ph) for ph in texts)
                   for p in patterns):
                scores[genre] += KEYWORD_WEIGHT
        if raw.endswith("!") or raw.startswith("("):
            scores["expressions"] += KEYWORD_WEIGHT
        for phrase in texts:
            if (phrase.endswith("ly") and len(phrase) > 4
                    and " " not in phrase and phrase not in _ADVERB_STOP):
                scores["manner"] += KEYWORD_WEIGHT
                break
        for i, (phrase, is_verb) in enumerate(phrases):
            weight = 1.0 / (1.0 + i)          # the first gloss counts most
            for j, syn in enumerate(self._synsets(phrase, is_verb)):
                sense_weight = weight / (1.0 + j)
                for genre, vote in self._synset_votes(syn).items():
                    scores[genre] += vote * sense_weight
        return scores


def build_genres(out: sqlite3.Connection, words, *, report: bool) -> None:
    try:
        from nltk.corpus import wordnet as wn
        wn.synsets("test")
    except Exception as exc:                                  # pragma: no cover
        raise BuildError("WordNet is unavailable — run:\n"
                 "    python -m pip install nltk\n"
                 "    python -c \"import nltk; nltk.download('wordnet')\"\n"
                 f"({exc})")

    assigner = _Assigner(wn)
    rows: list[tuple] = []
    per_genre: dict[str, int] = defaultdict(int)
    unassigned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for expr, reading, meaning in words:
        key = (expr, reading)
        if key in seen:
            continue
        seen.add(key)
        scores = assigner.score(meaning or "")
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        ranked = [(g, s) for g, s in ranked if s >= MIN_SCORE]
        if not ranked:
            unassigned.append((expr, meaning))
            continue
        top = ranked[0][1]
        for rank, (genre, score) in enumerate(ranked[:2]):
            if rank and score < top * SECONDARY_RATIO:
                break
            rows.append((expr, reading, genre, rank, round(score, 4)))
            per_genre[genre] += 1

    out.executemany(
        "INSERT OR REPLACE INTO word_genre "
        "(expression, reading, genre, rank, score) VALUES (?,?,?,?,?)", rows)
    out.executemany(
        "INSERT OR REPLACE INTO genres (key, ord, words) VALUES (?,?,?)",
        [(g.key, i, per_genre.get(g.key, 0)) for i, g in enumerate(GENRES)])

    total = len(seen)
    placed = total - len(unassigned)
    print(f"  genres: {placed}/{total} words placed "
          f"({placed / max(total, 1):.0%}), {len(rows)} assignments")
    if report:
        print("\n  words per genre:")
        for g in GENRES:
            n = per_genre.get(g.key, 0)
            flag = "   <-- EMPTY" if n == 0 else ("   <-- thin" if n < 30 else "")
            print(f"    {g.icon} {g.key:<15} {n:>5}{flag}")
        print(f"\n  unplaced sample ({len(unassigned)} total):")
        for expr, meaning in unassigned[:25]:
            print(f"    {expr}  |  {meaning}")


# --------------------------------------------------------------------------- #
# Lookalikes
# --------------------------------------------------------------------------- #
SHAPE_NEIGHBOURS = 12
SHAPE_MIN = 0.42
#: Weights for the three shape signals. The bitmap dominates on purpose: it is
#: the only one that knows 土 and 士 look alike.
W_BITMAP, W_COMPONENT, W_STROKE = 0.55, 0.32, 0.13


def _render_grid(chars: list[str], size: int = 48, box: int = 56):
    """Every kanji as a mean-centred, unit-length 16x16 ink vector."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(FONT_PATH), size)
    vecs = np.zeros((len(chars), 16 * 16), dtype=np.float32)
    for i, ch in enumerate(chars):
        img = Image.new("L", (box, box), 0)
        draw = ImageDraw.Draw(img)
        left, top, right, bottom = draw.textbbox((0, 0), ch, font=font)
        draw.text(((box - (right - left)) / 2 - left,
                   (box - (bottom - top)) / 2 - top), ch, font=font, fill=255)
        small = np.asarray(img.resize((16, 16), Image.BILINEAR),
                           dtype=np.float32).ravel()
        small -= small.mean()                 # correlate shape, not ink volume
        norm = np.linalg.norm(small)
        if norm > 0:
            small /= norm
        vecs[i] = small
    return vecs


def build_shape(out: sqlite3.Connection, chars: list[str]) -> None:
    import numpy as np

    if not FONT_PATH.exists():                                # pragma: no cover
        raise BuildError(f"font missing: {FONT_PATH} — run scripts/fetch_fonts.py")

    comps: dict[str, set[str]] = {}
    if KANJIDATA_PATH.exists():
        con = sqlite3.connect(KANJIDATA_PATH)
        comps = {r[0]: set(r[1].split())
                 for r in con.execute("SELECT kanji, components FROM components")}
        con.close()

    strokes: dict[str, int] = {}
    try:
        from jamdict import Jamdict
        jd = Jamdict()
        for ch in chars:
            try:
                found = jd.lookup(ch).chars
                if found and found[0].stroke_count:
                    strokes[ch] = int(found[0].stroke_count)
            except Exception:
                pass
    except Exception as exc:                                  # pragma: no cover
        print(f"  ! jamdict unavailable, skipping stroke counts ({exc})")

    vecs = _render_grid(chars)
    sim = vecs @ vecs.T                       # cosine similarity, all pairs
    np.fill_diagonal(sim, -1.0)
    idx_of = {ch: i for i, ch in enumerate(chars)}

    rows: list[tuple] = []
    # Only the bitmap's best few dozen are worth the component/stroke maths.
    top_k = min(len(chars) - 1, 60)
    order = np.argpartition(-sim, top_k - 1, axis=1)[:, :top_k]
    for i, ch in enumerate(chars):
        mine, my_strokes = comps.get(ch, set()), strokes.get(ch)
        scored: list[tuple[float, str]] = []
        for j in order[i]:
            other = chars[j]
            bitmap = float(sim[i, j])
            if bitmap <= 0.0:
                continue
            theirs = comps.get(other, set())
            union = mine | theirs
            jaccard = len(mine & theirs) / len(union) if union else 0.0
            if my_strokes and strokes.get(other):
                delta = abs(my_strokes - strokes[other])
                stroke = max(0.0, 1.0 - delta / 6.0)
            else:
                stroke = 0.0
            total = (W_BITMAP * bitmap + W_COMPONENT * jaccard
                     + W_STROKE * stroke)
            if total >= SHAPE_MIN:
                scored.append((total, other))
        scored.sort(reverse=True)
        for total, other in scored[:SHAPE_NEIGHBOURS]:
            rows.append((ch, other, round(total, 4)))
        idx_of[ch] = i

    out.executemany("INSERT OR REPLACE INTO kanji_shape "
                    "(kanji, neighbour, score) VALUES (?,?,?)", rows)
    have = len({r[0] for r in rows})
    print(f"  lookalikes: {len(rows)} pairs over {have}/{len(chars)} kanji")


# --------------------------------------------------------------------------- #
# Soundalikes
# --------------------------------------------------------------------------- #
SOUND_NEIGHBOURS = 10
SOUND_MIN = 0.6
_KATA_TO_HIRA = {chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)}
#: Small kana glue onto the previous mora rather than standing alone, and the
#: long mark just holds the previous vowel — normalising both makes きって and
#: きて a one-edit pair, which is exactly the confusion worth drilling.
_SMALL = "ゃゅょぁぃぅぇぉっ"


def _norm_reading(reading: str) -> str:
    out = "".join(_KATA_TO_HIRA.get(c, c) for c in reading)
    return "".join(c for c in out if c not in _SMALL and c != "ー")


def _edit_distance(a: str, b: str, cap: int) -> int:
    """Levenshtein distance, abandoning early once it exceeds *cap*."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(val)
            best = min(best, val)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def build_sound(out: sqlite3.Connection, words) -> None:
    entries: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for expr, reading, _m in words:
        key = (expr, reading)
        if key in seen:
            continue
        seen.add(key)
        norm = _norm_reading(reading)
        if len(norm) >= 2:
            entries.append((expr, reading, norm))

    # Block on shared kana bigrams so we only measure plausible pairs.
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, (_e, _r, norm) in enumerate(entries):
        for gram in {norm[k:k + 2] for k in range(len(norm) - 1)}:
            buckets[gram].append(i)

    rows: list[tuple] = []
    for i, (expr, reading, norm) in enumerate(entries):
        cands: set[int] = set()
        for gram in {norm[k:k + 2] for k in range(len(norm) - 1)}:
            bucket = buckets[gram]
            if len(bucket) <= 400:      # skip ultra-common grams: no signal
                cands.update(bucket)
        cands.discard(i)
        scored: list[tuple[float, str, str]] = []
        for j in cands:
            other_e, other_r, other_n = entries[j]
            if other_e == expr or other_n == norm:
                continue                # same word, or a true homophone pair
            longest = max(len(norm), len(other_n))
            cap = max(1, int(longest * (1.0 - SOUND_MIN)))
            dist = _edit_distance(norm, other_n, cap)
            score = 1.0 - dist / longest
            if score >= SOUND_MIN:
                scored.append((score, other_e, other_r))
        scored.sort(reverse=True)
        for score, other_e, other_r in scored[:SOUND_NEIGHBOURS]:
            rows.append((expr, reading, other_e, other_r, round(score, 4)))

    out.executemany(
        "INSERT OR REPLACE INTO word_sound (expression, reading, "
        "n_expression, n_reading, score) VALUES (?,?,?,?,?)", rows)
    have = len({(r[0], r[1]) for r in rows})
    print(f"  soundalikes: {len(rows)} pairs over {have}/{len(entries)} words")


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if clusters.db already exists")
    ap.add_argument("--report", action="store_true",
                    help="print per-genre counts and unplaced words")
    ap.add_argument("--only", choices=("genres", "shape", "sound"),
                    help="rebuild a single table (implies --force)")
    args = ap.parse_args(argv)

    if OUT_PATH.exists() and not (args.force or args.only):
        print(f"{OUT_PATH} exists — pass --force to rebuild.")
        return 0
    if not VOCAB_PATH.exists():
        raise BuildError(f"vocabulary DB missing: {VOCAB_PATH} "
                 "— run scripts/setup_data.py first")

    src = sqlite3.connect(VOCAB_PATH)
    words = list(src.execute(
        "SELECT expression, reading, meaning FROM words ORDER BY expression"))
    chars = sorted({ch for expr, _r, _m in words for ch in kanji_chars(expr)})
    src.close()
    print(f"Clustering {len(words)} words / {len(chars)} kanji -> {OUT_PATH}")

    keep: dict[str, list[tuple]] = {}
    if args.only and OUT_PATH.exists():
        old = sqlite3.connect(OUT_PATH)
        for table in ("word_genre", "kanji_shape", "word_sound"):
            try:
                keep[table] = list(old.execute(f"SELECT * FROM {table}"))
            except sqlite3.Error:
                pass
        old.close()

    OUT_PATH.unlink(missing_ok=True)
    out = sqlite3.connect(OUT_PATH)
    out.executescript(SCHEMA)

    for table, rows in keep.items():
        if not rows:
            continue
        marks = ",".join("?" * len(rows[0]))
        out.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({marks})", rows)

    if args.only in (None, "genres"):
        out.execute("DELETE FROM word_genre")
        build_genres(out, words, report=args.report)
    if args.only in (None, "shape"):
        out.execute("DELETE FROM kanji_shape")
        build_shape(out, chars)
    if args.only in (None, "sound"):
        out.execute("DELETE FROM word_sound")
        build_sound(out, words)

    out.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                    [("genre_count", str(len(GENRES))),
                     ("source_words", str(len(words)))])
    out.commit()
    out.execute("VACUUM")
    out.close()
    print(f"Done: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        sys.exit(str(exc))
