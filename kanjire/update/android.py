"""Android side of the self-updater: download the APK, hand it to the OS.

There is no folder swap on Android — the system package installer applies
APK updates (same signing key required). Our job is: verify the Ed25519
manifest + sha256 like every other platform, then fire the install intent;
Android shows its own confirmation UI and replaces the app. User state
survives because it lives in the app's private dir, not the bundle
(see kanjire/paths.py).

Requires (declared in android/buildozer.spec):
* REQUEST_INSTALL_PACKAGES permission — the "install unknown apps" prompt
  the player accepts once;
* a FileProvider — API 24+ forbids file:// URIs across processes, so the
  installer must be handed a content:// URI.
"""
from __future__ import annotations

import os
from pathlib import Path


def is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ


def apk_staging_dir() -> Path:
    """Somewhere the FileProvider exports and we may write."""
    base = os.environ.get("ANDROID_PRIVATE") or os.environ["ANDROID_ARGUMENT"]
    d = Path(base) / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_apk(apk_path: Path) -> bool:
    """Fire the package-installer intent for a downloaded, verified APK.

    Returns True if the intent was launched (the app stays alive; Android's
    installer takes over and restarts us on completion)."""
    try:
        from jnius import autoclass, cast

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")

        activity = PythonActivity.mActivity
        authority = activity.getPackageName() + ".fileprovider"
        uri = FileProvider.getUriForFile(activity, authority,
                                         File(str(apk_path)))
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(
            uri, "application/vnd.android.package-archive")
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return True
    except Exception:
        return False
