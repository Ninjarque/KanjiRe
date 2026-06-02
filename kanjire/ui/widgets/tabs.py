"""A tiny top tab bar shared by the menu, stats and (eventually) any other
top-level scenes. Each tab is a small :class:`Button` with a selected state.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from kanjire.ui import theme
from kanjire.ui.widgets.button import Button


class TabBar:
    def __init__(
        self,
        items: Sequence[tuple[str, Callable[[], None]]],
        batch,
        bg_group,
        text_group,
        *,
        accent: theme.Color | None = None,
        font_size: int = 14,
    ) -> None:
        self.items = list(items)
        accent = accent if accent is not None else theme.ACCENT
        self.buttons: list[Button] = [
            Button(label, cb, batch, bg_group, text_group,
                   accent=accent, font_size=font_size)
            for label, cb in self.items
        ]
        self.active_index = 0
        if self.buttons:
            self.buttons[0].set_selected(True)

    # ---------------------------------------------------------------- #
    def set_active(self, label_or_index) -> None:
        if isinstance(label_or_index, int):
            idx = label_or_index
        else:
            idx = next(
                (i for i, (l, _) in enumerate(self.items) if l == label_or_index),
                0,
            )
        self.active_index = idx
        for i, b in enumerate(self.buttons):
            b.set_selected(i == idx)

    def set_scale(self, s: float) -> None:
        for b in self.buttons:
            b.set_scale(s)

    def set_rect(self, x: float, y: float, w: float, h: float, gap: float = 8.0) -> None:
        n = max(1, len(self.buttons))
        bw = (w - (n - 1) * gap) / n
        for i, b in enumerate(self.buttons):
            b.set_rect(x + i * (bw + gap), y, bw, h)

    # ---------------------------------------------------------------- #
    def on_mouse_press(self, x: float, y: float) -> bool:
        for b in self.buttons:
            if b.enabled and b.contains(x, y):
                b.click()
                return True
        return False

    def on_mouse_motion(self, x: float, y: float) -> None:
        for b in self.buttons:
            b.set_hover(b.enabled and b.contains(x, y))

    def delete(self) -> None:
        for b in self.buttons:
            b.delete()
