"""Integrity + authenticity primitives for the updater.

Three independent guards, applied in order before any downloaded code runs:

1. **Ed25519 signature** over the manifest (this module) — proves the manifest
   was produced by whoever holds the private key, so a compromised host or a
   swapped asset can't push a build you didn't sign.
2. **SHA-256** of the downloaded zip vs. the value in the *signed* manifest.
3. **Zip-slip-safe extraction** — refuse archive members that would escape the
   destination directory.

The signing side (``scripts/gen_update_key.py`` / ``build_release.py``) and the
verifying side here MUST agree byte-for-byte on the signed payload, so the
canonicalisation lives in one place: :func:`canonical_payload`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

#: Manifest key that holds the detached signature; excluded from the signed
#: payload (you can't sign a field that contains its own signature).
SIGNATURE_KEY = "signature"


def canonical_payload(manifest: dict) -> bytes:
    """Deterministic bytes that get signed/verified.

    Drops the signature field, then serialises with sorted keys and tight
    separators so the producer and consumer always hash the exact same bytes
    regardless of dict ordering or whitespace.
    """
    body = {k: v for k, v in manifest.items() if k != SIGNATURE_KEY}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_manifest(manifest: dict, private_key_hex: str) -> dict:
    """Return a copy of *manifest* with a base64 Ed25519 ``signature`` added."""
    from nacl.signing import SigningKey

    key = SigningKey(bytes.fromhex(private_key_hex.strip()))
    sig = key.sign(canonical_payload(manifest)).signature
    signed = dict(manifest)
    signed[SIGNATURE_KEY] = base64.b64encode(sig).decode("ascii")
    return signed


def verify_manifest(manifest: dict, public_key_hex: str) -> bool:
    """True iff *manifest*'s signature verifies against *public_key_hex*.

    Returns ``False`` (never raises) on any problem — missing/garbled
    signature, wrong key, malformed payload — so callers can treat an
    unverifiable manifest exactly like "no update available".
    """
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        sig_b64 = manifest.get(SIGNATURE_KEY)
        if not sig_b64:
            return False
        sig = base64.b64decode(sig_b64)
        VerifyKey(bytes.fromhex(public_key_hex.strip())).verify(
            canonical_payload(manifest), sig
        )
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _is_within(base: Path, target: Path) -> bool:
    """True if *target* resolves to a path inside *base* (zip-slip guard)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_extract(zip_path: Path, dest_dir: Path) -> None:
    """Extract a **zip** into *dest_dir*, rejecting any path traversal.

    Raises :class:`ValueError` if a member would land outside *dest_dir*
    (absolute path, ``..`` components, or symlink-style escape).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            out = dest_dir / member
            if not _is_within(dest_dir, out):
                raise ValueError(f"unsafe path in archive: {member!r}")
        zf.extractall(dest_dir)


def safe_extract_tar(tar_path: Path, dest_dir: Path) -> None:
    """Extract a **tar(.gz)** into *dest_dir*, preserving Unix perms/symlinks.

    Used for Linux/macOS bundles, where a plain zip would drop the executable
    bit off the launcher. Rejects path traversal and symlinks/hardlinks that
    point outside *dest_dir* (zip-slip / link-escape).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for m in tf.getmembers():
            out = dest_dir / m.name
            if not _is_within(dest_dir, out):
                raise ValueError(f"unsafe path in archive: {m.name!r}")
            if m.issym() or m.islnk():
                target = (dest_dir / m.name).parent / m.linkname
                if not _is_within(dest_dir, target):
                    raise ValueError(
                        f"unsafe link in archive: {m.name!r} -> {m.linkname!r}"
                    )
        # ``filter="data"`` (Py 3.12+) is extra defense — it also rejects
        # traversal/links and strips setuid/sticky bits, while keeping the
        # executable bit on regular files (needed for the Linux launcher).
        try:
            tf.extractall(dest_dir, filter="data")
        except TypeError:  # very old Python without the filter kwarg
            tf.extractall(dest_dir)


def extract_archive(path: Path, dest_dir: Path) -> None:
    """Safely extract *path* into *dest_dir*, dispatching by file extension."""
    p = str(path).lower()
    if p.endswith(".zip"):
        safe_extract(path, dest_dir)
    elif p.endswith((".tar.gz", ".tgz", ".tar")):
        safe_extract_tar(path, dest_dir)
    else:
        raise ValueError(f"unsupported archive type: {path}")
