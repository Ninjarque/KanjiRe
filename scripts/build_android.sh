#!/bin/sh
# Build the KanjiRe Android APK inside WSL.
#
#   build_android.sh <repo-mnt-path> <version>
#
# buildozer/python-for-android can't build on /mnt/* (DrvFS breaks symlinks
# and is painfully slow), so the repo is rsynced to a native-FS work dir and
# the APK is copied back to <repo>/dist/.
#
# One-time setup (needs sudo, see android/README.md):
#   sudo apt install -y python3-pip python3.12-venv build-essential git zip \
#        unzip autoconf libtool libltdl-dev pkg-config cmake ccache \
#        zlib1g-dev libffi-dev libssl-dev libncurses-dev
#   python3 -m venv ~/kanjire-buildozer-venv
#   ~/kanjire-buildozer-venv/bin/pip install buildozer cython
#
# NOTE: debug APKs are signed with this machine's persistent debug keystore
# (~/.android/debug.keystore). Android only installs an update over the old
# version when signatures match, so always release from the same WSL machine
# (or move that keystore with you).
set -e

REPO="$1"
VERSION="$2"
[ -n "$REPO" ] || { echo "usage: build_android.sh <repo> [version]"; exit 2; }

# The APK's INTERNAL version always comes from kanjire/__init__.py (buildozer
# reads it via version.regex). A mismatched filename once shipped a stack of
# "0.23.1..4" APKs that were all really 0.23.0 — so the source is now the
# only truth, and an explicit arg must agree with it.
REAL_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/kanjire/__init__.py")
[ -n "$REAL_VERSION" ] || { echo "ERROR: no __version__ in kanjire/__init__.py"; exit 2; }
if [ -n "$VERSION" ] && [ "$VERSION" != "$REAL_VERSION" ]; then
    echo "ERROR: asked for $VERSION but kanjire/__init__.py says $REAL_VERSION."
    echo "       The filename must match what the app believes it is."
    exit 5
fi
VERSION="$REAL_VERSION"

VENV="$HOME/kanjire-buildozer-venv"
WORK="$HOME/kanjire-android"

if [ ! -x "$VENV/bin/buildozer" ]; then
    echo "ERROR: buildozer venv missing at $VENV — run the one-time setup in this script's header."
    exit 3
fi

mkdir -p "$WORK"
rsync -a --delete \
    --exclude .git --exclude .buildozer --exclude dist --exclude build \
    --exclude __pycache__ --exclude corpora --exclude "*.pyc" \
    "$REPO/" "$WORK/"

cd "$WORK/android"
# p4a installs meson/ninja with `pip --user` (venv must be created with
# --system-site-packages or that pip call fails outright); their scripts
# land in ~/.local/bin.
export PATH="$VENV/bin:$HOME/.local/bin:$PATH"
# User-space JDK (WSL ships only a JRE; gradle/aapt need javac). Installed
# once by: curl adoptium temurin-17 tarball -> ~/jdk17 (no sudo needed).
if [ -x "$HOME/jdk17/bin/javac" ]; then
    export JAVA_HOME="$HOME/jdk17"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
"$VENV/bin/buildozer" android debug

APK=$(ls -t bin/*.apk 2>/dev/null | head -1)
[ -n "$APK" ] || { echo "ERROR: buildozer produced no APK"; exit 4; }
mkdir -p "$REPO/dist"
cp "$APK" "$REPO/dist/KanjiRe-$VERSION-android.apk"
echo "OK: dist/KanjiRe-$VERSION-android.apk"
