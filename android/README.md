# KanjiRe on Android

The Android app is the **Kivy UI** (`kanjire/kivyui/`) over the same core as
the desktop builds — game engine, decks, stats, SRS, reading curriculum, and
the MQTT multiplayer/friends relay are byte-identical, so cross-platform
multiplayer works out of the box.

## One-time WSL toolchain setup (needs sudo)

```sh
sudo apt update && sudo apt install -y \
    python3-pip python3.12-venv build-essential git zip unzip \
    autoconf libtool libltdl-dev pkg-config cmake ccache \
    zlib1g-dev libffi-dev libssl-dev libncurses-dev
python3 -m venv ~/kanjire-buildozer-venv
~/kanjire-buildozer-venv/bin/pip install buildozer cython
```

The first APK build downloads the Android SDK/NDK (~2 GB) into
`~/kanjire-android/android/.buildozer` and takes a long while; later builds
are minutes.

## Building

* By hand: `wsl -d Ubuntu-24.04 -- sh scripts/build_android.sh /mnt/m/Japanese/KanjiRe <version>`
* As part of a release: `python scripts/release.py <level>` builds
  Windows + Linux + the APK and signs all three into one manifest
  (`--skip-android` / `--require-android` adjust the failure policy).

## Distribution & self-update

Friends sideload the APK once ("install unknown apps" permission). After
that the in-app updater handles new versions: it checks the same
Ed25519-signed `latest.json` on GitHub Releases, downloads the APK for the
`android` platform entry, verifies its sha256, and hands it to the system
installer via a FileProvider (`kanjire/update/android.py`).

**Signing caveat:** Android only installs an update over an existing app
when both are signed with the same key. Debug builds use this WSL machine's
persistent `~/.android/debug.keystore` — always release from the same
machine, or migrate that keystore.

## Platform notes

* User state lives in the app's persistent private dir
  (`ANDROID_PRIVATE/kanjire_user`), which survives APK updates — the p4a
  app bundle itself is wiped on every update (see `kanjire/paths.py`).
* TTS uses the system TextToSpeech engine (Google TTS has strong Japanese
  voices) via pyjnius — `kanjire/kivyui/audio.py`.
* Corpus imports (fugashi/MeCab) stay desktop-only; imported decks may sync
  over the relay in a future version.
* The Samsung Fold cover/inner switch is a live window resize; every screen
  re-lays out (verified by the fold-size passes in `tests/kivy_capture.py`).
