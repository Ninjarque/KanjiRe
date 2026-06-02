"""Offline tests for the auto-updater: versioning, signing, integrity, staging.

No network and no frozen build required — the GitHub fetch is monkeypatched and
the swap/script logic is exercised against temp dirs.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from kanjire.update import applier, checker, config, verify

# A throwaway keypair generated for the tests (NOT the production key).
from nacl.signing import SigningKey

_TEST_KEY = SigningKey.generate()
TEST_PRIVATE_HEX = bytes(_TEST_KEY).hex()
TEST_PUBLIC_HEX = bytes(_TEST_KEY.verify_key).hex()


# --- version comparison ------------------------------------------------- #
def test_parse_version():
    assert checker.parse_version("0.2.0") == (0, 2, 0)
    assert checker.parse_version("1.2.3-rc1") == (1, 2, 3)


@pytest.mark.parametrize("remote,local,expect", [
    ("0.2.0", "0.1.0", True),
    ("0.1.1", "0.1.0", True),
    ("0.1.0", "0.1.0", False),
    ("0.1.0", "0.2.0", False),
    ("1.0.0", "0.9.9", True),
])
def test_is_newer(remote, local, expect):
    assert checker.is_newer(remote, local) is expect


# --- signing / verification --------------------------------------------- #
def _manifest(**over):
    base = {
        "version": "0.2.0",
        "url": "https://github.com/x/y/releases/download/v0.2.0/KanjiRe-0.2.0-windows.zip",
        "sha256": "ab" * 32,
        "size": 123,
        "notes": "hi",
    }
    base.update(over)
    return base


def test_sign_verify_roundtrip():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    assert "signature" in signed
    assert verify.verify_manifest(signed, TEST_PUBLIC_HEX) is True


def test_verify_rejects_tampered_field():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    signed["version"] = "9.9.9"  # tamper after signing
    assert verify.verify_manifest(signed, TEST_PUBLIC_HEX) is False


def test_verify_rejects_wrong_key():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    other_pub = bytes(SigningKey.generate().verify_key).hex()
    assert verify.verify_manifest(signed, other_pub) is False


def test_verify_rejects_missing_signature():
    assert verify.verify_manifest(_manifest(), TEST_PUBLIC_HEX) is False


# --- integrity helpers -------------------------------------------------- #
def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert verify.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()


def test_safe_extract_ok(tmp_path):
    zp = tmp_path / "ok.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("KanjiRe/file.txt", "data")
    dest = tmp_path / "out"
    verify.safe_extract(zp, dest)
    assert (dest / "KanjiRe" / "file.txt").read_text() == "data"


def test_safe_extract_rejects_zip_slip(tmp_path):
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises(ValueError):
        verify.safe_extract(zp, tmp_path / "out")


# --- check_for_update (network mocked) ---------------------------------- #
@pytest.fixture
def baked_key(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_KEY_HEX", TEST_PUBLIC_HEX)


def _patch_manifest(monkeypatch, manifest):
    monkeypatch.setattr(checker, "fetch_manifest", lambda *a, **k: manifest)


def test_check_disabled_without_key(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_KEY_HEX", "")
    assert checker.check_for_update("0.1.0") is None


def test_check_returns_info_for_newer(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.2.0"), TEST_PRIVATE_HEX)
    _patch_manifest(monkeypatch, signed)
    info = checker.check_for_update("0.1.0")
    assert info is not None and info.version == "0.2.0"


def test_check_none_when_not_newer(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.1.0"), TEST_PRIVATE_HEX)
    _patch_manifest(monkeypatch, signed)
    assert checker.check_for_update("0.1.0") is None


def test_check_rejects_bad_signature(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.2.0"), TEST_PRIVATE_HEX)
    signed["version"] = "0.3.0"  # tamper
    _patch_manifest(monkeypatch, signed)
    assert checker.check_for_update("0.1.0") is None


def test_check_rejects_non_https_url(monkeypatch, baked_key):
    signed = verify.sign_manifest(
        _manifest(version="0.2.0", url="http://evil/x.zip"), TEST_PRIVATE_HEX
    )
    _patch_manifest(monkeypatch, signed)
    assert checker.check_for_update("0.1.0") is None


def test_check_survives_network_error(monkeypatch, baked_key):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(checker, "fetch_manifest", boom)
    assert checker.check_for_update("0.1.0") is None


# --- swap-script generation --------------------------------------------- #
def test_swap_script_has_rollback(tmp_path):
    bat = applier._swap_script(tmp_path)
    text = bat.read_text(encoding="ascii")
    assert ":rollback" in text
    assert "tasklist" in text          # waits for the app to exit
    assert 'del "%~f0"' in text        # self-deletes


def test_can_self_update_writable(tmp_path):
    assert applier.can_self_update(tmp_path / "KanjiRe") is True
