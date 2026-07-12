# KanjiRe — Beginner-to-Reader Roadmap

> **STATUS (2026-07-12): Tiers 0–3 are fully shipped** — v0.3.0 (Tier 0 +
> FSRS spine), v0.4.0 (typed recall, confused-pair boards), v0.5.0 (kanji
> anatomy, sentences, pitch, coverage, placement), v0.6.0 (Journey, Reading
> Room, listening, leech hunts), v0.7.0 (Reading Room v2: own texts).
> Tier 4 (handwriting, grammar-lite, Steam, sync) awaits discussion after
> battle-testing. Implementation details live in `docs/ARCHITECTURE.md`.

> Written 2026-07-12 from a three-track research pass (feature analysis of
> WaniKani / jpdb / Anki+FSRS / Renshuu / Bunpro / Satori Reader / Wagotabi /
> MaruMori / Kanji Study / Skritter / Ringotan / Duolingo; community
> wishlists and complaints from WaniKani forums, HN, Reddit-consensus
> writeups, Steam; and learning-science literature — FSRS, retrieval
> practice, coverage curves, tadoku, motivation research), cross-checked
> against the audited codebase map in `docs/ARCHITECTURE.md`.

## 1. Vision

KanjiRe's goal: take a learner from **zero Japanese (kana) to genuinely
reading real text**, as a *game* — not another flashcard grinder. The
research converges on one pipeline that works:

> kana → frequency-ordered vocab with kanji taught *inside* words (components
> and phonetic series as connective tissue) → adaptive spaced retrieval,
> hardened toward recall → per-word stats powering an honest coverage meter
> and adaptive furigana → graded reading as early as possible, feeding
> unknown words back into review → audio everywhere → short sessions,
> forgiving streaks.

