"""Executable history in .umm-archive plus version rollback.

Phase 0: stub. Implemented in phase 6 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["archive_exe", "list_archived", "restore"]


def archive_exe(exe_path: Path, version: str) -> Path:
    """Copy the exe into ``<exe_dir>/.umm-archive`` and update the manifest."""
    raise NotImplementedError


def list_archived(exe_path: Path) -> list[str]:
    """Versions available for rollback, newest first."""
    raise NotImplementedError


def restore(exe_path: Path, version: str) -> bool:
    """Put an archived version back in place."""
    raise NotImplementedError
