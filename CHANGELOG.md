# Changelog

All notable changes to KanjiRe. Newest first. Versions follow
`MAJOR.MINOR.PATCH`:

* **PATCH** (0.1.0 → 0.1.1) — bug fixes, copy tweaks, small polish.
* **MINOR** (0.1.x → 0.2.0) — new features, modes, or notable UX changes.
* **MAJOR** (0.x → 1.0.0) — a big milestone (bumped deliberately, not automatically).

Notes under the current version are what friends see in the in-app "update
ready" banner, so write them for players, not for the commit log.

## [Unreleased]

## 0.32.0 — 2026-07-29

- **Games with genres.** Every word in the dictionary has been sorted into
  40 named topics — Food & Drink, Weather & Sky, The Body, Travel, Set
  Phrases — and the Journey tab has a new **Genres** view to explore them:
  one tile per topic, coloured by how much of it you know, opening onto its
  five JLPT levels so you can work through *food at N5* and watch it fill
  up, exactly like the stations on the road.
- **Boards that group themselves.** Three new dials — Meaning, Looks and
  Sound — decide what the words on a board have in common. Turn up *Looks*
  and you get 待 持 侍 時 on one board, so you finally learn to tell them
  apart; turn up *Sound* and you get 病院 入院 工員; turn up *Meaning* and
  the whole board is one topic. They stack, and they work in multiplayer
  too — your friend doesn't even need to have updated for you to host a
  themed room.
- **A shorter mode list.** The Play tab now leads with the three modes you
  actually start from — Time Attack, Survival, Learn — plus a **＋** that
  turns whatever you've set up into a custom mode of your own (with a red
  delete button when you want it gone). Zen, Recall and Familiarize are
  still there, one row down, as ready-made modes.
- The Journey map is five wide on every device now, so the 鬼 boss stations
  line up in a column instead of wandering across the grid.

## 0.31.1 — 2026-07-19

- **The loading ring spins for real now** (third time's the charm —
  measured, this time). Profiling found two heavyweights still sitting
  on the interface thread: card text sizing re-rendered each card's
  text up to twenty times per card (now one or two, with repeated fits
  remembered), and dealing a board wrote each word's "seen" stat to
  disk with a full sync — ~20ms each, six per deal. Launches dropped
  from a single long freeze to a handful of light frames, boards are
  built a few cards per frame behind the deal animation, and — bonus —
  every match mid-game also lost that ~20ms write hiccup, on the
  computer too.

## 0.31.0 — 2026-07-18

- **Browse the whole dictionary.** Stats grew a Dictionary view (both
  platforms): every word in every deck — not just the ones you've
  played — searchable by kanji, reading or meaning, sorted N5-first,
  with each word coloured by how well you know it. On the computer,
  clicking an entry opens the full word card (components, phonetic
  family, pitch accent, example sentence); on the phone, tapping a word
  speaks it. Words that exist in several decks show once.
- **The loading ring now actually spins.** Game assembly moved off the
  interface thread, so launching a board animates smoothly instead of
  freezing mid-spin — and launches got a bit faster too (the kanji
  sound-family table is now computed once instead of on every launch).

## 0.30.0 — 2026-07-18

