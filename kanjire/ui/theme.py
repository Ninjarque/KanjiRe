"""Theme palettes + the live colour constants every other module reads.

The rest of the UI imports this module with ``from kanjire.ui import theme``
and reads ``theme.BG``, ``theme.TEXT`` etc. as normal attributes, so calling
:func:`apply_palette` swaps the whole look-and-feel - no need to re-import
anywhere; scenes just need to be rebuilt (``app.go_menu()`` etc.) to pick
the new values up for already-constructed widgets.

Each palette is a dict of the same colour tokens. Add a new palette by
appending to :data:`PALETTES`; it shows up automatically in the Settings
THEME row.
"""
from __future__ import annotations

Color = tuple[int, int, int]


PALETTES: dict[str, dict[str, object]] = {
    # --- Charcoal: warm neutral dark grey, gentle accents (default) ---- #
    "Charcoal": dict(
        BG=(20, 21, 24),
        PANEL=(42, 44, 50),
        PANEL_HI=(60, 63, 71),
        TEXT=(244, 246, 250),
        MUTED=(172, 176, 186),
        DIM=(108, 112, 124),
        ACCENT=(95, 175, 235),
        GOLD=(230, 195, 100),
        SUCCESS=(105, 205, 150),
        DANGER=(225, 100, 115),
        FACE_KANJI=(85, 185, 230),
        FACE_READING=(125, 215, 150),
        FACE_ROMAJI=(235, 200, 95),
        FACE_MEANING=(215, 125, 185),
    ),
    # --- Midnight: very dark blue, cooler tones, slightly more contrast --
    "Midnight": dict(
        BG=(12, 14, 22),
        PANEL=(30, 34, 50),
        PANEL_HI=(48, 53, 75),
        TEXT=(238, 242, 252),
        MUTED=(160, 170, 200),
        DIM=(98, 108, 142),
        ACCENT=(115, 190, 255),
        GOLD=(240, 200, 90),
        SUCCESS=(110, 220, 165),
        DANGER=(235, 100, 130),
        FACE_KANJI=(115, 195, 255),
        FACE_READING=(135, 230, 160),
        FACE_ROMAJI=(245, 205, 90),
        FACE_MEANING=(225, 135, 200),
    ),
    # --- Sumi: nearly-black, ink-on-paper feel, restrained accents ----- #
    "Sumi": dict(
        BG=(10, 11, 13),
        PANEL=(32, 33, 36),
        PANEL_HI=(54, 56, 62),
        TEXT=(248, 248, 246),
        MUTED=(170, 170, 168),
        DIM=(108, 108, 110),
        ACCENT=(170, 200, 230),
        GOLD=(225, 195, 120),
        SUCCESS=(150, 210, 165),
        DANGER=(215, 130, 135),
        FACE_KANJI=(160, 200, 235),
        FACE_READING=(160, 215, 170),
        FACE_ROMAJI=(230, 205, 130),
        FACE_MEANING=(220, 160, 195),
    ),
    # --- Graphite: medium grey for more contrast / accessibility ------- #
    "Graphite": dict(
        BG=(28, 30, 34),
        PANEL=(54, 58, 66),
        PANEL_HI=(72, 76, 86),
        TEXT=(250, 250, 252),
        MUTED=(190, 194, 204),
        DIM=(130, 134, 146),
        ACCENT=(100, 185, 250),
        GOLD=(240, 200, 95),
        SUCCESS=(115, 215, 155),
        DANGER=(235, 110, 120),
        FACE_KANJI=(105, 195, 245),
        FACE_READING=(140, 225, 160),
        FACE_ROMAJI=(245, 205, 100),
        FACE_MEANING=(225, 135, 195),
    ),
    # --- Paper: light "ink on paper". The only light palette - widgets
    #     flip their lighten/darken direction via theme.is_light(). ------ #
    "Paper": dict(
        BG=(245, 242, 234),
        PANEL=(232, 227, 215),
        PANEL_HI=(214, 207, 192),
        TEXT=(34, 32, 28),
        MUTED=(96, 92, 84),
        DIM=(150, 145, 134),
        ACCENT=(40, 110, 175),
        GOLD=(168, 120, 20),
        SUCCESS=(40, 130, 80),
        DANGER=(185, 50, 55),
        FACE_KANJI=(40, 100, 165),
        FACE_READING=(35, 120, 70),
        FACE_ROMAJI=(160, 115, 15),
        FACE_MEANING=(150, 55, 120),
    ),
    # --- High Contrast: pure black, white text, vivid accents ---------- #
    "High Contrast": dict(
        BG=(0, 0, 0),
        PANEL=(20, 20, 20),
        PANEL_HI=(40, 40, 40),
        TEXT=(255, 255, 255),
        MUTED=(210, 210, 210),
        DIM=(150, 150, 150),
        ACCENT=(80, 190, 255),
        GOLD=(255, 215, 70),
        SUCCESS=(70, 230, 130),
        DANGER=(255, 80, 90),
        FACE_KANJI=(90, 200, 255),
        FACE_READING=(90, 240, 140),
        FACE_ROMAJI=(255, 220, 80),
        FACE_MEANING=(255, 120, 210),
    ),
    # --- Vivid: saturated, energetic accents on a deep indigo base ----- #
    "Vivid": dict(
        BG=(18, 16, 30),
        PANEL=(40, 34, 64),
        PANEL_HI=(60, 50, 92),
        TEXT=(250, 248, 255),
        MUTED=(190, 180, 215),
        DIM=(130, 120, 160),
        ACCENT=(120, 120, 255),
        GOLD=(255, 200, 60),
        SUCCESS=(60, 230, 160),
        DANGER=(255, 70, 120),
        FACE_KANJI=(70, 200, 255),
        FACE_READING=(120, 255, 110),
        FACE_ROMAJI=(255, 210, 70),
        FACE_MEANING=(255, 95, 200),
    ),
    # --- Monochrome: greyscale. FACE_* differ by LIGHTNESS, not hue, and
    #     DANGER is a dark grey so the mismatch flash reads as a strong
    #     darkening even without colour. ------------------------------- #
    "Monochrome": dict(
        BG=(16, 16, 16),
        PANEL=(46, 46, 46),
        PANEL_HI=(70, 70, 70),
        TEXT=(240, 240, 240),
        MUTED=(170, 170, 170),
        DIM=(110, 110, 110),
        ACCENT=(210, 210, 210),
        GOLD=(225, 225, 225),
        SUCCESS=(190, 190, 190),
        DANGER=(90, 90, 90),
        FACE_KANJI=(235, 235, 235),
        FACE_READING=(175, 175, 175),
        FACE_ROMAJI=(205, 205, 205),
        FACE_MEANING=(115, 115, 115),
    ),
}

