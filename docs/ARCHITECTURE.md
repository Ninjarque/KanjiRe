# KanjiRe — Application Reference

> Verified against the code on 2026-07-12 (v0.2.0, commit `f950f7e`) by a full
> function-level sweep of every module. This is the developer-facing map of
> everything that is genuinely implemented. The player-facing intro lives in
> `README.md`.
>
> **ADDENDUM — v0.3.0 through v0.7.0 (same day, the beginner-to-reader
> pass; see §13 at the end for the delta).** Sections below describe the
> v0.2.0 baseline and remain accurate for what they cover.

KanjiRe is a pyglet (1.5) Japanese kanji matching mini-game inspired by
Wagotabi's "Saeki's Kanji". Each word becomes up to 3 cards — kanji / reading /
meaning — shuffled on a board; the player groups them by clicking. On top of
that core loop sit five game modes, three deck families, a cross-deck knowledge
tracker, an 8-palette theme system, EN/FR localisation, SAPI text-to-speech,
and a cryptographically signed cross-platform self-updater.

---

## 1. Repository layout

```
kanjire/
  __init__.py        __version__ (single source of truth for releases)
  __main__.py        entry point: DB sanity check, then GameApp
  paths.py           read-only data dir vs writable user dir split
  userstate.py       user_state.json (settings, presets, hi-scores)
  i18n.py            EN/FR strings, process-global locale
  jputil.py          dependency-free JP text helpers
  kana.py            synthetic kana deck generator
  model/             Word dataclass + samplers (pure, no DB)
  game/              GameConfig/PRESETS + GameEngine (pure logic, no pyglet)
  data/              SQLite vocab DB, corpus ingestion, stats recorder
  ui/                pyglet shell: app, scenes, widgets, theme, audio
  update/            self-updater: config, verify, checker, applier, controller
  fonts/             6 bundled SIL-OFL Japanese TTFs (+ licenses)
  data/kanjire.db    bundled vocab DB (built offline)
  data/glosses.db    French-gloss sidecar (built offline)
scripts/             data pipeline + build/release tooling
tests/               52 pytest tests + smoke_ui.py + screenshot generators
main.py              6-line launcher (delegates to kanjire.__main__)
```

Layering rule: `game/` and `model/` import no DB and no pyglet; `data/` imports
no pyglet; `ui/` wires everything together.

---

## 2. Game modes (presets)

All in `kanjire/game/config.py::PRESETS` — a dict of factory functions
returning fresh `GameConfig` instances.

| Preset | Key parameters |
|---|---|
| **Time Attack** | 120 s timer, 6 words/round |
| **Survival** | untimed, `lives_mode=True`, 3 hearts (max 5), `heart_chance=0.35`, Learn bucket mix 1/2/3 |
| **Zen** | untimed, 8 words/round, no penalties |
| **Familiarize** | untimed, 5 words × 3 passes, random fonts, random vertical writing |
| **Learn** | untimed, 6 words/round, bucket mix known=1 / less_known=2 / unknown=3 |

Users can save any menu configuration as a **custom preset** (name via tkinter
prompt; right-click its mode button to delete). Presets persist in
`user_state.json` and appear as gold buttons in the MODE row.

### GameConfig fields

Content: `decks`, `levels` (JLPT, empty=any), `faces` (subset of
kanji/reading/meaning), `words_per_round`, `frequency_bias` (0 uniform → 1 true
frequency). Pacing: `duration` (None=untimed), `max_mistakes` (None=unlimited;
note: the game ends on the *(N+1)th* mistake — N mistakes are tolerated).
Scoring: `base_points=100` (× combo), `mismatch_penalty`, `round_bonus=200`.
Familiarize: `repetitions`, `random_fonts`, `vertical_writing`
(off/random/all). Learn: `learn_known/less_known/unknown` (0–3 selector steps,
mapped to weights 0/1/3/6 in `ui/scenes/game.py::_LEARN_STEPS`). Survival:
`lives_mode`, `start_lives`, `max_lives`, `heart_chance`. Kana: `kana_length`
(1–3), `kana_script` (hira/kata/both). `with_(**changes)` returns a modified
copy; `__post_init__` validates/clamps everything.

