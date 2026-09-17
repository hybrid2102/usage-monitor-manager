"""Launch profiles: named sets of instances started or stopped together.

Phase 0: stub. Implemented in phase 7 of PLAN.md section 7.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["apply_profile", "list_profiles"]


def list_profiles() -> Sequence[str]:
    """Names of the configured launch profiles."""
    raise NotImplementedError


def apply_profile(name: str) -> None:
    """Start the profile's instances and stop the others."""
    raise NotImplementedError
