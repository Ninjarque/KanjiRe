"""The release build must fail unless the data files are bundled and current.

The auto-updater swaps the whole install folder, so whatever the build bundles
is exactly what a player gets on their next update - that's how the vocabulary
and the reading corpus ride the updater. This guards that guarantee.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_build_release():
    spec = importlib.util.spec_from_file_location(
        "build_release_mod", _ROOT / "scripts" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def br():
    return _load_build_release()


def _fake_bundle(tmp_path, br, files):
    """Build a fake install folder with the given {name: bytes} under _internal."""
    data = tmp_path / "KanjiRe" / "_internal" / "kanjire" / "data"
    data.mkdir(parents=True)
    for name, content in files.items():
        (data / name).write_bytes(content)
    return tmp_path / "KanjiRe"


def test_a_current_bundle_passes(tmp_path, br):
    # Copy the real source data files verbatim -> must pass.
    from kanjire.paths import DATA_DIR

    files = {}
    for name in br._REQUIRED_DATA + br._OPTIONAL_DATA:
        src = DATA_DIR / name
        if src.exists():
            files[name] = src.read_bytes()
    bundle = _fake_bundle(tmp_path, br, files)
    assert br._check_data_bundled(bundle) == 0


def test_every_guarded_file_is_actually_bundled(br):
    """The guard list and the --add-data list must not drift apart.

    They are two separate literals: adding a sidecar to one and forgetting
    the other either ships nothing (guard-only) or ships it unverified
    (--add-data only). Both have happened to other data files.
    """
    from kanjire.paths import DATA_DIR

    flags = " ".join(br._add_data_args())
    for name in br._REQUIRED_DATA + br._OPTIONAL_DATA:
        if (DATA_DIR / name).exists():
            assert name in flags, f"{name} is guarded but never bundled"


def test_android_packaging_includes_the_data_files():
    """Android has no build-time guard, so pin the two lines that matter."""
    spec = (_ROOT / "android" / "buildozer.spec").read_text(encoding="utf-8")
    exts = next(line for line in spec.splitlines()
                if line.startswith("source.include_exts"))
    assert "db" in [e.strip() for e in exts.split("=")[1].split(",")]
    excluded = next((line for line in spec.splitlines()
                     if line.startswith("source.exclude_patterns")), "")
    for name in ("kanjire.db", "clusters.db", "kanjidata.db", "sentences.db"):
        assert name not in excluded, f"{name} is excluded from the APK"


def test_a_missing_required_file_fails(tmp_path, br):
    from kanjire.paths import DATA_DIR

    files = {}
    for name in br._OPTIONAL_DATA:
        src = DATA_DIR / name
        if src.exists():
            files[name] = src.read_bytes()
    # kanjire.db deliberately absent.
    bundle = _fake_bundle(tmp_path, br, files)
    assert br._check_data_bundled(bundle) == 1


def test_a_stale_file_fails(tmp_path, br):
    from kanjire.paths import DATA_DIR

    files = {}
    for name in br._REQUIRED_DATA + br._OPTIONAL_DATA:
        src = DATA_DIR / name
        if src.exists():
            files[name] = src.read_bytes()
    # Corrupt one file so its hash no longer matches the source.
    if "sentences.db" in files:
        files["sentences.db"] = b"STALE-CONTENT"
    else:
        files["kanjire.db"] = b"STALE-CONTENT"
    bundle = _fake_bundle(tmp_path, br, files)
    assert br._check_data_bundled(bundle) == 1
