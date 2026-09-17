"""Linux primitives: documented stub for a future phase (PLAN.md section 9.12).

The signatures mirror ``win32.py`` so the dispatcher can import either module
without the callers knowing which one they got. Every function raises
``NotImplementedError``: the v1 implementation is Windows-only by decision
(PLAN.md section 3, non-goals).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

__all__ = [
    "DETACHED_PROCESS",
    "authenticode_status",
    "delete_run_key",
    "file_product_name",
    "file_version",
    "mutex_is_held",
    "no_window_popen_kwargs",
    "parse_run_command",
    "read_run_entries",
    "read_run_key",
    "read_shortcuts",
    "write_run_key",
]

DETACHED_PROCESS = 0
"""No such creation flag on POSIX: ``start_new_session`` plays that role."""

_MESSAGE = "the Linux platform layer is a stub: see PLAN.md section 9.12"


def _unsupported() -> NoReturn:
    raise NotImplementedError(_MESSAGE)


def file_version(path: Path | str) -> str | None:
    """Versions come from the source checkout on Linux, not from a PE resource."""
    _unsupported()


def file_product_name(path: Path | str) -> str | None:
    """No PE resources on Linux."""
    _unsupported()


def read_run_entries() -> dict[str, str]:
    """XDG autostart desktop files would take the place of the Run key."""
    _unsupported()


def read_run_key(name: str) -> str | None:
    """See :func:`read_run_entries`."""
    _unsupported()


def write_run_key(name: str, command: str) -> None:
    """See :func:`read_run_entries`."""
    _unsupported()


def delete_run_key(name: str) -> None:
    """See :func:`read_run_entries`."""
    _unsupported()


def parse_run_command(value: str) -> tuple[Path | None, list[str]]:
    """Desktop-file ``Exec=`` parsing, once Linux is implemented."""
    _unsupported()


def read_shortcuts(paths: Sequence[Path]) -> dict[Path, tuple[Path | None, list[str]]]:
    """No ``.lnk`` files on Linux."""
    _unsupported()


def mutex_is_held(name: str) -> bool:
    """A ``flock`` on a file under ``$XDG_RUNTIME_DIR`` would play this role."""
    _unsupported()


def no_window_popen_kwargs() -> dict[str, Any]:
    """Nothing to hide: no console windows are created on Linux."""
    return {}


def authenticode_status(path: Path | str) -> str:
    """No Authenticode on Linux: the caller treats this as information only."""
    return "unavailable"
