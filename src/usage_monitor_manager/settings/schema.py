"""Declarative settings schema per kind: key, type, default, section, help.

Phase 0: stub. Implemented in phase 5 of PLAN.md section 7.
"""

from __future__ import annotations

from typing import Any

__all__ = ["schema_for"]


def schema_for(schema_id: str) -> dict[str, Any]:
    """Return the schema of a kind, keyed by settings key."""
    raise NotImplementedError
