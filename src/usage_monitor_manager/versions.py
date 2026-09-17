"""Version handling: PE ProductVersion via ctypes, semver parse and compare.

Phase 0: stub. Implemented in phase 1 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["compare_versions", "parse_version", "read_pe_product_version"]


def read_pe_product_version(exe_path: Path) -> str | None:
    """Read the ``ProductVersion`` of a Windows executable, ``None`` if absent."""
    raise NotImplementedError


def parse_version(text: str) -> tuple[int, ...]:
    """Parse ``v1.22.0`` / ``1.22.0.0`` into a comparable tuple."""
    raise NotImplementedError


def compare_versions(local: str, remote: str) -> int:
    """Return -1, 0 or 1 comparing two versions normalized to 3 components."""
    raise NotImplementedError