DEFAULT_PALETTE = "Charcoal"
_current_palette: str = DEFAULT_PALETTE

# Initialise the module-level constants with the default palette. They get
# rebound in apply_palette() so every theme.BG / theme.TEXT etc. lookup sees
# the latest values without callers needing to re-import.
BG: Color = (0, 0, 0)
BG_TOP: Color = (0, 0, 0)
PANEL: Color = (0, 0, 0)
PANEL_HI: Color = (0, 0, 0)
TEXT: Color = (0, 0, 0)
MUTED: Color = (0, 0, 0)
DIM: Color = (0, 0, 0)
ACCENT: Color = (0, 0, 0)
GOLD: Color = (0, 0, 0)
SUCCESS: Color = (0, 0, 0)
DANGER: Color = (0, 0, 0)
FACE_COLORS: dict[str, Color] = {}
FACE_LABELS = {"kanji": "漢字", "reading": "かな", "romaji": "abc",
               "meaning": "EN"}


def apply_palette(name: str) -> None:
    """Switch the live theme. Subsequent ``theme.XYZ`` reads see new colours."""
    global BG, BG_TOP, PANEL, PANEL_HI, TEXT, MUTED, DIM
    global ACCENT, GOLD, SUCCESS, DANGER, FACE_COLORS, _current_palette
    p = PALETTES.get(name) or PALETTES[DEFAULT_PALETTE]
    _current_palette = name if name in PALETTES else DEFAULT_PALETTE
    BG = p["BG"]
    # No more gradient — keep BG_TOP equal so anything still calling a
    # gradient helper renders as a flat colour (fixes the visible banding).
    BG_TOP = p["BG"]
    PANEL = p["PANEL"]
    PANEL_HI = p["PANEL_HI"]
    TEXT = p["TEXT"]
    MUTED = p["MUTED"]
    DIM = p["DIM"]
    ACCENT = p["ACCENT"]
    GOLD = p["GOLD"]
    SUCCESS = p["SUCCESS"]
    DANGER = p["DANGER"]
    FACE_COLORS = {
        "kanji":   p["FACE_KANJI"],
        "reading": p["FACE_READING"],
        "romaji":  p["FACE_ROMAJI"],
        "meaning": p["FACE_MEANING"],
    }


def current_palette() -> str:
    return _current_palette


# Make the constants real at module import time.
apply_palette(DEFAULT_PALETTE)


# --------------------------------------------------------------------------- #
# Colour helpers (unchanged from before)
# --------------------------------------------------------------------------- #
def lerp(a: Color, b: Color, t: float) -> Color:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def darken(c: Color, t: float) -> Color:
    return lerp(c, (0, 0, 0), t)


def lighten(c: Color, t: float) -> Color:
    return lerp(c, (255, 255, 255), t)


def luminance(c: Color) -> float:
    """Perceptual-ish relative luminance in 0..1."""
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


def is_light() -> bool:
    """True when the active palette's background is a light surface.

    Reads the live module-level :data:`BG`, so it tracks :func:`apply_palette`.
    Widgets use this to flip their lighten/darken direction so the same code
    reads correctly under both dark and light palettes.
    """
    return luminance(BG) >= 0.5


def tint(c: Color, amount: float) -> Color:
    """Shift a colour *away from the background*: darken on light themes,
    lighten on dark ones. Use for hover / selected / raised surfaces."""
    return darken(c, amount) if is_light() else lighten(c, amount)


def readable_on(bg: Color, *, dark: Color = (20, 21, 24),
                light: Color | None = None) -> Color:
    """Pick a text colour with adequate contrast on *bg*: a near-black on
    bright fills, the palette :data:`TEXT` (or *light* override) on dark ones."""
    l = TEXT if light is None else light
    return dark if luminance(bg) >= 0.55 else l


def with_alpha(c: Color, a) -> tuple[int, int, int, int]:
    alpha = int(a * 255) if isinstance(a, float) else int(a)
    return (c[0], c[1], c[2], max(0, min(255, alpha)))
