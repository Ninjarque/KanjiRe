"""Drives the update lifecycle on a background thread, for the UI to poll.

The pyglet UI runs on one thread and must never block on the network, so all
checking/downloading happens in a daemon thread here. The thread only mutates
plain attributes (``status`` / ``info`` / ``staged``); scenes read them each
frame and build/tear-down their banner accordingly. Applying the update and
relaunching is the only thing that touches the running process, and it's only
triggered by an explicit user click on the UI thread.
"""
from __future__ import annotations

import os
import threading
import time

from kanjire import __version__
from kanjire.update import applier, checker, config

# Lifecycle states the UI switches on.
IDLE = "idle"
CHECKING = "checking"
DOWNLOADING = "downloading"
READY = "ready"
UP_TO_DATE = "up_to_date"
ERROR = "error"


class UpdateController:
    def __init__(self, state) -> None:
        self.state = state            # UserState, for the check-throttle timestamp
        self.status = IDLE
        self.info = None              # checker.UpdateInfo once a newer build is found
        self.staged = None            # Path to the extracted new bundle once downloaded
        self.error: str | None = None
        self.progress = (0, 0)        # (downloaded, total) bytes
        self._dismissed = False       # "Later" — session-only; re-prompts next launch
        self._thread: threading.Thread | None = None

    # -- gating ---------------------------------------------------------- #
    def self_update_capable(self) -> bool:
        """True for standalone frozen bundles and Android APK installs (or
        the dev test override).

        pip / distro-package installs are updated by their package manager, so
        the in-app updater must stay inert there — even on a manual check, which
        would otherwise try to swap a manager-owned install."""
        from kanjire.update.android import is_android
        return bool(applier.is_frozen() or is_android()
                    or os.environ.get("KANJIRE_UPDATE_TEST"))

    def _allowed(self, force: bool) -> bool:
        """Gate every check on (updates configured) AND (self-update capable).
        ``force`` only skips the time throttle — it does NOT bypass these."""
        return config.updates_enabled() and self.self_update_capable()

    def _due(self) -> bool:
        last = float(self.state.update_last_check or 0)
        return (time.time() - last) >= config.CHECK_INTERVAL_SECONDS

    def maybe_start(self, force: bool = False) -> None:
        """Kick off a check unless one is running, gated, or recently done."""
        if self._thread and self._thread.is_alive():
            return
        if not self._allowed(force):
            return
        if not force and not self._due():
            return
        if force:
            self._dismissed = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- the worker ------------------------------------------------------ #
    #: Give the network check this long, total. Sockets have their own
    #: timeouts, but on Android the whole stack has been seen to wedge in
    #: ways none of them cover — the UI must still conclude.
    CHECK_DEADLINE = 25.0

    def _checked_with_deadline(self):
        """Run the checker in a disposable thread with a hard deadline.

        If it wedges (seen on-device: 'Checking…' forever), we abandon the
        zombie — self._thread is THIS watchdog, which exits, so the player's
        next manual check starts fresh instead of being blocked by a corpse.
        """
        box = {}

        def work():
            try:
                box["info"] = checker.check_for_update(__version__)
            except Exception as exc:  # noqa: BLE001
                box["exc"] = exc

        t = threading.Thread(target=work, daemon=True,
                             name="kanjire-update-check")
        t.start()
        t.join(self.CHECK_DEADLINE)
        if t.is_alive():
            checker._debug(f"check STILL RUNNING after "
                           f"{self.CHECK_DEADLINE:.0f}s — abandoned")
            raise TimeoutError(
                f"update check timed out after {self.CHECK_DEADLINE:.0f}s")
        if "exc" in box:
            raise box["exc"]
        return box.get("info")

    def _run(self) -> None:
        # NOTHING may escape this thread: an uncaught exception here used to
        # kill it silently and leave the UI on "Checking…" forever (exactly
        # what happened on Android when PyNaCl was missing from the bundle).
        try:
            self.status = CHECKING
            self.error = None
            checker._debug(f"frozen={applier.is_frozen()} "
                           f"capable={self.self_update_capable()}")
            info = self._checked_with_deadline()
            self.state.set_update_last_check(time.time())
            if info is None:
                self.status = UP_TO_DATE
                return
            self.info = info
            self.status = DOWNLOADING
            self.progress = (0, info.size)
            self.staged = applier.stage(info, progress=self._on_progress)
            self.status = READY
        except Exception as exc:  # noqa: BLE001 — surface as a benign banner state
            checker._debug(f"update check crashed: {type(exc).__name__}: {exc}")
            self.error = str(exc)
            self.status = ERROR

    def _on_progress(self, done: int, total: int) -> None:
        self.progress = (done, total)

    # -- UI queries / actions ------------------------------------------- #
    @property
    def banner_visible(self) -> bool:
        return self.status == READY and self.info is not None and not self._dismissed

    def can_apply(self) -> bool:
        if self.status != READY or self.staged is None:
            return False
        if str(self.staged).lower().endswith(".apk"):
            return True   # the system installer applies it, no folder swap
        return applier.can_self_update()

    def apply(self) -> bool:
        """Launch the swap helper. Returns True if the app should now exit."""
        if not (self.status == READY and self.staged is not None):
            return False
        applier.apply_and_restart(self.staged)
        return True

    def dismiss(self) -> None:
        self._dismissed = True
