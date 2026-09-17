"""Update execution: download, verify, stop, swap, restart, rollback.

Phase 0: stub. Implemented in phase 3 of PLAN.md section 7.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .model import Instance, UpdatePlan

__all__ = ["apply_plan", "plan_update", "rollback"]


def plan_update(instances: Sequence[Instance]) -> Sequence[UpdatePlan]:
    """Group instances by executable and build one plan per outdated exe."""
    raise NotImplementedError


def apply_plan(plan: UpdatePlan, progress_cb: Callable[[str], None] | None = None) -> bool:
    """Run the plan's steps, keeping a ``.bak`` until the post-start check passes."""
    raise NotImplementedError


def rollback(plan: UpdatePlan) -> bool:
    """Restore the ``.bak`` executable and restart the affected instances."""
    raise NotImplementedError
