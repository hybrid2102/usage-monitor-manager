"""Self-update of the manager: rename-swap of the running exe and relaunch.

Phase 0: stub. Implemented in phase 6 of PLAN.md section 7.
"""

from __future__ import annotations

__all__ = ["check_self_update", "finish_self_update", "perform_self_update"]


def check_self_update() -> str | None:
    """Return the newer manager version available, ``None`` when up to date."""
    raise NotImplementedError


def perform_self_update() -> bool:
    """Download, verify, rename-swap, relaunch with ``--finish-self-update``."""
    raise NotImplementedError


def finish_self_update(old_pid: int) -> bool:
    """Wait for the previous process to die, drop the ``.bak``, notify."""
    raise NotImplementedError