---

## 3. Decks

- **`jlpt`** — 8 032 words N5–N1 built from open-anki-jlpt-decks CSVs with
  wordfreq zipf frequencies, cleaned/capitalised English meanings, and French
  glosses where JMdict has one (~82 % coverage). Built by
  `scripts/build_jlpt_dataset.py`.
- **`corpus:<slug>`** — any Japanese text ingested via fugashi/MeCab + jamdict
  (`kanjire/data/ingest.py`). Reachable in-game ("+ Import file…" /
  "+ Paste text…", hidden when the NLP stack is absent — e.g. frozen builds)
  or via `scripts/ingest_corpus.py`. `scripts/fetch_sample_corpus.py` seeds a
  `corpus:wikipedia` deck from 8 Wikipedia articles.
- **`kana`** — fully synthetic (`kanjire/kana.py`), injected at the front of
  the deck list by the menu. 102-entry `(romaji, hiragana, katakana)` sound
  table (gojuuon 46, dakuten 18, handakuten 5, yōon 33). `sample(n, length,
  script)` invents n words of 1–3 distinct sounds; expression/reading carry the
  chosen script(s) (`both` = hira on the kanji face, kata on the reading face)
  and meaning is romaji, so the engine works unchanged.

---

## 4. Game engine (`kanjire/game/engine.py`)

Pure logic, fully unit-tested, no pyglet/DB imports.

- **Round loop**: `start()` → `next_round()` samples words, `_deal_board()`
  creates one `Card` per configured face (locale-aware via
  `Word.face_text(face, locale)`), shuffles the board, fires `recorder.saw()`
  per word. `advance()` handles Familiarize sub-passes (re-deal same words)
  before drawing a fresh set. `update(dt)` drives the timer.
- **`select(card_id)` state machine** → `SelectResult` with `kind` in
  `select / deselect / group_complete / mismatch / noop`. Completing a group
  awards `base_points × combo`; clearing a board adds `round_bonus`. A
  mismatch resets combo, applies `mismatch_penalty` (score floored at 0), and
  fires `recorder.confused(target, offending, offending_face)` — the
  **"only the wrong"** semantics: only the in-progress target and the
  wrongly-clicked word get pinged, on the offending card's face dimension.
- **Recency cooldown**: `_recent_rounds` (deque, 3 rounds) is passed to the
  sampler as `penalize` so just-shown words are heavily down-weighted —
  fixes 見る-every-round repetitiveness.
- **Sampler protocol**: `sampler(pool, n, *, bias, rng, penalize)` →
  `list[Word]`. Default wraps `weighted_sample_words`.
- **meta_provider protocol** (Survival): `meta_provider(round_words)` →
  `(is_new: list[bool], bounty_candidate: int | None)`. Injected by
  `ui/scenes/game.py`; with the default provider, lives_mode has no stickers
  or bounties (by design — keeps the engine DB-free and testable).

### Survival mechanics (`lives_mode`)

- **新 sticker** on words never *matched* (`matches == 0` in stats — not
  "never seen", because `saw()` fires every deal). Cleared only by a clean
  match (no error touched that group during the round).
- **Hearts** (`engine.lives`): a mismatch costs a heart only when the
  in-progress target is already learned (新 targets are free). Game over at 0.
- **One bounty per board** on the hardest already-learned word: a **♥** when
  below half hearts and `rng() < heart_chance`, else a **¥ coin**
  (`_COIN_CHANCE = 0.5`) worth `base_points × combo` bonus. Paid only on a
  clean completion. Hearts cap at `max_lives`.
- `SelectResult` carries the UI feedback: `life_delta`, `bonus_points`,
  `bounty_type`, `group_was_new`, `sticker_cleared`, `lives_left`.

