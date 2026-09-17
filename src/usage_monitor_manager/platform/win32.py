"""Windows primitives: detached Popen, Run key, file version, WinVerifyTrust, hotkey.

Phase 0: stub. Implemented in phase 1 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["delete_run_key", "file_version", "read_run_key", "write_run_key"]


def file_version(path: Path) -> str | None:
    """``GetFileVersionInfoW`` wrapper returning the ``ProductVersion`` string."""
    raise NotImplementedError


def read_run_key(name: str) -> str | None:
    """Read the ``HKCU`` autostart entry named ``name``."""
    raise NotImplementedError


def write_run_key(name: str, command: str) -> None:
    """Create or update an autostart entry."""
    raise NotImplementedError


def delete_run_key(name: str) -> None:
    """Remove an autostart entry if present."""
    raise NotImplementedError
