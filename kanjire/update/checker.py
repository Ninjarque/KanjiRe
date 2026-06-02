"""Find out whether a newer signed release exists (network side).

Pure-ish: this module only *reads* the network and returns a verified
:class:`UpdateInfo` (or ``None``). It never touches the filesystem install or
any pyglet object, so it is safe to call from a background thread.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass

from kanjire import __version__
from kanjire.update import config, verify


def current_platform() -> str:
    """Normalised OS key matching the manifest's ``platforms`` map."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


@dataclass(frozen=True)
class UpdateInfo:
    """A verified, newer-than-current release described by a signed manifest."""

    version: str
    url: str
    sha256: str
    size: int
    notes: str


_NUM = re.compile(r"\d+")


def parse_version(v: str) -> tuple[int, ...]:
    """Lenient numeric version tuple: ``"0.2.0"`` → ``(0, 2, 0)``.

    Trailing pre-release junk (``"1.2.0-rc1"``) is reduced to its leading
    numbers so a release always compares >= its own pre-releases. Good enough
    for the simple ``MAJOR.MINOR.PATCH`` scheme this project uses.
    """
    return tuple(int(n) for n in _NUM.findall(v.split("-")[0].split("+")[0]))


def is_newer(remote: str, local: str) -> bool:
    """True if *remote* is a strictly higher version than *local*."""
    return parse_version(remote) > parse_version(local)


def _http_get(url: str, timeout: int) -> bytes:
    # HTTPS-only: refuse to fetch anything over plaintext, even via redirect to
    # a manifest-declared URL.
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": f"KanjiRe/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
        if not final.lower().startswith("https://"):
            raise ValueError(f"redirected to non-HTTPS URL: {final!r}")
        return resp.read()


def fetch_manifest(url: str | None = None, timeout: int | None = None) -> dict:
    """Download and JSON-parse the manifest. Raises on network/parse errors."""
    url = url or config.MANIFEST_URL
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    return json.loads(_http_get(url, timeout).decode("utf-8"))


def check_for_update(
    current_version: str | None = None,
    *,
    manifest_url: str | None = None,
) -> UpdateInfo | None:
    """Return an :class:`UpdateInfo` if a verified, newer release exists.

    Returns ``None`` for "nothing to do" in every benign case — updates
    disabled, network error, bad signature, same/older version, or a malformed
    manifest. Only genuinely unexpected programming errors propagate.
    """
    if not config.updates_enabled():
        return None
    current = current_version or __version__
    try:
        manifest = fetch_manifest(manifest_url)
    except (OSError, ValueError, json.JSONDecodeError):
        return None  # offline / DNS / timeout / garbage — just skip quietly

    # Authenticity FIRST: never trust the version/url/hash in an unverified
    # manifest.
    if not verify.verify_manifest(manifest, config.PUBLIC_KEY_HEX):
        return None

    try:
        version = str(manifest["version"])
        notes = str(manifest.get("notes", ""))
        # Pick the asset for *this* OS. New manifests carry a ``platforms`` map;
        # legacy 0.1.x manifests only had top-level fields (Windows-only).
        platforms = manifest.get("platforms")
        if isinstance(platforms, dict):
            entry = platforms.get(current_platform())
            if not entry:
                return None  # this release has no build for our OS
            url = str(entry["url"])
            sha256 = str(entry["sha256"]).lower()
            size = int(entry.get("size", 0))
        else:
            # Legacy single-platform manifest == Windows. Don't let a Linux/mac
            # client download a Windows zip.
            if current_platform() != "windows":
                return None
            url = str(manifest["url"])
            sha256 = str(manifest["sha256"]).lower()
            size = int(manifest.get("size", 0))
    except (KeyError, ValueError, TypeError):
        return None

    if not url.lower().startswith("https://"):
        return None
    if not is_newer(version, current):
        return None
    return UpdateInfo(version=version, url=url, sha256=sha256, size=size, notes=notes)
