"""Self-update for KanjiRe release builds.

The flow, end to end:

* **Publish** (maintainer): ``scripts/build_release.py --publish`` builds the
  zip, writes a small ``latest.json`` manifest (version + download URL +
  SHA-256), **signs the manifest with an Ed25519 private key**, and uploads
  both to a GitHub Release.
* **Check** (every frozen build, on launch): a background thread downloads
  ``latest.json`` over HTTPS, **verifies its signature** against the public key
  baked into the app, and compares versions.
* **Apply** (on the player's confirmation): the new zip is downloaded, its
  SHA-256 is checked against the signed manifest, it is extracted with
  zip-slip protection, and a small helper script swaps the program folder and
  relaunches the app. The writable ``%APPDATA%/KanjiRe`` state is never touched.

Nothing here runs in dev (non-frozen) runs unless explicitly invoked, so
``python -m kanjire`` never tries to update itself. See :mod:`kanjire.update.config`.
"""
from __future__ import annotations

from kanjire.update.checker import UpdateInfo, check_for_update

__all__ = ["UpdateInfo", "check_for_update"]
