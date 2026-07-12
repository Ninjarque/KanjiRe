#!/usr/bin/env bash
# Build the Linux KanjiRe bundle inside WSL / on Linux, without sudo.
#
# Usage:  bash scripts/build_linux.sh <repo-dir>
#
# Ubuntu (esp. WSL) often ships no pip/venv and we have no passwordless sudo,
# so we bootstrap pip into ~/.local, install build deps --user, stage libGLU.so
# (pyglet dlopens it; bundling it makes minimal distros work), then build the
# tar.gz artifact via scripts/build_release.py --artifact-only.
set -e
REPO="${1:?usage: build_linux.sh <repo-dir>}"
export PATH="$HOME/.local/bin:$PATH"
BUILD="$HOME/.cache/kanjire-build"
mkdir -p "$BUILD"

# 1) Ensure pip exists (bootstrap into ~/.local if the system has none).
if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "[build_linux] bootstrapping pip into ~/.local …"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$BUILD/get-pip.py"
    python3 "$BUILD/get-pip.py" --user --break-system-packages >/dev/null 2>&1
fi

# 2) Build dependencies (idempotent; --user keeps it sudo-free).
python3 -m pip install -q --user --break-system-packages \
    'pyglet>=1.5,<2.0' pynacl 'fsrs>=6.0,<7' pyinstaller >/dev/null 2>&1

# 3) Stage libGLU.so to bundle (download the .deb + extract, no install).
GLU="$BUILD/libs"
rm -rf "$GLU" "$BUILD/gluroot"
mkdir -p "$GLU"
( cd "$BUILD" && rm -f libglu1-mesa*.deb \
    && apt-get download libglu1-mesa >/dev/null 2>&1 \
    && dpkg-deb -x libglu1-mesa*.deb gluroot ) || true
SRC="$(find "$BUILD/gluroot" -name 'libGLU.so.1*' 2>/dev/null | head -1)"
if [ -n "$SRC" ]; then
    cp "$SRC" "$GLU/libGLU.so"
    export KANJIRE_BUNDLE_LIBS="$GLU/libGLU.so"
    echo "[build_linux] will bundle libGLU.so from $SRC"
else
    echo "[build_linux] WARNING: couldn't stage libGLU.so; relying on system GL."
fi

# 4) Build the Linux artifact (no --force: keeps the Windows zip in shared dist/).
cd "$REPO"
python3 scripts/build_release.py --artifact-only
