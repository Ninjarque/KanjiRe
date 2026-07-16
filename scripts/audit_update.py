"""Verify the LIVE update channel: can every released version reach the latest?

Run after publishing (``release.py`` calls it automatically), or any time you
want to be sure nobody is stranded on an old build::

    python scripts/audit_update.py

Checks, against the real published manifest:

* the Ed25519 signature verifies with the baked-in public key,
* both platform entries (windows, linux) exist,
* the top-level ``url``/``sha256``/``size`` mirror is still there - 0.1.x and
  0.2.x clients predate the ``platforms`` map and read only those,
* every historical version is actually offered the newest build (this catches
  version-compare bugs, e.g. 0.9.0 vs 0.10.0),
* the advertised asset URLs return HTTP 200 with the size the manifest claims.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import sys
import urllib.request

from kanjire import __version__
from kanjire.update import config as ucfg
from kanjire.update import verify as uverify
from kanjire.update.checker import check_for_update, fetch_manifest

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

#: Every version ever published. Add new ones as they ship.
RELEASED = [
    "0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0",
    "0.8.0", "0.8.1", "0.9.0", "0.10.0", "0.11.0", "0.11.1", "0.12.0",
    "0.13.0", "0.14.0", "0.15.0", "0.16.0", "0.17.0", "0.18.0", "0.19.0", "0.20.0", "0.21.0",
]


def main(argv=None) -> int:
    problems: list[str] = []
    print(f"repo version : {__version__}")
    print(f"manifest     : {ucfg.MANIFEST_URL}")

    try:
        manifest = fetch_manifest()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to fetch the manifest: {exc}")
        return 1
    latest = manifest.get("version")
    print(f"published    : {latest}")

    if not uverify.verify_manifest(manifest, ucfg.PUBLIC_KEY_HEX):
        problems.append("signature does NOT verify against the baked-in key")
    else:
        print("signature    : OK")

    plats = manifest.get("platforms") or {}
    for p in ("windows", "linux"):
        if p not in plats:
            problems.append(f"no '{p}' build in the manifest")
    for key in ("url", "sha256", "size"):
        if key not in manifest:
            problems.append(
                f"top-level '{key}' missing - 0.1.x/0.2.x clients cannot update")

    print("\nwhat each released client would do:")
    for v in RELEASED:
        info = check_for_update(v)
        if v == latest:
            if info is not None:
                problems.append(f"{v} (latest) was offered {info.version}")
            print(f"  {v:<8} up to date")
        elif info is None:
            problems.append(f"{v} is offered NO update - it would be stranded")
            print(f"  {v:<8} NO UPDATE  <-- stranded")
        elif info.version != latest:
            problems.append(f"{v} offered {info.version}, expected {latest}")
            print(f"  {v:<8} -> {info.version}  <-- wrong target")
        else:
            print(f"  {v:<8} -> {info.version}")

    print("\nassets:")
    for name, entry in sorted(plats.items()):
        try:
            req = urllib.request.Request(entry["url"], method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as resp:
                size = int(resp.headers.get("Content-Length") or 0)
            flag = "OK" if size == entry["size"] else "SIZE MISMATCH"
            if size != entry["size"]:
                problems.append(f"{name}: manifest size {entry['size']} != {size}")
            print(f"  {name:<8} HTTP 200  {size / 1e6:6.1f} MB  {flag}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name}: asset not downloadable ({exc})")
            print(f"  {name:<8} FAILED: {exc}")

    print()
    if problems:
        print("UPDATE AUDIT FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("UPDATE AUDIT OK - every released version updates to the latest build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
