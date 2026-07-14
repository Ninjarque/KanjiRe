# Changelog

All notable changes to KanjiRe. Newest first. Versions follow
`MAJOR.MINOR.PATCH`:

* **PATCH** (0.1.0 → 0.1.1) — bug fixes, copy tweaks, small polish.
* **MINOR** (0.1.x → 0.2.0) — new features, modes, or notable UX changes.
* **MAJOR** (0.x → 1.0.0) — a big milestone (bumped deliberately, not automatically).

Notes under the current version are what friends see in the in-app "update
ready" banner, so write them for players, not for the commit log.

## [Unreleased]
- **Friends!** Add the people you play with straight from the room (a **+ add**
  button next to their name), see who's online and who's sitting in a room, and
  play together in one click: **invite** a friend while you're hosting, or **ask
  to join** theirs when they're the one with the room open. Invites reach you
  anywhere in the app, not just on the multiplayer screen — accept and you land
  straight in their room, no code to read out loud. Remove a friend any time.
  (Nothing is announced until you've actually played online once; a friend who
  quits or crashes stops showing as online within seconds.)
- **The multiplayer room settings are full-size now** — they were built at about
  two-thirds the size of the equivalent menu rows, so they read like a shrunken
  afterthought however big your window was.
- Fixed text being clipped inside the name / room-code / search boxes at larger
  window sizes.

## 0.16.0 — 2026-07-12
- **Fixed: updating did nothing on Linux.** It downloaded, you clicked restart,
  the app closed and that was that — same version. The helper that swaps the
  folders waited for the app's process to disappear with a loop that could wait
  *forever*, and if the window closed while the process lingered, it did exactly
  that. It now gives the app 30 seconds, insists, and applies the update either
  way (renaming a folder is safe on Linux even if the old process is still up).
- **The update banner now shows on every tab**, not just Play — sitting in Stats
  or the Reading Room, you'd never have been told an update was ready.
- **Multiplayer: hover a card for a second and everyone sees it light up.** The
  player whose turn it is can point at what they're considering, so the others
  can follow their thinking instead of watching a still board. It's yours only
  on your turn, and it never lingers into someone else's.

## 0.15.0 — 2026-07-12
- **Multiplayer: a completed group now stays up for two seconds.** It used to be
  scored and swept off the board the instant the last card was clicked, so the
  other players never got to see which cards went together — the whole point of
  watching someone else's turn. The group lights up and holds, the board is
  frozen for everyone (nobody can click through it), and it clears and passes
  the turn at the same moment on every screen.

## 0.14.0 — 2026-07-12
- **The updater actually updates now.** On Windows it downloaded the new
  version, said "ready", and then quietly relaunched the *old* one: the helper
  that swaps the folders waited for the app to exit using a command that can't
  run without a console window, so it waited forever. It now waits for the
  files to be free instead, applies the update, restarts you into the new
  build — and no longer flashes a black console window while it does.
- **When an update check finds nothing, it now says why** (in `update.log` next
  to your save file) instead of silently pretending you're up to date, and it
  falls back to a bundled certificate store on Linux distros that don't ship a
  usable one.
- **Romaji is on by default everywhere**, including the Journey and boss fights.
  Turn it off in one click on the Advanced tab if you don't want it.
- **Multiplayer: writing direction and fonts** join the room settings, matching
  the single-player Advanced tab — and everyone's board looks identical, which
  a naive random roll would have broken.
- **Multiplayer: players who vanish are dropped.** Close the app, lose wifi, or
  pull the plug and the room now notices within 15 seconds and moves on — the
  game used to sit forever on the turn of someone who was never coming back.
  Their unplayed turns leave with them instead of being handed to whoever's left.
- **Play again** on the multiplayer results screen: same players, same settings,
  fresh words. The final scores are much bigger and easier to read, too.
- The update banner no longer covers the Multiplayer and Save-as-preset buttons.

## 0.13.0 — 2026-07-12
- **Fixed the missing characters on Linux.** 漢字 in the title, the streak
  icons and several buttons showed up as empty boxes. Two causes: the bundled
  Japanese fonts were being thrown away at startup, and **bold** text fell back
  to a font with no Japanese in it at all. Both fixed — Linux now renders the
  same as Windows.
- **Fixed the search box** (Stats → Words / Kanji / History): pressing Enter
  used to type a stray character into it, results didn't appear until you
  resized the window, and the text didn't scale with the rest of the UI.
- **Multiplayer: cards per word now offers 2, 3 or 4** with the same labels as
  the single-player Advanced tab, including the romaji card.
- **You can finally see what the host picked.** Guests' settings buttons are
  read-only, but the selected option is now clearly highlighted instead of
  every button looking identically greyed out.

## 0.12.0 — 2026-07-12
- **Multiplayer rooms now have full game settings** — deck, JLPT levels,
  words per round, cards per word (including the romaji card) and turns
  each. The host sets them in the lobby and **everyone watches the choices
  update live**, so you all know what you're about to play.
- **Host can pause** mid-game (nobody can click while paused) and drop back
  to the **room settings** to reconfigure and start a fresh game — same
  players, same code, scores reset.
- Everyone stays on the same page: the update system was audited so every
  version ever released still updates cleanly to the newest build.

## 0.11.1 — 2026-07-12
- **Fixed: the Linux build crashed on startup** on many distros with
  `undefined symbol: g_sort_array` / a GStreamer error. The bundle was
  accidentally shipping the build machine's GLib, which clashed with the
  system one. KanjiRe never needed GStreamer (all its sounds are generated
  in-app), so it's gone — the Linux app now starts cleanly everywhere.

## 0.11.0 — 2026-07-12
- **Multiplayer now needs nothing but a room code.** No IP addresses, no
  port forwarding, no router settings: create a room, read the 5 letters to
  your friend, they type them in and hit Join — anywhere in the world.
  (Under the hood everyone connects *out* to a public relay, which is
  exactly what home routers allow by default. A direct server address is
  still there as an optional advanced field for LAN/self-hosting.)

## 0.10.0 — 2026-07-12
- **MULTIPLAYER!** New ⚡ button on the menu: host a room (or join with a
  4-letter code) and race your friends on a **shared board** — everyone
  sees the same cards, you play one turn each, matched groups vanish and
  refill for everyone, and combos build your score across your turns.
  Pick 5/10/15 turns per player; highest score wins. Hosting runs the tiny
  server inside the app (forward port 24857 to play over the internet), or
  run `scripts/run_server.py` on any machine with Python.

## 0.9.0 — 2026-07-12
- **Romaji cards!** The CARDS PER WORD option now has a third choice:
  "+ Romaji (4 cards)" adds a yellow *abc* card with the word's reading in
  romaji, so each group is kanji + kana + romaji + meaning. (This replaces
  the small romaji hint under kana cards.)
- **Game history**: a new History tab in Stats lists your recent games —
  date, mode, score, matches. **Click any row to replay that exact game**;
  right-click to remove it.
- **The whole interface is bigger** — everything scales up ~20% more with
  your window, so it no longer feels small on 1080p+ screens, and the
  Journey map now shows as many stations as your window fits.
- Layout polish everywhere: long meanings can't spill out of dense boards
  anymore, and every screen was re-checked at four window sizes.

## 0.8.1 — 2026-07-12
- Fixed text overlapping in several places: KanjiRe is now **DPI-aware** on
  Windows, so with display scaling (125-175%) the window uses its real size —
  layouts stop squeezing into a smaller virtual one and all text renders
  noticeably **crisper**. The UI also keeps shrinking properly on genuinely
  small windows, the Learn options are more compact, and vertical kana no
  longer runs into the new romaji hints.

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
