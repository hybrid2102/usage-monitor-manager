"""Brand colour presets and the derived bar / icon palette.

A port of ``usage-monitor-config-editor``'s ``main_window`` colour helpers
(PLAN.md section 1.5), without Qt: one primary colour produces ``bar_fg`` plus
the ``fg`` channel of ``icon_light`` and ``icon_dark``, blending 65 % toward
white and black respectively so the tray icon stays readable on both a light
and a dark taskbar.

Only the ``fg`` channel is derived: ``fg_half``, ``fg_dim`` and ``fg_warn``
keep whatever the user has (and get the upstream default when the file does
not set them), because they encode meaning - half-filled bars and the warning
red - rather than brand identity.
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
BLEND_RATIO = 0.65

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


def palette_from_primary(primary: str) -> dict[str, str]:
    """``{bar, light, dark}`` from one brand colour."""
    normalized = normalize_hex(primary)
    return {
        "bar": normalized,
        "light": blend_hex(normalized, DEFAULT_LIGHT, BLEND_RATIO),
        "dark": blend_hex(normalized, DEFAULT_DARK, BLEND_RATIO),
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
    """One ``icon_light`` / ``icon_dark`` object with only ``fg`` replaced."""
    defaults: dict[str, list[int]] = dict(schema_for(kind).fields[key].default)
    current: Any = data.get(key)
    from .store import as_json_object

    block: JsonDict = as_json_object(current) or {}
    for channel, value in defaults.items():
        block.setdefault(channel, list(value))
    block["fg"] = rgba_from_hex(fg)
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