- **Launching a game now shows a loading spinner.** Tapping PLAY (or a
  Journey station, Today's Training, a history replay, a Recall drill)
  answers instantly with a spinning 漢 ring while the board is prepared,
  instead of the menu freezing for a beat. Extra taps during the load
  are swallowed, so you can't accidentally double-launch.

## 0.29.2 — 2026-07-18

- **The unclickable buttons are fixed — for real, with proof.** Thanks
  to the touch-marker screenshots, the culprit turned out to be OUR
  hidden overlays: the invisible invite popup, the after-match sentence
  strip, and the collapsed tab bar all kept sitting silently over parts
  of the screen, and hidden-but-disabled things still swallow taps.
  Exactly the bands you found dead: the Multiplayer button, the
  Translation/Next buttons in the Reading Room, and the bottom row of
  cards mid-game. All overlays are now "ghosts" while hidden — taps
  pass straight through — and the test suite now fires real
  screen-level taps at those exact spots so this whole class of bug
  can't come back.

## 0.29.1 — 2026-07-18

- **Android touch fix, the real one this time.** The app no longer uses
  the keyboard "pan" mode that shifted the whole picture away from where
  taps actually land whenever Android misreported the keyboard height
  (a known quirk, worst near the bottom of the screen and on foldables).
  That was the true cause of buttons only working near their edges — and
  of the Multiplayer button dying entirely. Fullscreen is also back OFF:
  your system bars are yours again, and hiding them is now a choice —
  Settings → Display → Fullscreen.
- **New: Settings → Display → "Touch marker (debug)".** Turn it on and a
  small gold ring appears exactly where the game believes you touched.
  If taps ever feel off again, flip it on and send us a screenshot with
  your finger on a button — the ring (and the numbers above it) tell us
  precisely what's wrong.
- Dialogs that ask you to type (like the pairing code) now sit near the
  top of the screen, so the keyboard can't cover them.

## 0.29.0 — 2026-07-18

- **Multiplayer has passes now.** A new ×1/×2/×3/×5 row in the lobby:
  with more than one pass, the same words play that many times before
  fresh ones appear. A cleared group leaves its spots EMPTY — the gaps
  keep shuffling with the cards instead of new words filling in — and
  once the board is cleared the same words come back re-shuffled for the
  next pass. The turn bar shows "Pass 1/2". Great for locking words in
  together. (Everyone needs this version to play together — the update
  banner will say so.)
- Fixed the multiplayer lobby's FONTS row on Android showing the writing
  labels ("Horiz. / Mix") instead of Single / Random.

## 0.28.0 — 2026-07-18

- **Android: buttons and cards now respond wherever you tap.** Taps used
  to land offset from what you saw (a known Android quirk when the status
  bar is visible), so buttons only worked near their edges — worst at the
  bottom of the screen and on foldables. The game now runs true
  fullscreen, which removes the offset entirely.
- **Simpler modes.** There are now four real modes — Time Attack,
  Survival, Zen, Recall — and the old Familiarize and Learn live on as
  one-tap presets right beside them (gold buttons), since they were
  really just configurations. Every setting they carried (passes, fonts,
  vertical writing, and the known/less-known/unknown word mix) is now
  visible and adjustable in EVERY mode, on desktop and Android alike.
- **Mix your dictionaries.** The deck row is now toggles, like the card
  faces: turn on JLPT and Wikipedia (and any deck you imported) together
  and the board draws from all of them at once. The JLPT level chips
  keep applying to the JLPT part of the mix. Kana stays a solo pick —
  it generates syllable drills rather than drawing from a word list.
- **Hiragana / Katakana are toggles too.** In the Kana deck, pick
  Hiragana, Katakana, or turn both on to match ひらがな ↔ カタカナ
  across scripts (the old "Both").
- **The Kana deck's script choice actually works now.** Hiragana-only and
  Katakana-only always quietly fell back to the mixed deck — pick
  Hiragana, Katakana, or Both and that's genuinely what you'll drill.
  On Android the kana options (length + script) now appear too, and the
  deck list shows clean names ("Wikipedia", not "corpus:wikipedia").

## 0.27.0 — 2026-07-17

- **Pick your card faces directly.** "Cards per word" is now four toggle
  buttons — 漢字 Kanji, かな Kana, abc Romaji, Meaning — each in the same
  colour as its cards on the board. Turn any combination on or off (at
  least two), in the solo menu and the multiplayer lobby alike. New
  combos like kana ↔ meaning without kanji are now possible.
- **The Journey tab no longer freezes when scrolling fast** — the road's
  ~540 station buttons are now drawn through a recycled list that keeps
  only a screenful alive at once.

## 0.26.1 — 2026-07-17

- Fixed overlapping buttons on the Recall screens: the typing box hid
  behind the Start button on the study list (and behind the answer
  choices), and big drills now shrink their study list to fit any window.
- Phone landscape: the Recall header (back button, progress) no longer
  gets pushed off the top of the screen.
- Multiplayer: a friend's name and status no longer run into the Invite
  button in narrow windows.

## 0.26.0 — 2026-07-17

- **Recall got a big upgrade** (thanks to a friend's suggestions!):
  - Words with several correct readings (like 何 = なん or なに) now accept
    any of them — a handful were literally impossible to answer before.
  - **Study first**: the drill shows its words once — kanji, every
    reading, meaning — before quizzing you (on by default, toggleable).
  - New prompt style, **Pick the reading**: choose among lookalike
    options instead of typing — great for telling similar readings apart.
    Tap on the phone, click or press 1–4 on the computer.
- Fixed: the "Read + hear" prompt on the computer behaved like "Mixed".

## 0.25.1 — 2026-07-17

- The pairing code now wraps onto its own line on phone screens — it was
  cut off after the first characters at narrow widths.
- The desktop Settings page scrolls (mouse wheel) — the Device sync
  section was pushed below the bottom edge on smaller windows.

## 0.25.0 — 2026-07-17

- **Your progress now follows you across devices.** Link your phone and
  computer once (Settings → Device sync: one shows a short code, the other
  types it) and your words, streak, reviews, reading history, high scores
  and saved presets merge automatically whenever your devices are online —
  in any order, never losing or double-counting anything. No account, no
  password, no server: your devices talk to each other directly, encrypted
  end-to-end, free forever. Link as many devices as you like.

## 0.24.0 — 2026-07-17

- **KanjiRe is now on Android!** The whole game on your phone: every mode
  (Time Attack, Survival with hearts, Zen, Familiarize, Learn, Recall),
  Today's Training with your streak, the Journey road, the Reading Room
  with its difficulty dials, your stats and history — all sharing the same
  data and rules as the desktop version.
- **Cross-platform multiplayer.** Phone and desktop players meet in the
  same rooms with the same 5-letter codes. On touch, hold a card to point
  at it for everyone (the hover equivalent).
- **Friends work everywhere.** Requests, presence and play-together
  invites reach you on the phone, and answering an invite drops you
  straight into the room.
- **The phone updates itself too.** The app checks the same signed
  releases; a new version downloads and Android installs it — your
  progress carries over.
- Built for the Fold: the layout re-flows live when you open or close the
  phone, mid-game included.
- Recall: the answer box now sits right under the kanji and kana preview —
  never hidden behind the keyboard — and each word's audio always plays to
  the end (plus a beat) before the next one appears.
- After a match you can now show the example sentence big in the middle of
  the screen (Settings → "Sentence after a match": Off / Default / Big) —
  works in multiplayer too, everyone sees the same sentence.
- The phone's back button behaves everywhere: closes the keyboard first,
  backs out of a game to the menu, and asks before closing the app
  (with an "always close" option).

## 0.23.0 — 2026-07-16
- **More reading examples for under-covered words.** Added ~900 real,
  human-translated sentences (from Tatoeba, same free licence as before), chosen
  specifically for words that had fewer than three examples — so the Reading
  Room has more to draw on where it was thinnest.

## 0.22.0 — 2026-07-16
- **The Reading Room now has difficulty controls.** Two dials at the top: how
  many new words a sentence may have (known-only / +1 / +2), and how hard it
  should be (Easy / Comfortable / Challenging). Every sentence is rated by the
  JLPT level and frequency of its words — its overall level and its single
  hardest word — and the room serves them ordered to fit what you pick, with the
  level shown under each sentence.

## 0.21.0 — 2026-07-16
- **Reading Room: fixed sentences being wrongly marked "you know every word".**
  A sentence full of names or loanwords (like 竹内力とバンバンバザール) was judged
  only on the one common word the dictionary recognised, so it claimed you knew
  the whole thing. It now counts the kanji actually on screen — a name or word
  the dictionary doesn't cover no longer hides behind a word you do know.
- **Crashes are now written to a file** (`crash.log` next to your save data), so
  if the app ever dies you can share that instead of having to relaunch from a
  terminal to catch the error.

## 0.20.0 — 2026-07-16
- **Fixed a Linux crash** when clicking "mark a JLPT level as known", deleting a
  preset, or naming a saved preset. Those used a desktop dialog toolkit that
  isn't part of the packaged app, so on Linux the app crashed the moment you
  clicked one. They're now in-app dialogs that work everywhere and match the
  rest of the UI.

## 0.19.0 — 2026-07-15
- **New mode: Recall.** The type-the-reading drill that used to only appear at
  the end of a Today session is now a mode of its own — pick it, and every
  prompt shows you a word to type the reading of (romaji converts to kana as you
  type, no Japanese keyboard needed). It has the same controls as everything
  else: deck, JLPT levels, how many words, the known/less-known/unknown
  difficulty mix, and a **prompt style** — read-and-type, listen-and-type
  (dictation, if your system speaks Japanese), or a mix of both.

## 0.18.0 — 2026-07-14
- **Friends is now its own tab**, next to Stats — who's online, who's in a room,
  requests waiting on you, and one click to play together from anywhere.
- **Friendship goes both ways now.** Adding someone sends them a *request*: they
  accept (or decline) and only then do you appear on each other's lists. You can
  ask the moment you meet — the **+ add** button is there in the room lobby, not
  just at the end of the game — and a request waits for someone who's offline,
  arriving the next time they open the app.

## 0.17.0 — 2026-07-14
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
