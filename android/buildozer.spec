[app]
title = KanjiRe
package.name = kanjire
package.domain = org.kanjire

# The spec lives in android/; the app source is the repo root.
source.dir = ..
source.include_exts = py,ttf,db,json,txt,md
source.exclude_dirs = tests, docs, scripts, corpora, dist, build, android, .git, .buildozer, .vscode, __pycache__
# stats.db / user_state.json are the DEV machine's user state (dev runs keep
# it inside kanjire/data) — never ship it; Android writes its own under
# ANDROID_PRIVATE.
source.exclude_patterns = KanjiRe.spec, release.py, kanjire/data/stats.db, kanjire/data/user_state.json, kanjire/data/session.json

version.regex = __version__ = "(.*)"
version.filename = %(source.dir)s/kanjire/__init__.py

# Portable core only: the pyglet UI is excluded at runtime (never imported by
# kanjire.kivyui), and heavy dev-time deps (fugashi/jamdict) are not bundled.
# pynacl: the updater's Ed25519 manifest verification — WITHOUT it the update
# check dies (now caught, but then no phone could ever verify an update).
requirements = python3,kivy==2.3.1,paho-mqtt,certifi,pynacl

orientation = all
# Immersive fullscreen. With fullscreen=0 the SDL surface renders under the
# status bar while touch coords are offset by its height (p4a #2326 / #153):
# every button only responded near its edges, worst at the screen bottom and
# on fold-state changes. No bar → no offset.
fullscreen = 1

# REQUEST_INSTALL_PACKAGES: the self-updater downloads the new APK and hands
# it to the system installer (the player OKs "install unknown apps" once).
android.permissions = INTERNET, REQUEST_INSTALL_PACKAGES

android.api = 34
android.minapi = 24
android.archs = arm64-v8a
# Unattended builds (release.py runs this inside WSL with no TTY).
android.accept_sdk_license = True

# FileProvider for the updater: API 24+ forbids file:// URIs across
# processes, so the downloaded APK is exported through a content:// URI.
# A <provider> must sit INSIDE <application>, which no buildozer key can do
# — p4a_hook.py patches the dist's manifest template instead.
android.enable_androidx = True
android.gradle_dependencies = androidx.core:core:1.13.1
# The <provider> element: injected into the manifest template by the hook.
p4a.hook = ./p4a_hook.py
# The @xml/file_paths resource it references: p4a wipes src/main/res to a
# pristine state each build, so this must ride the official add_resources
# channel (copied in AFTER the wipe), not the hook.
android.add_resources = ./res_extra/xml/file_paths.xml:xml/file_paths.xml

# Keep the user's stats/settings across reinstalls where possible.
android.allow_backup = True

# Kivy on Android: pause instead of dying when the app loses focus.
#   (kanjire relies on this for MQTT reconnect-on-resume.)
android.wakelock = False

[buildozer]
log_level = 2
warn_on_root = 1
# Everything build-related stays inside android/ (never synced to Windows).
build_dir = ./.buildozer
bin_dir = ./bin
