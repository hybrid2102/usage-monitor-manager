"""Start / stop / restart of monitor instances (never of foreign processes).

Phase 0: stub. Implemented in phase 2 of PLAN.md section 7.
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import Instance

__all__ = ["restart", "start", "stop"]


def stop(instance: Instance, timeout: float = 15.0) -> bool:
    """Terminate the app pid then the bootloader; confirm the mutex is free."""
    raise NotImplementedError


def start(instance: Instance) -> Instance:
    """Launch the executable detached, with an explicit ``--config-dir``."""
    raise NotImplementedError


def restart(instances: Sequence[Instance]) -> Sequence[Instance]:
    """Stop every instance, then start them in sequence with a delay."""
    raise NotImplementedError
