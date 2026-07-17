#!/bin/sh
# Dump interesting AndroidManifest entries from a built APK (WSL helper).
# Usage: inspect_apk_manifest.sh <apk-path>
APK="$1"
SDK="$HOME/.buildozer/android/platform/android-sdk"
AAPT=$(ls "$SDK"/build-tools/*/aapt 2>/dev/null | head -1)
[ -n "$AAPT" ] || { echo "aapt not found under $SDK"; exit 2; }
"$AAPT" dump xmltree "$APK" AndroidManifest.xml \
    | grep -iE "provider|FILE_PROVIDER|REQUEST_INSTALL|authorities|uses-sdk|targetSdk"
