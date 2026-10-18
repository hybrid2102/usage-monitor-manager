"""Brand colour presets and the derived bar / icon palette.

One primary colour produces ``bar_fg`` and vivid tray colours for dark and
light taskbars. A small initial blend preserves brand identity; extra shading
is applied only when needed for contrast against the reference taskbar.

The normal, half and dim channels share the brand colour, retaining the half
and dim alpha values so bar meaning is preserved. Warning colours stay intact.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..model import JsonDict
from .schema import schema_for

__all__ = [
    "BRAND_PRESETS",
    "DEFAULT_DARK",
    "DEFAULT_LIGHT",
    "apply_palette",
    "blend_hex",
    "brand_preset_for",
    "normalize_hex",
    "palette_from_primary",
    "readable_text_color",
    "rgba_from_hex",
]

DEFAULT_LIGHT = "#FFFFFF"
DEFAULT_DARK = "#000000"
LIGHT_BLEND_RATIO = 0.15
DARK_BLEND_RATIO = 0.25
MIN_ICON_CONTRAST = 4.5

BRAND_PRESETS: tuple[tuple[str, str], ...] = (
    ("Claude", "#D97757"),
    ("ChatGPT", "#FFFFFF"),
    ("Codex", "#10A37F"),
    ("Copilot", "#8B5CF6"),
    ("Gemini", "#4285F4"),
    ("Perplexity", "#20B2AA"),
    ("Grok", "#000000"),
    ("DeepSeek", "#4D6BFE"),
    ("Mistral", "#FF7000"),
)
"""The config editor's preset list, order included."""


def normalize_hex(value: str) -> str:
    """Return ``#RRGGBB`` upper case, accepting the ``#RGB`` short form.

    Raises:
        ValueError: with the config editor's own Italian message.
    """
    text = str(value).strip()
    if not text.startswith("#") or len(text) not in (4, 7):
        raise ValueError("Usa un colore esadecimale come #D97757.")
    digits = text[1:]
    if any(char not in "0123456789abcdefABCDEF" for char in digits):
        raise ValueError("Usa un colore esadecimale come #D97757.")
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    return "#" + digits.upper()


def _channels(value: str) -> tuple[int, int, int]:
    normalized = normalize_hex(value)
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def blend_hex(first: str, second: str, second_ratio: float) -> str:
    """Blend two colours, ``second_ratio`` being how much of ``second`` wins."""
    if not 0 <= second_ratio <= 1:
        raise ValueError("Il rapporto di fusione deve essere compreso tra 0 e 1.")
    mixed = [
        round(left * (1 - second_ratio) + right * second_ratio)
        for left, right in zip(_channels(first), _channels(second), strict=True)
    ]
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _luminance(color: str) -> float:
    channels = [channel / 255 for channel in _channels(color)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_color(primary: str, toward: str, ratio: float, background: str) -> str:
    """Keep the initial colour unless it needs more contrast to be readable."""
    background_luminance = _luminance(background)

    def readable(color: str) -> bool:
        luminance = _luminance(color)
        contrast = (max(luminance, background_luminance) + 0.05) / (min(luminance, background_luminance) + 0.05)
        return contrast >= MIN_ICON_CONTRAST

    initial = blend_hex(primary, toward, ratio)
    if readable(initial):
        return initial
    low, high = ratio, 1.0
    for _ in range(20):
        midpoint = (low + high) / 2
        if readable(blend_hex(primary, toward, midpoint)):
            high = midpoint
        else:
            low = midpoint
    return blend_hex(primary, toward, high)


def palette_from_primary(primary: str) -> dict[str, str]:
    """``{bar, light, dark}`` from one brand colour."""
    normalized = normalize_hex(primary)
    return {
        "bar": normalized,
        "light": _contrast_color(normalized, DEFAULT_LIGHT, LIGHT_BLEND_RATIO, "#202020"),
        "dark": _contrast_color(normalized, DEFAULT_DARK, DARK_BLEND_RATIO, DEFAULT_LIGHT),
    }


def rgba_from_hex(value: str, alpha: int = 255) -> list[int]:
    """``#RRGGBB`` to the monitors' ``[R, G, B, A]`` representation."""
    red, green, blue = _channels(value)
    return [red, green, blue, alpha]


def readable_text_color(background: str) -> str:
    """Black or white text, whichever reads better on ``background``."""
    red, green, blue = _channels(background)
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#111111" if luminance > 150 else "#FFFFFF"


def brand_preset_for(label: str) -> tuple[str, str] | None:
    """The most recognizable preset whose name appears in ``label``."""
    needle = str(label).casefold()
    for brand, color in BRAND_PRESETS:
        if brand.casefold() in needle:
            return brand, color
    return None


def _icon_block(data: Mapping[str, Any], key: str, kind: str, fg: str) -> JsonDict:
    """Tint the normal channels, preserving bar opacity and warning colours."""
    defaults: dict[str, list[int]] = dict(schema_for(kind).fields[key].default)
    current: Any = data.get(key)
    from .store import as_json_array, as_json_object

    block: JsonDict = as_json_object(current) or {}
    for channel, value in defaults.items():
        block.setdefault(channel, list(value))
    block["fg"] = rgba_from_hex(fg)
    for channel in ("fg_half", "fg_dim"):
        current_color = as_json_array(block[channel])
        alpha = current_color[3] if current_color is not None and len(current_color) == 4 else defaults[channel][3]
        block[channel] = rgba_from_hex(fg, alpha)
    return block


def apply_palette(data: Mapping[str, Any], primary: str, kind: str) -> JsonDict:
    """Return ``data`` with ``bar_fg``, ``icon_light`` and ``icon_dark`` set.

    Everything else - including the other popup colours and the unknown keys -
    is left exactly as it was.
    """
    palette = palette_from_primary(primary)
    result: JsonDict = dict(data)
    result["bar_fg"] = palette["bar"]
    result["icon_light"] = _icon_block(data, "icon_light", kind, palette["light"])
    result["icon_dark"] = _icon_block(data, "icon_dark", kind, palette["dark"])
    return result
