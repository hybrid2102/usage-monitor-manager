"""Linux primitives (flock singleton, XDG paths): placeholder for a future phase.

Phase 0: stub. Implemented in phase 8 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["file_version"]


def file_version(path: Path) -> str | None:
    """Not applicable on Linux: versions come from the source checkout."""
    raise NotImplementedError
