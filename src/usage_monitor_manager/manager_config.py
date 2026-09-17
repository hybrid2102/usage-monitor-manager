"""Persistent manager state in %LOCALAPPDATA%/UsageMonitorManager.

Phase 0: stub. Implemented in phase 4 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["config_path", "load", "save"]


def config_path() -> Path:
    """Path of ``manager-config.json``."""
    raise NotImplementedError


def load() -> dict[str, Any]:
    """Load the manager state, returning defaults when the file is missing."""
    raise NotImplementedError


def save(state: dict[str, Any]) -> None:
    """Write the manager state atomically."""
    raise NotImplementedError
