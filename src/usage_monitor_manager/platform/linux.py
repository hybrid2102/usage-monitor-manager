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
    "MANAGER_RUN_NAME",
    "MutexHolder",
    "RegistryRoot",
    "authenticode_status",
    "delete_run_key",
    "file_product_name",
    "file_version",
    "get_manager_autostart",
    "hold_mutex",
    "manager_autostart_command",
    "mutex_is_held",
    "no_window_popen_kwargs",
    "parse_run_command",
    "read_run_entries",
    "read_run_key",
    "read_shortcuts",
    "set_manager_autostart",
    "sync_manager_autostart_path",
    "write_run_key",
]

DETACHED_PROCESS = 0
"""No such creation flag on POSIX: ``start_new_session`` plays that role."""

RegistryRoot = tuple[int, str]
"""Kept for signature parity with ``win32``; meaningless on Linux."""

MANAGER_RUN_NAME = "UsageMonitorManager"
"""On Linux this would name a ``~/.config/autostart/*.desktop`` file."""

_MESSAGE = "the Linux platform layer is a stub: see PLAN.md section 9.12"


def _unsupported() -> NoReturn:
    raise NotImplementedError(_MESSAGE)


def file_version(path: Path | str) -> str | None:
    """Versions come from the source checkout on Linux, not from a PE resource."""
    _unsupported()


def file_product_name(path: Path | str) -> str | None:
    """No PE resources on Linux."""
    _unsupported()


def read_run_entries(root: RegistryRoot | None = None) -> dict[str, str]:
    """XDG autostart desktop files would take the place of the Run key."""
    _unsupported()


def read_run_key(name: str, root: RegistryRoot | None = None) -> str | None:
    """See :func:`read_run_entries`."""
    _unsupported()


def write_run_key(name: str, command: str, root: RegistryRoot | None = None) -> None:
    """See :func:`read_run_entries`."""
    _unsupported()


def delete_run_key(name: str, root: RegistryRoot | None = None) -> None:
    """See :func:`read_run_entries`."""
    _unsupported()


def manager_autostart_command(exe_cmd: str | None = None) -> str:
    """A ``.desktop`` ``Exec=`` line, once Linux is implemented."""
    _unsupported()


def get_manager_autostart(root: RegistryRoot | None = None) -> str | None:
    """See :func:`manager_autostart_command`."""
    _unsupported()


def set_manager_autostart(
    enabled: bool,
    exe_cmd: str | None = None,
    root: RegistryRoot | None = None,
) -> str | None:
    """See :func:`manager_autostart_command`."""
    _unsupported()


def sync_manager_autostart_path(
    exe_cmd: str | None = None,
    root: RegistryRoot | None = None,
) -> str | None:
    """See :func:`manager_autostart_command`."""
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


class MutexHolder:
    """Signature parity with ``win32.MutexHolder``; never constructed here."""

    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self._handle = handle

    def release(self) -> None:
        """Nothing is held: the Linux layer is a stub."""
        return

    def __enter__(self) -> MutexHolder:
        return self

    def __exit__(self, *_exc: object) -> None:
        return


def hold_mutex(name: str) -> MutexHolder | None:
    """A held ``flock`` would play this role (PLAN.md section 9.12)."""
    _unsupported()


def no_window_popen_kwargs() -> dict[str, Any]:
    """Nothing to hide: no console windows are created on Linux."""
    return {}


def authenticode_status(path: Path | str) -> str:
    """No Authenticode on Linux: the caller treats this as information only."""
    return "unavailable"