---

## 5. Sampling & knowledge model

**`model/sampling.py`**
- `weighted_sample_words` — Efraimidis-Spirakis weighted sampling with weight
  `10^(freq × bias)`; rejects face collisions (duplicate expression, reading,
  or normalised meaning) within a round; `penalize`d keys get an absolute
  floor weight `_RECENT_PENALTY = 0.01` (a floor, not a multiplier — a
  multiplier can't beat an exponential frequency weight).
- `learn_sample_words` — bucket-mix sampler: proportional per-bucket targets,
  **review buckets (`known`, `less_known`) sampled uniformly** (bias forced to
  0 so one very common word can't dominate reviews), fresh `unknown` bucket
  keeps the frequency bias (teach common words first), cross-bucket
  dedup + backfill. Wired up in `GameScene.__init__` using
  `stats.classify_words` over the pool.

**`data/stats.py`** — cross-deck `word_stats` keyed by `(expression, reading)`
in the user-dir `stats.db`; every event commits immediately.
- Events: `saw` (+1 seen per deal), `matched` (+1 matches, +1
  `current_streak`), `confused` (+1 `mistakes_<face>` on both target and
  offending word, resets both streaks).
- `knowledge_score(row)` = `matches / (matches + 1.5·eff_mistakes + 1)` where
  `eff_mistakes = max(0, mistakes − 0.5·current_streak)` — a clean streak
  forgives old mistakes.
- `classify(row)` → `unknown` (never seen) / `known` (streak ≥ 2, or
  zero-mistake with ≥1 match, or score ≥ 0.55) / `less_known` otherwise.
- `hardest_seen(limit)` — seen words sorted hardest-first (ascending
  streak-aware score, then most mistakes); feeds Survival bounty selection.
- Also: `overview()`, `bucket_counts()`, `classify_words()`, `reset_word()`,
  `reset_all()`.

---

## 6. Data layer

**`data/db.py`** — vocab DB (read-only at runtime, WAL when writable).
Tables: `decks(name, kind, description, source, created_at, word_count)`,
`words(id, deck, expression, reading, meaning, meaning_fr, jlpt, freq, pos,
count, has_kanji)` with `UNIQUE(deck, expression, reading)`, and
`kanji(deck, char, count, freq, grade, jlpt, meanings)`. API: `connect`
(read-only URI mode supported), `init_db`, `init_stats_schema`, `upsert_deck /
upsert_word / upsert_kanji` (COALESCE-preserving conflict handling),
`refresh_deck_counts`, `list_decks`, `load_words(decks, levels, require_kanji,
min_freq)` — note `require_kanji=True` by default — and `word_count`.

**`data/ingest.py`** — corpus pipeline: fugashi/UniDic tokenisation keeping
content POS (noun/pronoun/verb/adjective/adjectival-noun/adverb, skipping
proper nouns and numerals) with kanji-containing base forms; per-token reading
hints (katakana → hiragana) disambiguate homographs; jamdict resolves
reading + meaning (KanjiDic2 for per-kanji grade/JLPT/meanings); zipf-like
`freq = log10(count/total) + 9`; best-effort French glosses via the
`glosses.db` sidecar. `analyze(text)` → `CorpusResult`; `write_deck()`
persists it. Heavy NLP imports are lazy so the game runs without them.

**`paths.py`** — the frozen/dev split that keeps user progress safe:
bundled read-only data resolves inside the package (or `sys._MEIPASS` when
frozen); the writable user dir is `%APPDATA%\KanjiRe` (Windows) or
`~/.kanjire` (overridable via `KANJIRE_USER_DIR`; plain dev runs keep state in
`kanjire/data/` for easy inspection). `user_state.json` and `stats.db` live
there, so vocab-DB rebuilds and app updates never wipe progress.

**`jputil.py`** — dependency-free helpers: `is_kanji / has_kanji /
kanji_chars / is_kana / kata_to_hira / is_mostly_japanese` (actually an
"any-Japanese" check) / `capitalize_first` (skips leading punctuation so
`"-- honorific"` → `"-- Honorific"`).

---

## 7. UI layer (pyglet)

**`ui/app.py::GameApp`** — opens vocab DB read-only + separate stats DB,
applies persisted locale and palette *before* the window exists, sizes the
window via `_initial_window_size` (1180×1020 capped to the screen, floor
760×600 — laptop-friendly), paints a flat background with `glClearColor`, runs
a 60 Hz tick, and starts the update check (`updater.maybe_start()`) in
`run()`. Scene switching goes through `set_scene` (exit/enter/resize hooks);
`go_menu/go_game/go_results/go_stats/go_settings/go_import(_pasted)` lazily
import scenes. `apply_palette(name)` persists + rebinds theme + repaints +
rebuilds Settings. `can_ingest` gates the import buttons (needs jamdict DB +
fugashi + jamdict importable). F11 toggles fullscreen globally.

**`ui/theme.py`** — **8 palettes**: Charcoal (default), Midnight, Sumi,
Graphite (dark), Paper (the only light one), High Contrast, Vivid, Monochrome
(faces differ by lightness; DANGER is dark grey so the mismatch flash reads).
Live module globals (`BG/PANEL/TEXT/ACCENT/GOLD/FACE_COLORS/…`) are rebound by
`apply_palette`. Helpers: `lerp/darken/lighten/luminance/is_light`,
`tint(c, amt)` (shifts *away from* the background — the key cross-palette
fix), `readable_on(bg)`. **Gotcha (standing rule):** never use a theme colour
as a def-time parameter default — it freezes the import-time palette. Default
to `None` and resolve `theme.X` in the body.

**`ui/metrics.py`** — `scale_for(w, h) = clamp(min(w/1180, h/1020), 0.80,
1.50)`. Every scene's `on_resize` multiplies fonts/heights/margins by it;
widgets carry a base font + `set_scale(s)`.

**`ui/audio.py`** — `SFX`: 8 synthesized-in-process sounds (select, match,
mismatch, heart, coin, damage — plus unused `match_hi`/`round_clear`), fresh
`Player` per play with pruning. `Speech`: **direct SAPI via comtypes**
(`comtypes.client` imported before pyglet to win the COM apartment race),
`Async | PurgeBeforeSpeak` so each utterance interrupts the last; picks first
JP and first EN voice. Never reintroduce pyttsx3+queue — it dropped
utterances under fast play. `Audio` is the mute-aware facade.

**`ui/fonts.py`** — registers the 6 bundled OFL fonts (DotGothic16, Klee One,
Yuji Boku, Hachi Maru Pop, Reggae One, Zen Maru Gothic) plus available system
JP fonts into `JP_FONTS` (Familiarize random-font pool); `JP_FONT` is the safe
UI default.

**Widgets (`ui/widgets/`)** — `Button` (hover/selected/disabled, theme-safe
via tint/readable_on), `TabBar` (buttons with an active index), `Panel`
(framed surface + optional header), `TextInput` (Caret + incremental layout,
placeholder, focus ring; used by Stats search), `CardView`/`CardText` (neon
card: glow, badge, Survival `set_sticker`, flash/shake/scale animation state;
vertical tategaki = one Label per char; **meaning auto-fit** shrinks the font
in a measure-loop down to 9 pt so unwrappable words never spill). `ui/anim.py`
provides the tween `Animator` (easings incl. back/elastic); `ui/layout.py`
picks the card grid (`choose_grid`/`slot_center`).

### Scenes

- **MenuScene** — top nav (Play | Stats | Settings) + **Quick | Advanced**
  sub-tabs (gold `TabBar`). Quick: MODE (built-ins + gold custom presets),
  DECK (+ Import file / Paste text), JLPT LEVEL *or* KANA length/script,
  WORDS. Advanced: CARDS (3/2 faces), FONTS, WRITING, PASSES, plus Learn
  buckets (Learn mode only) or STARTING HEARTS {2/3/5 → max 4/5/6} + HEART
  BOUNTIES {None/Low/Med/High → 0/.35/.6/.9} (Survival only). Bottom-anchored
  footer: Save preset · PLAY · availability · hi-score. Inactive tab widgets
  are parked off-screen (−4000) so they can't be clicked; `_refresh` is
  tab-gated. Per-mode toggles persist on every change (`last_per_mode`);
  Enter/Space plays. The **update banner** (built/destroyed in `update()`)
  shows "Update X ready" + one-line flattened release notes, with
  Restart/Later — Restart disabled and a red hint shown when the install dir
  isn't writable.
- **GameScene** — wires sampler (kana / learn-buckets / default) +
  Survival `meta_provider` (newness from stats, bounty from the hardest
  learned word among the player's toughest 60) into the engine. HUD: score,
  combo, and a mode-dependent centre — hearts (♥/♡) in Survival, mm:ss + timer
  bar (red under 20 %/10 s) when timed. Card entrance pop-in, match rise/fade,
  mismatch flash/shake, score/combo/±♥ popups, per-event SFX + optional TTS
  (JP reading or EN meaning depending on the card face). ESC → menu, M →
  mute.
- **ResultsScene** — Time's up / Game over title, gold score + "new best"
  (persisted per mode), 6-stat row (rounds, matches, best combo, accuracy,
  mistakes, learned), review grid of up to 18 seen words, Again / Menu.
- **StatsScene** — Overview / Words / Kanji inner tabs. Overview: 4 big-number
  tiles (total/known/struggling/unknown) + per-face mistake bar chart +
  accuracy line. Words & Kanji: sortable columns (click headers, ▲/▼),
  search box, scroll, alternating stripes; **right-click a word row → reset
  its stats** (tkinter confirm). Kanji rows are aggregated in Python from
  word stats.
- **SettingsScene** — AUDIO panel (Mute, Speak on select/match/mismatch —
  defaults off/off/on/on), LANGUAGE (EN/FR — rebuilds the scene),
  THEME (one button per palette, live switch), ABOUT (version, manual
  "Check for updates" ignoring the 4 h throttle, live status line;
  pip/distro installs show "managed by your package manager").
- **ImportTextScene** — background daemon thread runs read → tokenise →
  resolve (with live progress: count + current word) → write deck; auto
  returns to menu ~1.2 s after done. File and paste dialogs are tkinter.

---

## 8. i18n & persistence

**`i18n.py`** — `SUPPORTED = ("en", "fr")`, process-global `set_locale`/`tr`
with English fallback then key fallback. EN and FR define an identical key
set (~150 keys across nav, menu, HUD, results, stats, settings, updater,
import, paste dialog).

**`userstate.py`** — write-through JSON at `user_state.json`:
`high_scores{mode}`, `settings.{muted, tts_on_select, tts_on_match,
tts_on_mismatch, locale, palette, update_last_check, last_mode}`,
`last_per_mode{mode: toggles}`, `presets[]` (saved custom modes). Load/save
swallow IO errors; unknown keys are preserved (no migration/versioning).

---

## 9. Self-updater (`kanjire/update/`)

Channel: GitHub Releases at public repo `ninjarque/KanjiRe`;
`MANIFEST_URL = …/releases/latest/download/latest.json`; auto-check throttled
to 4 h via `settings.update_last_check`.

**Security, in order:** ① Ed25519 signature on the manifest verified against
the baked-in `PUBLIC_KEY_HEX` *before* trusting any URL/hash
(`canonical_payload` = sorted-keys JSON minus the `signature` field — the
single shared signed-bytes definition) → ② SHA-256 of the downloaded archive
vs the signed manifest → ③ zip-slip-safe extraction (`safe_extract` /
`safe_extract_tar` with `filter="data"` and symlink checks). HTTPS-only,
re-checked after redirects. Crypto via PyNaCl.

**Manifest** is multi-platform + back-compat: `platforms: {windows, linux}`
map (each `url/sha256/size`) plus top-level Windows mirror fields so 0.1.x
clients keep updating. `checker.current_platform()` picks the entry; a legacy
manifest with no `platforms` map is treated as Windows-only. Version compare
strips pre-release tags.

**Apply** (`applier.py`): download to staging (`%LOCALAPPDATA%/KanjiRe/updates`)
→ verify → extract to a same-volume sibling `.kanjire-update-new/` (instant
rename swap) → detached swap script: Windows self-deleting `.bat` (waits on
PID via tasklist, rename install → `.old`, move new in, relaunch, delete
backup, **rollback on failure**); Linux/mac `.sh` (`kill -0` wait, `mv`
backup + rollback, `setsid` relaunch). `can_self_update()` refuses read-only
install dirs (e.g. Program Files) — the banner shows the red hint and
disables Restart.

**Controller** — daemon-thread state machine IDLE → CHECKING → DOWNLOADING →
READY (or UP_TO_DATE / ERROR), polled by the menu banner and the Settings
status line. `self_update_capable()` gates everything to frozen bundles (or
`KANJIRE_UPDATE_TEST=1` in dev) — pip installs never self-update.

**Keys**: private key at `~\.kanjire_keys\update_ed25519.hex` (never in the
repo); public key baked into `update/config.py`. Regenerate with
`scripts/gen_update_key.py --force` (then every user needs one fresh manual
download).

---

## 10. Data pipeline & release tooling (`scripts/`)

**Setup** — `python scripts/setup_data.py [--no-corpus]`, 5 steps:
1. `fetch_jamdict_data.py` — installs the jamdict SQLite DB from the PyPI
   sdist (works around the broken `jamdict-data` Windows build).
2. `fetch_fonts.py` — the 6 OFL fonts + licenses from google/fonts.
3. `fetch_jmdict_multilang.py` — builds `glosses.db` (French) from
   scriptin/jmdict-simplified; also exports `open_for_lookup`/`lookup_fr`
   used by the other builders.
4. `build_jlpt_dataset.py` — the `jlpt` deck (the only fatal step).
5. `fetch_sample_corpus.py` — Wikipedia sample deck.

**Build** — `scripts/build_release.py`: PyInstaller `--onedir` play-only
bundle (NLP stack excluded; import buttons hide themselves). Bundles
`kanjire.db`, `glosses.db`, fonts, `--collect-all nacl` +
`--hidden-import _cffi_backend` for the updater; on Linux stages and bundles
`libGLU.so` (pyglet dlopens it and PyInstaller resets `LD_LIBRARY_PATH`, so it
must ride along). Windows → zip, Linux → tar.gz (preserves the +x bit).
Flags: `--force` (clean dist/build), `--artifact-only` (build + print
`ARTIFACT=` path; used by the WSL leg), `--publish`, `--notes`,
`--notes-from-changelog`. Manifest building signs `latest.json` and
**self-verifies** against the baked-in public key before writing.

**Release** — `python scripts/release.py <patch|minor|major>` (also
`--no-publish`, `--skip-linux`, `--dry-run`): write player-facing bullets
under `## [Unreleased]` in `CHANGELOG.md` first; the script bumps
`__version__`, stamps a dated changelog section, builds Windows natively
(force) then Linux in WSL (`Ubuntu-24.04`, `scripts/build_linux.sh` — sudo-free
pip bootstrap + apt-download libGLU staging; invoke WSL from
PowerShell/subprocess, never the git-bash Bash tool), writes one combined
signed manifest, and publishes via `gh release create/upload` (prepend
`C:\Program Files\GitHub CLI` to PATH in tool sessions).

---

## 11. Tests

| File | Count | Covers |
|---|---|---|
| `tests/test_engine.py` | 19 | dealing, scoring/combo, mismatch, rounds, timed/survival game-over, recorder events, face-collision rejection, all lives/bounty/sticker rules, 3-round cooldown |
| `tests/test_db.py` | 4 | jlpt deck present/loadable, solvable 2-face round, corpus deck (skips without `kanjire.db`) |
| `tests/test_stats.py` | 5 | classify/knowledge_score incl. streak forgiveness regressions |
| `tests/test_update.py` | 24 | version compare, sign/verify/tamper, sha, zip+tar slip, platform selection, legacy manifest, swap scripts, writability (offline, pytest-only) |
| `tests/smoke_ui.py` | ~15 checkpoints | headless end-to-end: menu → game → results → import (file+paste) → presets → persistence-across-restart → stats scene → learn buckets → kana mode |
| `tests/capture_screens.py` / `capture_responsive.py` | — | screenshot generators (`tests/_shots/`), incl. palette sweep, FR pass, and 1600×900 / 2560×1440 layouts |

All test files double as plain scripts (`_run_all`) except `test_update.py`
(needs pytest).

---

## 12. Known quirks, dead code, and open follow-ups

Found by the 2026-07-12 audit sweep; none are regressions, but they're the
honest edges of the codebase.

**Dead / vestigial**
- `ui/audio.py`: `match_hi` and `round_clear` SFX are synthesized but never
  played; `SFX.chord()` is never called.
- `userstate.py`: `data["last"]` is initialised but never read or written.
- `gfx.gradient_quad` is effectively flat (every palette sets
  `BG_TOP == BG`); retained for compatibility.
- `KanjiRe.spec` at the repo root is a stale, gitignored PyInstaller artifact
  from a manual WSL build — the pipeline drives PyInstaller via CLI args.
- `build_release._hidden_imports()` still lists `pyttsx3.drivers.sapi5` —
  the app dropped pyttsx3 for direct comtypes SAPI, so that hidden import is
  dead (`comtypes` itself is still required on Windows but is missing from
  `requirements.txt`).
- `TextInput._update_placeholder` has a redundant focused-empty branch;
  `menu._deck_label`'s `description` parameter is unused.

**Known gaps / sharp edges**
- README embeds `tests/_shots/menu.png` + `game.png`, but `tests/_shots/` is
  gitignored → images are broken on GitHub.
- `setup_data.py`'s docstring says "three/four steps"; it runs five.
- `ImportTextScene` doesn't participate in resolution scaling (raw pixel
  offsets) and its worker thread is a never-joined daemon.
- `TextInput` has no drag-to-select (explicit TODO).
- `engine._mistakes_exhausted` uses strict `>`: `max_mistakes=N` tolerates N
  mistakes and ends on the N+1th.
- A wrong-length `is_new` list from a meta_provider is silently replaced with
  all-False (quiet failure mode).
- `word_count()` loads all rows to count them; `stats.classify_words` runs
  one query per word.
- `UpdateController.apply()` trusts that the UI called `can_apply()` first
  (no internal writability re-check).
- `release.py` hardcodes `WSL_DISTRO = "Ubuntu-24.04"` with no override flag.
- The `word_stats` DDL exists in both `db.STATS_SCHEMA` and via
  `init_stats_schema` — keep them in sync by hand.
- Quick menu tab has a large empty band between WORDS and the bottom-anchored
  footer at tall resolutions (intentional, cosmetic).

**Operational notes**
- `build_release.py --force` fails with `WinError 5` if a previous
  `dist/KanjiRe/KanjiRe.exe` is still running — stop the process first.
- The updater only updates builds that already contain it, so new users need
  one manual seed download (v0.2.0+ from the release page).


## 13. Addendum: v0.3.0 - v0.7.0 (the beginner-to-reader pass)

Shipped 2026-07-12 across five releases; every feature below is tested
(92 pytest + 20 smoke checkpoints) and live via the auto-updater.

**New modules**
- `kanjire/srs/` - FSRS layer. `store.py::SrsStore` wraps py-fsrs 6 (soft
  dependency; no-op without it): `srs_state` table in stats.db, `update()`
  fed by StatsRecorder events (clean match=Good, fumbled=Hard,
  confusion/failed recall=Again, typed recall=Easy), `due_keys()`
  retrievability-sorted, `new_allowance()` (shrinks with due pile AND
  words already introduced today - review debt structurally impossible),
  `seed_known()` (placement, spread dues), `enqueue_new()` (Reading Room
  mining), `leech_keys()`. `session.py::build_today_plan` assembles Today:
  cross-deck due reviews + scoped new-word trickle, comeback plan
  (20 most-at-risk, no new) after 4+ days away.
- `kanjire/data/coverage.py` - frequency-weighted coverage per deck
  (10^zipf; true counts for corpora) + next-5%-milestone estimate.
- `kanjire/data/kanjidata.py` - read-only API over two new bundled
  sidecars: `kanjidata.db` (kradfile-u components 13k kanji; WK-Keisei
  phonetic series 2,945 kanji/591 series; kanjium pitch 124k entries;
  built by `scripts/fetch_kanji_data.py`) and `sentences.db` (Tanaka
  corpus, 62,399 sentences with complete per-sentence word indexes +
  n_kanji_words; built by `scripts/fetch_sentences.py`).
  `readable_sentences()` is the i+1 density query.
- Scenes: `journey.py` (456-station frequency road, cleared at 12/15
  known, gold frontier, boss every 5th station), `reading.py` (Reading
  Room: i+1 feed with source picker General/imported corpora, word chips,
  popup with +learn, read_log volume stats), `recall.py` (typed-recall
  epilogue after Today/Journey sessions; alternates with listening
  dictation when JP TTS available).

**Engine/config changes** - `session_mode` (finite pool, win on clearing
it, `session_left`, smaller final boards), `_group_errored` tracked in all
modes, recorder protocol `matched(word, clean)`, `SelectResult.session_complete`.

**Sampler** (`model/sampling.py`) - sequential weighted draw with stacked
confusability boosts: shared kanji x8, same phonetic series x5 (keisei),
historically-confused pairs x30 (review_log partner columns), same JLPT
x1.5; `penalize` floor unchanged. Wired via GameScene for all modes.

**Stats layer** - `review_log` (graded events + partner columns),
`read_log` (sentences/chars read, per source), `recalled()` verb,
`confusion_partners()`, `mark_known()` placement, heatmap `day_counts()`.
Vocab DB gains `corpus_sentences`/`corpus_sentence_words` (captured at
import by `ingest.index_sentences`; bundled Wikipedia deck ships 2,080).

**UI** - 5-tab nav (Play/Journey/Read/Stats/Settings). Menu: Today+PLAY
footer row (plan-aware label, done/bonus states), streak footer with
banked freezes. Stats overview: two columns - coverage bars + face bars
left; heatmap, accuracy, N5-N1 mark-known, WANTED leech-hunt button
right; word detail overlay adds pitch [n], components, sound family,
example sentence. Game: sentence toast after matches, session words-left
HUD. Results: session-complete title, streak line, practice-tricky-words.
`kana.py::romaji_to_hira` powers IME-free typed input.

**UserState** - streak_count/streak_freezes/streak_day (1 freeze per 7
days, bank 3, silently bridge gaps).

**Build/release** - sidecars bundled; fsrs hidden-import with
torch/fsrs.optimizer/pandas/tqdm excluded (a 5 GB-zip incident) + 300 MB
size guard; `release.py --rebuild` resumes a failed release; WSL build
installs fsrs; setup_data.py is now 7 steps.

**Data licenses** - kradfile-u (EDRDG/KanjiCafe CC BY-SA), WK-Keisei db
(GPL-3.0 - attribution in kanjidata.db meta), kanjium (CC BY-SA 4.0),
Tanaka corpus (CC BY 2.0 FR).
