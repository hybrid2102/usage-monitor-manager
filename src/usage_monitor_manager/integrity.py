"""Integrity checks: sha256 (API digest or .sha256 sidecar), optional Authenticode.

Phase 0: stub. Implemented in phase 3 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["sha256_file", "verify_authenticode", "verify_sha256"]


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of a file, read in chunks."""
    raise NotImplementedError


def verify_sha256(path: Path, expected: str) -> bool:
    """Compare a file's sha256 against an expected digest."""
    raise NotImplementedError


def verify_authenticode(path: Path) -> bool | None:
    """Informational Authenticode check; ``None`` when unsigned or unavailable."""
    raise NotImplementedError
