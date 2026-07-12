"""Tiny JSON-backed store for high scores and last-used settings."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from kanjire.paths import USER_STATE_PATH

#: Streak mercy: one freeze earned per this many consecutive days...
STREAK_FREEZE_EVERY = 7
#: ...banked up to this many. A freeze silently covers one missed day.
STREAK_FREEZE_BANK = 3


class UserState:
    def __init__(self, path: Path = USER_STATE_PATH) -> None:
        self.path = path
        self.data: dict = {"high_scores": {}, "last": {}}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.data.setdefault("high_scores", {})
        self.data.setdefault("last", {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def high_score(self, mode: str) -> int:
        return int(self.data["high_scores"].get(mode, 0))

    def record_score(self, mode: str, score: int) -> bool:
        """Store *score* if it beats the record. Returns True if it was a record."""
        if score > self.high_score(mode):
            self.data["high_scores"][mode] = int(score)
            self.save()
            return True
        return False

    # ---- audio / speech settings ------------------------------------ #
    @property
    def muted(self) -> bool:
        return bool(self.data.get("settings", {}).get("muted", False))

    def set_muted(self, muted: bool) -> None:
        self.data.setdefault("settings", {})["muted"] = bool(muted)
        self.save()

    def _audio_setting(self, key: str, default: bool) -> bool:
        return bool(self.data.get("settings", {}).get(key, default))

    def set_audio_setting(self, key: str, value: bool) -> None:
        self.data.setdefault("settings", {})[key] = bool(value)
        self.save()

    @property
    def tts_on_select(self) -> bool:    return self._audio_setting("tts_on_select", False)
    @property
    def tts_on_match(self) -> bool:     return self._audio_setting("tts_on_match", True)
    @property
    def tts_on_mismatch(self) -> bool:  return self._audio_setting("tts_on_mismatch", True)

    # ---- language / locale ------------------------------------------ #
    @property
    def locale(self) -> str:
        return str(self.data.get("settings", {}).get("locale", "en"))

    def set_locale(self, loc: str) -> None:
        self.data.setdefault("settings", {})["locale"] = loc
        self.save()

    # ---- auto-update bookkeeping ------------------------------------ #
    @property
    def update_last_check(self) -> float:
        """Unix timestamp of the last background update check (0 if never)."""
        return float(self.data.get("settings", {}).get("update_last_check", 0) or 0)

    def set_update_last_check(self, ts: float) -> None:
        self.data.setdefault("settings", {})["update_last_check"] = float(ts)
        self.save()

    # ---- visual theme palette --------------------------------------- #
    @property
    def palette(self) -> str:
        return str(self.data.get("settings", {}).get("palette", "Charcoal"))

    def set_palette(self, name: str) -> None:
        self.data.setdefault("settings", {})["palette"] = name
        self.save()

    # ---- last-active mode ------------------------------------------- #
    @property
    def last_mode(self) -> str | None:
        """Name of the mode the player had selected when they last quit."""
        v = self.data.get("settings", {}).get("last_mode")
        return str(v) if v else None

    def set_last_mode(self, mode: str) -> None:
        self.data.setdefault("settings", {})["last_mode"] = mode
        self.save()

    # -- per-mode last-used settings ----------------------------------- #
    def last_for_mode(self, mode: str) -> dict | None:
        return self.data.get("last_per_mode", {}).get(mode)

    def set_last_for_mode(self, mode: str, settings: dict) -> None:
        """Persist the menu's current toggle state under ``mode``.

        Called on every toggle change so re-opening the app picks up exactly
        where the player left off in each mode."""
        self.data.setdefault("last_per_mode", {})[mode] = settings
        self.save()

    # -- daily streak (with mercy) -------------------------------------- #
    def streak_status(self, today: date | None = None) -> dict:
        """Current streak as the player should see it: ``count`` (0 if it
        broke), ``freezes`` banked, ``done_today``. Never mutates state.

        Framing rule (docs/ROADMAP.md): a missed day *silently* consumes a
        banked freeze; only when no freeze can cover the gap does the count
        read 0. There is no "you lost your streak" moment - it just shows the
        honest number.
        """
        today = today or date.today()
        s = self.data.get("settings", {})
        count = int(s.get("streak_count", 0) or 0)
        freezes = int(s.get("streak_freezes", 0) or 0)
        last = s.get("streak_day")
        if not last or not count:
            return {"count": 0, "freezes": freezes, "done_today": False}
        try:
            last_d = date.fromisoformat(last)
        except ValueError:
            return {"count": 0, "freezes": freezes, "done_today": False}
        gap = (today - last_d).days
        if gap <= 0:
            return {"count": count, "freezes": freezes, "done_today": True}
        if gap == 1:
            return {"count": count, "freezes": freezes, "done_today": False}
        missed = gap - 1
        if missed <= freezes:
            return {"count": count, "freezes": freezes, "done_today": False}
        return {"count": 0, "freezes": freezes, "done_today": False}

    def stamp_streak(self, today: date | None = None) -> dict:
        """Record that today's training happened. Applies freeze mercy for
        missed days, extends or restarts the count, banks earned freezes.
        Returns the new status dict (same shape as :meth:`streak_status`)."""
        today = today or date.today()
        s = self.data.setdefault("settings", {})
        count = int(s.get("streak_count", 0) or 0)
        freezes = int(s.get("streak_freezes", 0) or 0)
        last = s.get("streak_day")
        last_d = None
        if last:
            try:
                last_d = date.fromisoformat(last)
            except ValueError:
                last_d = None
        if last_d is None or count == 0:
            count = 1
        else:
            gap = (today - last_d).days
            if gap == 0:                    # already stamped today: no-op
                return {"count": count, "freezes": freezes, "done_today": True}
            elif gap == 1:
                count += 1
            else:
                missed = gap - 1
                if missed <= freezes:       # mercy: freezes cover the hole
                    freezes -= missed
                    count += 1
                else:
                    count = 1
        if count and count % STREAK_FREEZE_EVERY == 0:
            freezes = min(STREAK_FREEZE_BANK, freezes + 1)
        s["streak_count"] = count
        s["streak_freezes"] = freezes
        s["streak_day"] = today.isoformat()
        self.save()
        return {"count": count, "freezes": freezes, "done_today": True}

    # -- named presets (user-saved custom modes) ----------------------- #
    @property
    def presets(self) -> list[dict]:
        return list(self.data.get("presets", []))

    def save_preset(self, preset: dict) -> None:
        """Insert (or replace by name) a custom-mode preset."""
        name = (preset.get("name") or "").strip()
        if not name:
            return
        presets = self.data.setdefault("presets", [])
        presets[:] = [p for p in presets if p.get("name") != name]
        presets.append(preset)
        self.save()

    def delete_preset(self, name: str) -> bool:
        presets = self.data.get("presets", [])
        before = len(presets)
        presets[:] = [p for p in presets if p.get("name") != name]
        if len(presets) != before:
            self.save()
            return True
        return False
