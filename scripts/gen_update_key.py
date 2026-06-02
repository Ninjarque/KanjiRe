"""Generate the Ed25519 signing keypair for KanjiRe auto-updates (run once).

The **private** key is written to ``%USERPROFILE%\\.kanjire_keys`` (outside the
repo — never commit it). The **public** key is patched straight into
``kanjire/update/config.py`` so it ships inside every build and the app can
verify manifests offline.

    python scripts/gen_update_key.py            # create keys if absent
    python scripts/gen_update_key.py --force    # overwrite an existing keypair

If you ever lose the private key you can run with ``--force`` to mint a new
pair, but then *every* friend needs one more manual zip (the build carrying the
new public key) before auto-updates resume.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import re
import sys
from pathlib import Path

from kanjire.paths import PACKAGE_DIR

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

KEY_DIR = Path.home() / ".kanjire_keys"
PRIVATE_KEY_PATH = KEY_DIR / "update_ed25519.hex"
CONFIG_PATH = PACKAGE_DIR / "update" / "config.py"


def _patch_pubkey(public_hex: str) -> None:
    """Rewrite the ``PUBLIC_KEY_HEX = "..."`` line in update/config.py."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    new, n = re.subn(
        r'PUBLIC_KEY_HEX\s*=\s*".*?"',
        f'PUBLIC_KEY_HEX = "{public_hex}"',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"ERROR: could not find PUBLIC_KEY_HEX in {CONFIG_PATH}")
    CONFIG_PATH.write_text(new, encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing private key")
    args = p.parse_args(argv)

    from nacl.signing import SigningKey

    if PRIVATE_KEY_PATH.exists() and not args.force:
        print(f"Private key already exists: {PRIVATE_KEY_PATH}")
        print("Refusing to overwrite. Re-run with --force if you really mean to.")
        return 1

    key = SigningKey.generate()
    private_hex = bytes(key).hex()
    public_hex = bytes(key.verify_key).hex()

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_PATH.write_text(private_hex, encoding="ascii")
    try:  # tighten perms where the OS supports it
        PRIVATE_KEY_PATH.chmod(0o600)
    except OSError:
        pass

    _patch_pubkey(public_hex)

    print("✓ Keypair generated.")
    print(f"  Private key (keep secret, never commit): {PRIVATE_KEY_PATH}")
    print(f"  Public key baked into: {CONFIG_PATH}")
    print(f"    PUBLIC_KEY_HEX = {public_hex}")
    print("")
    print("Next: commit the updated config.py, then publish with:")
    print("    python scripts/build_release.py --publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
