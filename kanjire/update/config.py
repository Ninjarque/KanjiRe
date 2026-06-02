"""Static configuration + the baked-in public key for the auto-updater.

Edit ``REPO_OWNER`` / ``REPO_NAME`` to point at the GitHub repo that hosts the
release zips, and run ``scripts/gen_update_key.py`` once to fill in
``PUBLIC_KEY_HEX`` (the matching private key stays only on your machine).
"""
from __future__ import annotations

#: GitHub repo that hosts the release zips + signed ``latest.json`` manifests.
#: The repo (or at least its releases) must be **public** so friends without a
#: GitHub account can download the assets.
REPO_OWNER = "ninjarque"
REPO_NAME = "KanjiRe"

#: Name of the signed manifest asset attached to every release.
MANIFEST_ASSET = "latest.json"

#: Hex-encoded Ed25519 *public* key (32 bytes → 64 hex chars). Generated once
#: by ``scripts/gen_update_key.py``; an empty value disables update checks
#: (the app simply behaves as it always has).
PUBLIC_KEY_HEX = "6856d9ddd6e836eaa6761105b4b70bc0d8e520c1186e155480409e7588bb438e"

#: Don't run an automatic background check more often than this (seconds).
#: The manual "Check for updates" button in Settings ignores the throttle.
CHECK_INTERVAL_SECONDS = 4 * 3600

#: Network timeout for the small manifest fetch and the zip download (seconds).
HTTP_TIMEOUT = 15

#: GitHub redirects ``releases/latest/download/<asset>`` to the newest release's
#: asset of that name — so we never need the rate-limited API just to find the
#: manifest, and never need an auth token for a public repo.
MANIFEST_URL = (
    f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
    f"/releases/latest/download/{MANIFEST_ASSET}"
)


def updates_enabled() -> bool:
    """True only when a public key has been baked in (i.e. you ran the keygen)."""
    return bool(PUBLIC_KEY_HEX.strip())
