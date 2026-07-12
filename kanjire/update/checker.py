"""Find out whether a newer signed release exists (network side).

Pure-ish: this module only *reads* the network and returns a verified
:class:`UpdateInfo` (or ``None``). It never touches the filesystem install or
any pyglet object, so it is safe to call from a background thread.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from kanjire import __version__
from kanjire.update import config, verify


def _debug(msg: str) -> None:
    """Record *why* a check came back empty.

    check_for_update() returns None for every benign failure, which is right for
    the UI and useless when a player reports "it never sees the update" - there
    was no way to tell a throttled check from a TLS error from a bad signature.
    The reason now always lands in ``<user dir>/update.log`` (and on stderr with
    KANJIRE_UPDATE_DEBUG=1), so a friend can just send the file.
    """
    if os.environ.get("KANJIRE_UPDATE_DEBUG"):
        print(f"[update] {msg}", file=sys.stderr, flush=True)
    try:
        from kanjire.paths import USER_DIR

        with open(USER_DIR / "update.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001 — logging must never break a check
        pass


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


def urlopen(req, timeout: int):
    """urlopen, with a certifi fallback when the OS has no usable CA bundle.

    A frozen build on a lean distro can hit CERTIFICATE_VERIFY_FAILED because
    there is no system CA store where OpenSSL expects one. That raises
    URLError -> OSError -> a silent "no update available", which is
    indistinguishable from being up to date. Retry once against certifi's
    bundle before giving up.
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        import ssl

        if not isinstance(exc.reason, ssl.SSLError):
            raise
        try:
            import certifi
        except ImportError:
            raise exc
        _debug(f"TLS verify failed ({exc.reason}); retrying with certifi")
        ctx = ssl.create_default_context(cafile=certifi.where())
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _http_get(url: str, timeout: int) -> bytes:
    # HTTPS-only: refuse to fetch anything over plaintext, even via redirect to
    # a manifest-declared URL.
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": f"KanjiRe/{__version__}"})
    with urlopen(req, timeout=timeout) as resp:
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
        _debug("no public key baked in - updates disabled")
        return None
    # KANJIRE_UPDATE_PRETEND_VERSION lets a real (frozen) build pretend it is
    # older, so the whole download/swap/relaunch path can be exercised against
    # the live release instead of only being reasoned about.
    current = (os.environ.get("KANJIRE_UPDATE_PRETEND_VERSION")
               or current_version or __version__)
    _debug(f"current={current} platform={current_platform()}")
    try:
        manifest = fetch_manifest(manifest_url)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # offline / DNS / timeout / TLS / garbage — benign for the UI, but the
        # single most likely thing to go wrong in the field, so say what it was.
        _debug(f"manifest fetch failed: {type(exc).__name__}: {exc}")
        return None

    # Authenticity FIRST: never trust the version/url/hash in an unverified
    # manifest.
    if not verify.verify_manifest(manifest, config.PUBLIC_KEY_HEX):
        _debug("manifest signature did NOT verify")
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
                _debug(f"release {version} has no build for {current_platform()}"
                       f" (has: {sorted(platforms)})")
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
    except (KeyError, ValueError, TypeError) as exc:
        _debug(f"malformed manifest: {type(exc).__name__}: {exc}")
        return None

    if not url.lower().startswith("https://"):
        _debug(f"refusing non-HTTPS asset url: {url!r}")
        return None
    if not is_newer(version, current):
        _debug(f"already up to date ({current} >= {version})")
        return None
    _debug(f"update available: {version} <- {url}")
    return UpdateInfo(version=version, url=url, sha256=sha256, size=size, notes=notes)
