"""Dated settings history, diffing and export/import.

Phase 0: stub. Implemented in phase 5 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["export_profile", "record", "restore"]


def record(settings_path: Path) -> Path:
    """Archive a dated copy of a settings file before overwriting it."""
    raise NotImplementedError


def restore(settings_path: Path, stamp: str) -> bool:
    """Restore an archived settings snapshot."""
    raise NotImplementedError


def export_profile(destination: Path) -> Path:
    """Export the settings of every instance as one portable JSON file."""
    raise NotImplementedError
