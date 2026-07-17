"""Cross-platform audio for the Kivy UI: synthesized SFX + TTS.

SFX: the same eight tones the pyglet UI synthesizes in-process are rendered
once to little WAV files in the user dir (pure-Python synth, no numpy) and
played through Kivy's SoundLoader — identical sound on all platforms.

TTS by platform:
* Android — the system TextToSpeech engine via pyjnius (Google TTS has
  excellent Japanese voices). QUEUE_FLUSH mirrors the desktop
  purge-before-speak behaviour: a new utterance interrupts the previous one.
* Windows — direct SAPI via comtypes (same approach as the pyglet UI).
* elsewhere — a clean no-op.
"""
from __future__ import annotations

import math
import os
import struct
import wave

from kanjire.paths import USER_DIR

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

_RATE = 22050


# --------------------------------------------------------------------------- #
# Tiny synth (mirrors pyglet.media.synthesis just enough)
# --------------------------------------------------------------------------- #
def _wave_sample(kind: str, freq: float, t: float) -> float:
    ph = (t * freq) % 1.0
    if kind == "sine":
        return math.sin(2 * math.pi * ph)
    if kind == "triangle":
        return 4 * abs(ph - 0.5) - 1
    return 2 * ph - 1  # sawtooth


def _envelope(name: str, t: float, dur: float, peak: float) -> float:
    if name == "decay":  # linear decay from peak to 0
        return peak * max(0.0, 1.0 - t / dur)
    # simple ADSR-ish: fast attack, decay to sustain, release at the end
    attack, decay, sustain = 0.005, 0.04, 0.5
    if t < attack:
        return t / attack
    if t < attack + decay:
        f = (t - attack) / decay
        return 1.0 + (sustain - 1.0) * f
    release_start = dur * 0.6
    if t > release_start:
        return sustain * max(0.0, 1.0 - (t - release_start) / (dur - release_start))
    return sustain


#: name -> (waveform, frequency, duration, envelope, peak)
_TONES = {
    "select":      ("triangle", 540, 0.07, "decay", 0.30),
    "match":       ("sine", 880, 0.22, "adsr", 1.0),
    "match_hi":    ("sine", 1320, 0.22, "adsr", 1.0),
    "mismatch":    ("sawtooth", 180, 0.22, "decay", 0.45),
    "round_clear": ("sine", 660, 0.28, "adsr", 1.0),
    "heart":       ("sine", 1040, 0.32, "adsr", 1.0),
    "coin":        ("triangle", 1568, 0.16, "adsr", 1.0),
    "damage":      ("sawtooth", 110, 0.30, "decay", 0.45),
}


def _render(path: str, kind: str, freq: float, dur: float,
            env: str, peak: float) -> None:
    n = int(_RATE * dur)
    frames = bytearray()
    for i in range(n):
        t = i / _RATE
        v = _wave_sample(kind, freq, t) * _envelope(env, t, dur, peak) * 0.55
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))


