"""Android self-update path: platform detection, APK staging, apply gating."""
from __future__ import annotations

from pathlib import Path

from kanjire.update import applier, checker
from kanjire.update.checker import UpdateInfo
from kanjire.update.controller import READY, UpdateController


def test_current_platform_android_wins_over_linux(monkeypatch):
    # sys.platform IS "linux" inside python-for-android — android must be
    # detected first or phones would download the Linux tar.gz.
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/app")
    assert checker.current_platform() == "android"
    monkeypatch.delenv("ANDROID_ARGUMENT")
    assert checker.current_platform() in ("windows", "linux", "macos")


def test_stage_apk_downloads_without_extract(monkeypatch, tmp_path):
    monkeypatch.setenv("ANDROID_ARGUMENT", str(tmp_path))
    monkeypatch.setenv("ANDROID_PRIVATE", str(tmp_path))

    def fake_download(info, dest, progress=None):
        Path(dest).write_bytes(b"apk-bytes")
        return Path(dest)

    monkeypatch.setattr(applier, "download", fake_download)
    info = UpdateInfo(version="9.9.9",
                      url="https://x/KanjiRe-9.9.9-android.apk",
                      sha256="0" * 64, size=9, notes="")
    staged = applier.stage(info)
    assert str(staged).endswith("KanjiRe-9.9.9.apk")
    assert staged.read_bytes() == b"apk-bytes"
    assert staged.parent.name == "updates"   # the FileProvider-exported dir


def test_controller_can_apply_apk_without_folder_swap(tmp_path):
    class _State:
        update_last_check = 0.0

        def set_update_last_check(self, ts):
            pass

    c = UpdateController(_State())
    c.status = READY
    c.staged = tmp_path / "KanjiRe-9.9.9.apk"
    c.info = UpdateInfo(version="9.9.9", url="https://x/a.apk",
                        sha256="0" * 64, size=1, notes="")
    # No can_self_update() folder-write probe for an APK — the system
    # installer applies it.
    assert c.can_apply() is True
