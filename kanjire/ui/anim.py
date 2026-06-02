"""A tiny tween/easing system for smooth card animations.

Each :class:`Tween` drives one attribute of one object from its current value to
a target over a duration, optionally after a delay and with an easing curve. An
:class:`Animator` owns the active tweens and advances them every frame.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Easing curves (input/output in 0..1)
# --------------------------------------------------------------------------- #
def linear(t: float) -> float:
    return t


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


def ease_out_back(t: float, s: float = 1.70158) -> float:
    t -= 1.0
    return t * t * ((s + 1) * t + s) + 1.0


def ease_out_elastic(t: float) -> float:
    if t in (0.0, 1.0):
        return t
    import math

    p = 0.3
    return 2 ** (-10 * t) * math.sin((t - p / 4) * (2 * math.pi) / p) + 1.0


@dataclass
class Tween:
    obj: object
    attr: str
    end: float
    duration: float
    ease: Callable[[float], float] = ease_out_cubic
    delay: float = 0.0
    on_done: Callable[[], None] | None = None

    _start: float = field(default=0.0, init=False)
    _elapsed: float = field(default=0.0, init=False)
    _started: bool = field(default=False, init=False)
    done: bool = field(default=False, init=False)

    def _begin(self) -> None:
        self._start = getattr(self.obj, self.attr)
        self._started = True

    def update(self, dt: float) -> None:
        if self.done:
            return
        if self.delay > 0:
            self.delay -= dt
            if self.delay > 0:
                return
            dt = -self.delay  # carry the overshoot into the first frame
            self.delay = 0.0
        if not self._started:
            self._begin()
        self._elapsed += dt
        t = 1.0 if self.duration <= 0 else min(1.0, self._elapsed / self.duration)
        value = self._start + (self.end - self._start) * self.ease(t)
        setattr(self.obj, self.attr, value)
        if t >= 1.0:
            setattr(self.obj, self.attr, self.end)
            self.done = True
            if self.on_done:
                self.on_done()


@dataclass
class _Delayed:
    time_left: float
    fn: Callable[[], None]
    done: bool = False


class Animator:
    def __init__(self) -> None:
        self._tweens: list[Tween] = []
        self._delayed: list[_Delayed] = []

    def add(self, tween: Tween) -> Tween:
        self._tweens.append(tween)
        return tween

    def to(self, obj, attr, end, duration, **kw) -> Tween:
        return self.add(Tween(obj, attr, end, duration, **kw))

    def after(self, delay: float, fn: Callable[[], None]) -> None:
        """Run *fn* once after *delay* seconds."""
        self._delayed.append(_Delayed(delay, fn))

    def clear(self) -> None:
        self._tweens.clear()
        self._delayed.clear()

    @property
    def busy(self) -> bool:
        return bool(self._tweens or self._delayed)

    def update(self, dt: float) -> None:
        for tw in self._tweens:
            tw.update(dt)
        if self._tweens:
            self._tweens = [t for t in self._tweens if not t.done]

        if self._delayed:
            for d in self._delayed:
                d.time_left -= dt
                if d.time_left <= 0:
                    d.done = True
                    d.fn()
            self._delayed = [d for d in self._delayed if not d.done]