class SFX:
    """The eight game tones, rendered once and played via SoundLoader."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._sounds: dict[str, object] = {}
        try:
            from kivy.core.audio import SoundLoader
        except Exception:
            return
        cache = USER_DIR / "sfx"
        cache.mkdir(parents=True, exist_ok=True)
        for name, (kind, freq, dur, env, peak) in _TONES.items():
            path = cache / f"{name}.wav"
            try:
                if not path.exists():
                    _render(str(path), kind, freq, dur, env, peak)
                snd = SoundLoader.load(str(path))
                if snd is not None:
                    self._sounds[name] = snd
            except Exception:
                continue

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        snd = self._sounds.get(name)
        if snd is None:
            return
        try:
            snd.stop()  # restart if still ringing
            snd.play()
        except Exception:
            pass

    def chord(self, names: list[str], spread: float = 0.05) -> None:
        if not self.enabled:
            return
        from kivy.clock import Clock
        for i, n in enumerate(names):
            Clock.schedule_once(lambda _dt, n=n: self.play(n), i * spread)


# --------------------------------------------------------------------------- #
# Speech
# --------------------------------------------------------------------------- #
class Speech:
    """say_jp / say_en with interrupt semantics, per platform."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.available = False
        self._impl = None
        if IS_ANDROID:
            self._impl = _AndroidTTS()
        elif os.name == "nt":
            self._impl = _SapiTTS()
        if self._impl is not None:
            self.available = self._impl.available

    def say_jp(self, text: str) -> None:
        if self.enabled and self._impl is not None and text:
            self._impl.say(text, "jp")

    def say_en(self, text: str) -> None:
        if self.enabled and self._impl is not None and text:
            self._impl.say(text, "en")

    def is_speaking(self) -> bool:
        """True while an utterance is still playing (best effort)."""
        if self._impl is None:
            return False
        try:
            return bool(self._impl.is_speaking())
        except Exception:
            return False

    def shutdown(self) -> None:
        if self._impl is not None:
            self._impl.shutdown()


class _AndroidTTS:
    def __init__(self) -> None:
        self.available = False
        try:
            from jnius import autoclass
            self._TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            self._Locale = autoclass("java.util.Locale")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            # None listener: init is async; speaks silently no-op until ready.
            self._tts = self._TextToSpeech(activity, None)
            self._lang = None
            self.available = True
        except Exception:
            self._tts = None

    def say(self, text: str, lang: str) -> None:
        if self._tts is None:
            return
        try:
            want = (self._Locale.JAPANESE if lang == "jp"
                    else self._Locale.ENGLISH)
            if lang != self._lang:
                self._tts.setLanguage(want)
                self._lang = lang
            self._tts.speak(text, self._TextToSpeech.QUEUE_FLUSH, None)
        except Exception:
            pass

    def is_speaking(self) -> bool:
        try:
            return bool(self._tts is not None and self._tts.isSpeaking())
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            if self._tts is not None:
                self._tts.shutdown()
        except Exception:
            pass


class _SapiTTS:
    """Windows SAPI, the same voice-picking logic as kanjire.ui.audio."""

    _JP_MARKERS = ("japanese", "ja-jp", "haruka", "ayumi", "ichiro", "sayaka")
    _EN_MARKERS = ("english", "en-us", "en-gb", "zira", "david", "mark",
                   "hazel")

    def __init__(self) -> None:
        self.available = False
        self._voice = None
        self._jp = None
        self._en = None
        self._current = None
        try:
            import comtypes.client as cc
            self._voice = cc.CreateObject("SAPI.SpVoice")
            voices = self._voice.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = (v.GetDescription() or "").lower()
                if self._jp is None and any(m in desc for m in self._JP_MARKERS):
                    self._jp = v
                if self._en is None and any(m in desc for m in self._EN_MARKERS):
                    self._en = v
            self.available = True
        except Exception:
            self._voice = None

    def say(self, text: str, lang: str) -> None:
        voice = self._jp if lang == "jp" else self._en
        if self._voice is None or voice is None:
            return
        try:
            if voice is not self._current:
                self._voice.Voice = voice
                self._current = voice
            self._voice.Speak(text, 3)  # SVSFlagsAsync | SVSFPurgeBeforeSpeak
        except Exception:
            pass

    def is_speaking(self) -> bool:
        try:
            # SpeechRunState: 1 = done, 2 = speaking.
            return (self._voice is not None
                    and self._voice.Status.RunningState == 2)
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            if self._voice is not None:
                self._voice.Speak("", 2)  # purge
        except Exception:
            pass


class Audio:
    """One object the screens use for both SFX and TTS."""

    def __init__(self, muted: bool = False) -> None:
        self.muted = muted
        self.sfx = SFX(enabled=not muted)
        self.speech = Speech(enabled=not muted)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.sfx.enabled = not muted
        self.speech.enabled = not muted

    def shutdown(self) -> None:
        self.speech.shutdown()
