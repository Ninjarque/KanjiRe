"""Sound effects (synthesized) and text-to-speech (SAPI via pyttsx3).

The synth SFX live in-process - no asset files needed. TTS runs in a dedicated
background thread so engine ``runAndWait`` calls never block the game loop. If
pyttsx3 or a matching voice isn't installed, every speech call becomes a no-op.
"""
from __future__ import annotations

# Initialise comtypes *before* pyglet's media subsystem touches COM. The two
# prefer different apartment modes; whichever calls CoInitializeEx first wins,
# and the loser then raises ``RPC_E_CHANGED_MODE`` when it tries.  Importing
# comtypes.client here at module top makes SAPI Speech work reliably no matter
# what order callers create Audio() / pyglet windows in.
try:
    import comtypes.client as _comtypes_client  # noqa: F401
except Exception:
    _comtypes_client = None  # type: ignore[assignment]

import pyglet
from pyglet.media import StaticSource
from pyglet.media.synthesis import (
    ADSREnvelope,
    LinearDecayEnvelope,
    Sawtooth,
    Sine,
    Triangle,
)


# --------------------------------------------------------------------------- #
# SFX
# --------------------------------------------------------------------------- #
class SFX:
    """Short synthesized sound effects."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        sel_env = LinearDecayEnvelope(peak=0.30)
        mismatch_env = LinearDecayEnvelope(peak=0.45)
        match_env = ADSREnvelope(attack=0.005, decay=0.04, release=0.18, sustain_amplitude=0.5)

        self._sources = {
            "select": StaticSource(Triangle(duration=0.07, frequency=540,
                                            envelope=sel_env)),
            "match": StaticSource(Sine(duration=0.22, frequency=880,
                                       envelope=match_env)),
            "match_hi": StaticSource(Sine(duration=0.22, frequency=1320,
                                          envelope=match_env)),
            "mismatch": StaticSource(Sawtooth(duration=0.22, frequency=180,
                                              envelope=mismatch_env)),
            "round_clear": StaticSource(Sine(duration=0.28, frequency=660,
                                             envelope=match_env)),
            # Survival feedback: a warm heart chime, a bright coin ping, and a
            # low damage buzz.
            "heart": StaticSource(Sine(duration=0.32, frequency=1040,
                                       envelope=match_env)),
            "coin": StaticSource(Triangle(duration=0.16, frequency=1568,
                                          envelope=match_env)),
            "damage": StaticSource(Sawtooth(duration=0.30, frequency=110,
                                            envelope=mismatch_env)),
        }
        self._players: list[pyglet.media.Player] = []

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        src = self._sources.get(name)
        if src is None:
            return
        try:
            p = pyglet.media.Player()
            p.queue(src)
            p.play()
            self._players.append(p)
            # prune finished players so the list does not grow unbounded
            self._players = [pl for pl in self._players if pl.playing]
        except Exception:
            pass

    def chord(self, names: list[str], spread: float = 0.05) -> None:
        """Stagger several SFX to make a quick arpeggio."""
        if not self.enabled:
            return
        for i, n in enumerate(names):
            pyglet.clock.schedule_once(lambda dt, n=n: self.play(n), i * spread)


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
_JP_MARKERS = ("japan", "haruka", "ayumi", "sayaka", "ichiro", "nanami")
_EN_MARKERS = ("english", "zira", "david", "mark", "hazel")

# SAPI Speak() flags.
_SVSFlagsAsync = 1            # don't block until speech finishes
_SVSFPurgeBeforeSpeak = 2     # cancel any pending / current speech first
_SPEAK_FLAGS = _SVSFlagsAsync | _SVSFPurgeBeforeSpeak


class Speech:
    """Direct SAPI 5 wrapper via comtypes.

    Every ``say_jp`` / ``say_en`` call goes straight to ``ISpVoice.Speak`` with
    ``Async | PurgeBeforeSpeak`` flags - so a fresh utterance **interrupts** the
    previous one cleanly and the player always hears feedback for the *latest*
    event. No queue, no background thread, no dropped events.

    ``available`` reports whether init succeeded; ``has_jp`` / ``has_en`` say
    whether the matching voice was found. Falls back to a no-op on systems
    without SAPI (non-Windows or no comtypes)."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.available = False
        self.has_jp = False
        self.has_en = False
        self._voice = None
        self._jp = None
        self._en = None
        self._current_voice = None
        if _comtypes_client is None:
            return
        try:
            self._voice = _comtypes_client.CreateObject("SAPI.SpVoice")
            voices = self._voice.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = (v.GetDescription() or "").lower()
                if self._jp is None and any(m in desc for m in _JP_MARKERS):
                    self._jp = v
                if self._en is None and any(m in desc for m in _EN_MARKERS):
                    self._en = v
            self.has_jp = self._jp is not None
            self.has_en = self._en is not None
            self.available = True
        except Exception:
            # SAPI / comtypes not available - speech is a no-op.
            self.available = False

    # -- public -------------------------------------------------------- #
    def _speak(self, text: str, voice) -> None:
        if not (self.enabled and self._voice is not None and voice is not None and text):
            return
        try:
            # Setting Voice is relatively expensive; skip if unchanged.
            if voice is not self._current_voice:
                self._voice.Voice = voice
                self._current_voice = voice
            self._voice.Speak(text, _SPEAK_FLAGS)
        except Exception:
            pass

    def say_jp(self, text: str) -> None:
        self._speak(text, self._jp)

    def say_en(self, text: str) -> None:
        self._speak(text, self._en)

    def shutdown(self) -> None:
        # Purge any in-flight speech so it doesn't outlive the window.
        try:
            if self._voice is not None:
                self._voice.Speak("", _SVSFPurgeBeforeSpeak)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Combined facade
# --------------------------------------------------------------------------- #
class Audio:
    """One object the game scenes use for both SFX and TTS."""

    def __init__(self, muted: bool = False) -> None:
        self.muted = muted
        self.sfx = SFX(enabled=not muted)
        self.speech = Speech(enabled=not muted)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.sfx.enabled = not muted
        self.speech.enabled = not muted

    def toggle_mute(self) -> bool:
        self.set_muted(not self.muted)
        return self.muted

    def shutdown(self) -> None:
        self.speech.shutdown()
