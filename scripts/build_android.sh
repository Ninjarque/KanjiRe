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
[ -n "$REPO" ] && [ -n "$VERSION" ] || { echo "usage: build_android.sh <repo> <version>"; exit 2; }

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
export PATH="$VENV/bin:$PATH"
"$VENV/bin/buildozer" android debug

APK=$(ls -t bin/*.apk 2>/dev/null | head -1)
[ -n "$APK" ] || { echo "ERROR: buildozer produced no APK"; exit 4; }
mkdir -p "$REPO/dist"
cp "$APK" "$REPO/dist/KanjiRe-$VERSION-android.apk"
echo "OK: dist/KanjiRe-$VERSION-android.apk"