KanjiRe already owns the rarest ingredients: a **per-word cross-deck stats
DB** (the substrate for everything above), a **pure tested game engine**, a
**corpus-ingest pipeline** (jpdb's killer "make a deck from the media you
care about" feature — we already have it), a kana deck for true beginners,
gamified Survival with hearts/新/bounties, and a **self-updater** that lets
us ship all of this incrementally to real users. What's missing is the
spine that connects sessions across *days*: scheduling, progression,
context, and reading.

Positioning: the community already loves kanji-as-game (Wagotabi 98%
positive, Yomifuda, Kanji Combat) but those are content-authored RPGs.
KanjiRe's niche is the **arcade layer over a real adaptive SRS** — jpdb's
brains with Wagotabi's heart.

## 2. Gap analysis (what the audit + research say we lack)

| Pipeline stage | KanjiRe today | Gap |
|---|---|---|
| Scheduling across days | per-word streak/score buckets, session-local sampler | no memory model, no "due" concept, no review log — knowledge decays invisibly |
| Kanji knowledge | per-kanji stats aggregation (display only) | no components, no phonetic series (keisei), no kanji→word dependency |
| Context | isolated word cards | no example sentences, no audio per word, no pitch accent |
| Reading | — | nothing bridges matching → actual reading |
| Progression | mode menu + hi-scores | no onboarding path, no long-horizon goal, no "what do I do today?" |
| Recall strength | recognition-only matching | no typed/recall modes; recognition alone overstates knowledge |
| Retention hooks | hi-score per mode | no streak/quests/heatmap; no coverage meter |
| Distractors | random co-sampled words (collision-avoidant) | distractors avoid confusability instead of exploiting it — backwards for learning |

## 3. Design principles (non-negotiables from the research)

1. **Review debt must be structurally impossible.** The #1 quit trigger
   across every community source is returning to a 400-review pile. KanjiRe
   never shows an overdue count; a comeback session is a normal-sized round
   of the most at-risk words, and everything else silently reschedules
   (FSRS makes this principled — it just recomputes recall probability).
2. **Recognition must be hardened toward recall.** Matching is a
   recognition task; make distractors adaptively confusable (same reading,
   shared kanji, same phonetic series, same semantic field) and add true
   recall modes. A word is never "known" on recognition evidence alone.
3. **Frequency-first, kanji inside words.** No RTK-style kanji-only track.
   Kanji get introduced by the first word that needs them, with component +
   phonetic-series info surfaced on the spot.
4. **Progress = honest coverage.** The flagship metric is "you can now
   recognize X% of [domain] text", computed from per-word stats against
   frequency lists — a true statement, not XP.
5. **Short rounds, forgiving streaks.** 3–5 minute self-contained rounds;
   streaks with earned freezes and repair; daily quest completable in 1–2
   rounds; never zero out visible progress.
6. **The game is the identity.** Every new system must land as a game
   mechanic (bounties, bosses, combos), not a chore list. Gamify the meta
   (collection, progression), never the grading.
7. **Skippable everything.** Placement/"I already know this" must exist at
   every level — lock-step pacing is WaniKani's most-hated trait.

## 4. The plan, tiered

Effort scale: S (< 1 session), M (1–3 sessions), L (multi-session feature),
XL (flagship, phased itself).

### Tier 0 — Quick wins (polish the game we have)

| # | Item | Effort | Notes |
|---|---|---|---|
| 0.1 | **Session summary upgrade**: end-of-round Results gains "words you struggled with" (from this session's confusions) and a one-click "practice these now" rematch round | S | engine already returns everything needed |
| 0.2 | **In-session answer streak counter** with escalating combo SFX (use the orphaned `match_hi` / `round_clear` sounds, and `SFX.chord` for round clears) | S | community loves micro-streaks (WK Answer Streak script) |
| 0.3 | **Smart-ish distractors v0**: bias `weighted_sample_words` co-sampling toward same-JLPT-level + shared-kanji words instead of pure avoidance | M | groundwork for 1.2 |
| 0.4 | **Stats heatmap**: GitHub-style daily-activity calendar on the Stats Overview (sessions/matches per day) | M | WK's most-installed userscript; needs a tiny `sessions` log table |
| 0.5 | **Word detail popup**: click any row in Stats→Words (or long-press a card post-match) → panel with big kanji, reading, meanings EN/FR, per-face mistake bars, deck/level | M | reuses Panel widget |
| 0.6 | Housekeeping from the audit: commit README screenshots (stop ignoring 2 shots), add `comtypes` to requirements, prune dead pyttsx3 hidden-import, stale spec, `setup_data` docstring | S | credibility for a public repo |

### Tier 1 — The spine: scheduling, sessions, habit (v0.3–0.4)

| # | Item | Effort | Notes |
|---|---|---|---|
| 1.1 | **FSRS scheduler** (`kanjire/srs/`): vendor or depend on `py-fsrs`; new `review_log` table (word key, timestamp, rating, mode) + per-word FSRS state (difficulty, stability, due). Engine recorder events map to ratings: clean fast match → Good/Easy, match-after-error → Hard, confused → Again. One user knob: desired retention (default 0.90). Ship default parameters; optimize later from the log. | L | benchmark-proven ~20–30% fewer reviews vs SM-2; graceful with irregular play |
| 1.2 | **Adaptive confusable distractors**: when dealing a board, co-sample the target's *confusion neighbors* — same reading (homophone), shared kanji, same phonetic series (Tier 2 data), previously-confused pairs from stats. Makes recognition "recall-like" (the science) and generates the "ooh, tricky" game feel. | M | sampler protocol already supports injection |
| 1.3 | **"Today" session / daily quest**: menu gains a big default button — *Today's Training*: 1–3 rounds mixing due reviews (FSRS) + a capped trickle of new words (cap computed from projected future load, not a fixed number). Completing it stamps the day. | L | this becomes the app's default entry point |
| 1.4 | **Streak with mercy**: daily-quest streak, earned streak freezes (1 per 7 days, banked), "repair yesterday" by doing a catch-up round. Framed as milestones ("Day 40"), never as loss. | M | Duolingo's power without its resentment |
| 1.5 | **Comeback mode**: after ≥ 4 days away, the app greets with "welcome back — your 20 most at-risk words" (lowest predicted recall), silently reschedules the rest, no backlog number anywhere. | S | falls out of 1.1's model |
| 1.6 | **Typed-recall round type**: prompt = kanji card, answer = type the reading in kana (romaji→kana converter, no IME needed — table exists in `kana.py`). Optional per-mode toggle; FSRS weights these grades higher. | L | the single biggest honesty upgrade to "known" |

### Tier 2 — Knowledge model & content depth (v0.5–0.6)

| # | Item | Effort | Notes |
|---|---|---|---|
| 2.1 | **Kanji decomposition data**: ingest KRADFILE/KanjiVG components + a curated keisei phonetic-series table into a new sidecar DB (build-time script, like `glosses.db`). Word detail (0.5) gains "built from: 青(blue)+日 · phonetic family 青→晴清精 (sei)". | L | the community's most-loved missing feature (Keisei userscript) |
| 2.2 | **Kanji families as gameplay**: a board variant where one group = a phonetic family (match kanji sharing a component + their shared on-reading); "family screens" in Stats→Kanji showing series mastery. | M | teaches the guess-unseen-kanji superpower |
| 2.3 | **Example sentences**: ingest Tatoeba (CC-BY) JA-EN/FR pairs at build time; each word stores its best short i+1-ish sentence. Shown on the word detail card and after matches (toast line under the board). New round type: *cloze match* — sentence with a gap is the prompt card. | L | "no context" is complaint #3 community-wide |
| 2.4 | **Pitch accent + audio polish**: Kanjium pitch data on the detail card (notation only, never quizzed); TTS already speaks on match — add per-word replay button on detail. | M | passive-from-day-one is the consensus |
| 2.5 | **Coverage meter (flagship metric)**: build-time general frequency list (BCCWJ-derived) + per-corpus lists (we ingest corpora already!) → Stats Overview and menu footer show "You can recognize ~X% of [general / your imported corpus] text", with next milestone ("214 words to 80%"). | L | jpdb's most-praised mechanic; our stats make it honest |
| 2.6 | **Placement / mark-as-known**: onboarding quiz (fast self-paced matching sweep by JLPT band) + bulk "I know these" in Stats to seed FSRS state. | M | kills the WaniKani "re-drill 500 known kanji" complaint |

### Tier 3 — The bridge to reading (v0.7–0.9)

| # | Item | Effort | Notes |
|---|---|---|---|
| 3.1 | **Journey mode** (new default progression): a level map (stations along a road — nod to Wagotabi's prefectures) over the frequency-ordered word list. Each station = a themed batch of ~20 words; clearing it = matching rounds + one typed-recall round; **boss round** = confusable-heavy board mixing the station's words with everything learned. Stations unlock forward but are always skippable (placement). Existing modes remain as the "arcade" wing. | XL | turns the menu-of-modes into a game with a destination |
| 3.2 | **Reading room v1 — sentence feed**: tap-through screen of real sentences (Tatoeba, later tadoku Level-0 stories) filtered to ≥ 95% known-token density by our stats. **Adaptive furigana**: readings shown only over words below mastery threshold, tap-to-reveal otherwise; a reveal logs an FSRS "Again"-lite event; a tap-to-save adds unknown words to the learning queue. Tracks characters read as a first-class stat. | XL | Satori Reader's signature, driven by data we already have; this is the moment the app becomes "reading practice," not "reading prep" |
| 3.3 | **Reading room v2 — your corpora**: point it at an imported corpus (the ingest pipeline keeps raw text) → read *that* text with adaptive furigana and pre-study decks ("study the 40 unknown words in chapter 1 first"). | L | jpdb's pre-study-your-media loop, fully local |
| 3.4 | **Listening rounds**: audio-prompt board (TTS speaks the word; match its kanji+meaning). Optional minimal-pair pitch mini-game later. | M | cheapest listening reps available |
| 3.5 | **Leech bounty hunts**: surface FSRS-identified leeches (repeated lapses) as a special Survival variant — "Wanted" board with bigger bounties; after N failures offer "retire this word" (blacklist) instead of infinite grind. | M | Bunpro ghosts + Anki leech-suspend, as a game mode |

### Tier 4 — Big bets (1.0+)

- **Handwriting**: KanjiVG stroke data + mouse/tablet tracing rounds
  (Ringotan-style scaffold: trace → hinted → memory). Desktop mouse input is
  the risk; prototype first. (L/XL)
- **Grammar-lite**: not a grammar course — but Journey stations could
  introduce the ~80 N5 particles/patterns as cloze-match rounds so Reading
  Room sentences stay parseable. (XL, decide after 3.2 data)
- **Steam release**: Wagotabi/Yomifuda prove the market; the updater,
  packaging, and game feel are already Steam-shaped. Requires art/audio
  polish pass + achievements. (XL)
- **Shared progress / sync**: export/import of stats+FSRS state first
  (file-based); real sync only if there's demand. (M then XL)

## 5. Data sources to ingest (all open)

| Data | Source | License | Feeds |
|---|---|---|---|
| Components/radicals | KRADFILE / KanjiVG | CC-BY-SA / EDRDG | 2.1, 2.2, handwriting |
| Phonetic series | keisei datasets (e.g. WK Keisei script data, The Kanji Code lists) | verify per-source | 2.1, 2.2, 1.2 |
| Example sentences | Tatoeba JA↔EN/FR | CC-BY 2.0 FR | 2.3, 3.2 |
| Pitch accent | Kanjium accent DB | free | 2.4 |
| Word frequency | BCCWJ short-unit lists; existing wordfreq | check terms / MIT | 2.5, 3.1 ordering |
| Kanji frequency | scriptin/kanji-frequency | CC-BY | stats, coverage |
| Graded stories | tadoku.org free readers | CC | 3.2 content |
| FSRS | py-fsrs | MIT | 1.1 |

All ingested at build time into sidecar DBs (the `glosses.db` pattern), so
the runtime app stays NLP-free and the release stays a lean play-only bundle.

## 6. Architecture fit (where each piece lands)

- `kanjire/srs/` — new: FSRS state machine + review-log recorder wrapping
  the existing `StatsRecorder` (both stay in user-dir `stats.db`; schema
  additive, no migration of existing rows needed — seed FSRS state lazily
  from legacy streak/score on first review).
- `model/sampling.py` — new `due_sample_words` (reviews) and
  `confusable_cosample` (distractors); the engine's `sampler`/`meta_provider`
  injection points already support this without engine changes.
- `game/engine.py` — mostly untouched; new round types (typed recall, cloze,
  audio prompt) are new *scenes* reusing engine scoring, or small
  `GameConfig` extensions (`prompt_mode`).
- `data/` — new sidecar builders in `scripts/` (components, sentences,
  pitch, frequency lists) mirroring `fetch_jmdict_multilang.py`.
- `ui/` — new scenes: `journey.py` (map), `reading.py`, `today.py` (or menu
  integration); Stats gains heatmap + coverage tiles. Theme/scaling/widget
  systems are ready as-is.
- The updater means each tier ships to friends the day it's verified.

## 7. Anti-features (deliberately not doing)

- No visible overdue/backlog counts, ever. No punishment for absence.
- No forced mnemonics (offer editable user notes instead, eventually).
- No lock-step levels without skip/placement.
- No RTK-style months-of-kanji-before-words track.
- No engagement-metric gamification detached from learning (leagues, gems);
  collection/meta rewards stay tied to real recall events.
- No always-on furigana in reading mode (crutch effect) — adaptive only.

## 8. Suggested order of attack

1. **v0.3 "The Spine"** — Tier 0 (all) + FSRS (1.1) + Today session (1.3)
   + comeback mode (1.5). *The app becomes a daily habit.*
2. **v0.4 "Honest Knowledge"** — typed recall (1.6), confusable distractors
   (1.2), streak+quests (1.4), placement (2.6).
3. **v0.5 "Depth"** — kanji components + families (2.1, 2.2), sentences
   (2.3), pitch/audio (2.4).
4. **v0.6 "The Meter"** — coverage meter (2.5), heatmap polish, stats
   upgrades.
5. **v0.7–0.9 "The Bridge"** — Journey mode (3.1), Reading Room v1→v2
   (3.2, 3.3), listening (3.4), leech hunts (3.5).
6. **1.0** — polish pass, then evaluate Tier 4 (Steam being the most
   exciting candidate).

Each step is independently shippable through the auto-updater, and each
makes the app strictly better for the friends already playing it.

## 9. Key research references

- FSRS: github.com/open-spaced-repetition (py-fsrs, benchmark: ~727M
  reviews, beats SM-2 for ~99.6% of users; 20–30% fewer reviews at equal
  retention).
- Coverage: Nation 2006 / Matsushita (JA: ~9.5k lexemes for 95%, ~20k for
  98% written coverage; ~1,000 kanji ≈ 90–95% of characters); 95–98%
  known-token density gates comprehensible reading.
- Kanji: keisei phonetic-semantic compounds ≈ 2/3+ of jōyō kanji; community
  consensus = kanji-in-vocab, components as support (TheMoeWay, Refold,
  WK Keisei userscript).
- Community: review debt = #1 quit cause (WK forums); most-wished features =
  workload control, context sentences, frequency/media decks, phonetic
  series, heatmaps (userscripts as de-facto wishlist).
- Motivation: goal-gradient + endowed progress; streaks need freezes/repair;
  3–10 min sessions beat long ones; the strongest retention factor is the
  app measurably paying off in real reading.
- Market: Wagotabi (98% positive), Yomifuda, Kanji Combat prove
  kanji-as-game demand; jpdb/Satori prove coverage stats + adaptive furigana
  are the beloved bridge mechanics.

Full sourced reports live in the session research (big-app feature analysis,
community wishlist mining, learning-science review); this file is the
distilled plan.
