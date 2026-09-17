"""Settings load/validate/atomic save, preserving unknown keys.

Phase 0: stub. Implemented in phase 5 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["load_settings", "save_settings", "validate"]


def load_settings(path: Path) -> dict[str, Any]:
    """Read a settings file as ``utf-8-sig``."""
    raise NotImplementedError


def validate(schema_id: str, data: dict[str, Any]) -> list[str]:
    """Port of the monitors' ``_validate()``; returns human readable problems."""
    raise NotImplementedError


def save_settings(path: Path, data: dict[str, Any]) -> None:
    """Write utf-8 with ``\n`` newlines via tmp + replace, keeping a ``.bak``."""
    raise NotImplementedError
