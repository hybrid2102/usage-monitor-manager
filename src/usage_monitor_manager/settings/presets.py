"""Brand colour palettes and derived icon/bar colours.

Phase 0: stub. Implemented in phase 5 of PLAN.md section 7.
"""

from __future__ import annotations

__all__ = ["BRAND_COLORS", "palette_for"]

BRAND_COLORS: dict[str, str] = {}


def palette_for(brand: str) -> dict[str, object]:
    """Derive ``bar_fg`` and light/dark icon colours from a brand colour."""
    raise NotImplementedError
