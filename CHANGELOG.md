# Changelog

All notable changes to KanjiRe. Newest first. Versions follow
`MAJOR.MINOR.PATCH`:

* **PATCH** (0.1.0 → 0.1.1) — bug fixes, copy tweaks, small polish.
* **MINOR** (0.1.x → 0.2.0) — new features, modes, or notable UX changes.
* **MAJOR** (0.x → 1.0.0) — a big milestone (bumped deliberately, not automatically).

Notes under the current version are what friends see in the in-app "update
ready" banner, so write them for players, not for the commit log.

## [Unreleased]

## 0.8.0 — 2026-07-12
- New **ROMAJI ON KANA CARDS** toggle (Advanced tab): kana cards — reading
  cards and both faces in Kana mode — show their romaji pronunciation in
  small type along the bottom edge. Great while you're still getting
  comfortable with the kana; saved per mode and in presets like every
  other toggle.

## 0.7.0 — 2026-07-12
- **Read your own texts!** The Reading Room now has a source picker: pick
  any text you've imported and read it sentence by sentence at your level,
  with the same tap-for-reading chips. New imports capture their sentences
  automatically, and the built-in Wikipedia deck comes pre-loaded with
  2,000+ of them.

## 0.6.0 — 2026-07-12
- **The Journey** — a new tab: the whole JLPT vocabulary becomes a road of
  456 stations ordered by real-world frequency. Clear stations by learning
  their words (however you like — nothing is locked), and face a **boss
  fight** (鬼) every fifth station: hearts on, your hardest recent words.
- **The Reading Room** — the other new tab, and the whole point: real
  Japanese sentences chosen so you know every word (or all but one).
  Tap any word for its reading, pitch and meaning; tap **+ learn** to queue
  a new one; T shows the translation. Your sentences-and-characters-read
  counter is the stat that actually matters.
- Today's typed-recall round now alternates with **listening prompts**:
  hear the word, type what you heard (F1 replays).
- **Wanted posters**: words you keep missing become a leech hunt on the
  Stats page — a hearts-on session over just your problem words.

## 0.5.0 — 2026-07-12
- **Kanji anatomy**: click a word in Stats and see what its kanji are built
  from, its **pitch accent**, and its **sound family** — 晴 borrows 青's
  せい, just like 清・精・請. Learn one component, unlock a whole family.
- **Example sentences everywhere**: matching a word now flashes a real
  sentence using it (with translation), and the word detail card shows one
  too. 62,000 sentences ride along, fully offline.
- **Coverage meter**: Stats now shows the honest number — how much everyday
  vocabulary you can recognize, weighted by how often words actually occur,
  with your next milestone ("34 words to 15%"). Imported texts get their
  own exact meter.
- **"I already know this"**: new N5-N1 buttons in Stats seed whole levels
  as known, so experienced learners skip the beginner grind. Seeded words
  drift back as occasional reviews instead of flooding your queue.
- Boards now also pair kanji from the **same sound family**, so the pattern
  jumps out at you while you play.

## 0.4.0 — 2026-07-12
- **Type the reading!** Today's Training now ends with a short typed-recall
  round: the kanji appears, you type the reading — romaji converts to kana
  live as you type, no Japanese keyboard needed. Typing a word correctly
  counts much more strongly toward mastering it than matching cards.
- Boards now deliberately **re-pair words you've confused before** so old
  mix-ups get re-tested and finally retired.

## 0.3.0 — 2026-07-12
- **Today's Training** — a big new button on the menu builds your daily
  session automatically: words due for review (scheduled by a real
  memory model, FSRS) plus a gentle trickle of new words. Finish it to
  grow your **daily streak** — with earned streak freezes, so one missed
  day never wipes your run.
- **Welcome-back sessions**: after a few days away KanjiRe greets you with
  a short refresher of your most at-risk words. No review mountain, ever.
- Boards are now **sneakier**: words that share a kanji (like 食べる and 食事)
  are more likely to appear together, so matching actually tests you.
- After a game, words that tripped you up are listed **in red** and a new
  **"Practice tricky words"** button replays just those in a chill rematch.
- Stats got an **activity heatmap** (your daily play, GitHub-style) and you
  can now **click any word row** for a detailed card: meanings, level,
  score, and exactly which face trips you.
- Better sounds: a brighter chime on hot combos and a little arpeggio when
  you clear the whole board.

## 0.2.0 — 2026-06-02
- KanjiRe now runs on **Linux** too — same game, same one-click auto-updates as
  on Windows. Download once and it keeps itself current; no terminal needed.

## 0.1.0 — 2026-06-02
- First release with the built-in **self-updater**: KanjiRe now checks GitHub
  for new signed builds on launch and offers a one-click "Restart & update".
- Settings now has an **About** panel showing the version with a manual
  "Check for updates" button.
