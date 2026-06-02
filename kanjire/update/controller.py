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
    def _allowed(self, force: bool) -> bool:
        """Auto-checks only happen in real (frozen) installs; a test override
        env lets us exercise the flow from a dev run."""
        if not config.updates_enabled():
            return False
        if force:
            return True
        if applier.is_frozen() or os.environ.get("KANJIRE_UPDATE_TEST"):
            return True
        return False

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
    def _run(self) -> None:
        self.status = CHECKING
        self.error = None
        info = checker.check_for_update(__version__)
        self.state.set_update_last_check(time.time())
        if info is None:
            self.status = UP_TO_DATE
            return
        self.info = info
        self.status = DOWNLOADING
        self.progress = (0, info.size)
        try:
            self.staged = applier.stage(info, progress=self._on_progress)
            self.status = READY
        except Exception as exc:  # noqa: BLE001 — surface as a benign banner state
            self.error = str(exc)
            self.status = ERROR

    def _on_progress(self, done: int, total: int) -> None:
        self.progress = (done, total)

    # -- UI queries / actions ------------------------------------------- #
    @property
    def banner_visible(self) -> bool:
        return self.status == READY and self.info is not None and not self._dismissed

    def can_apply(self) -> bool:
        return self.status == READY and self.staged is not None and applier.can_self_update()

    def apply(self) -> bool:
        """Launch the swap helper. Returns True if the app should now exit."""
        if not (self.status == READY and self.staged is not None):
            return False
        applier.apply_and_restart(self.staged)
        return True

    def dismiss(self) -> None:
        self._dismissed = True
