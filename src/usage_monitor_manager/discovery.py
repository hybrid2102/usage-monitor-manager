"""Instance discovery: running processes, Run keys, settings files.

Phase 0: stub. Implemented in phase 1 of PLAN.md section 7.
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import Instance

__all__ = ["discover_instances"]


def discover_instances() -> Sequence[Instance]:
    """Return every known instance, running or dormant, ordered by kind."""
    raise NotImplementedError
