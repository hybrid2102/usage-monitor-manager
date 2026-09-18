"""Linux primitives: documented stub for a future phase (PLAN.md section 9.12).

The signatures mirror ``win32.py`` exactly, so the dispatcher in
``platform/__init__.py`` can import either module and no caller has to know
which one it got. Every primitive that would need a real Linux implementation
raises ``NotImplementedError("Linux: previsto, non implementato ...")``: the v1
is Windows-only by decision (PLAN.md section 3, non-goals), and a stub that
raises is honest where a stub that returns a plausible empty answer would make
the discovery quietly find nothing.

Three functions do **not** raise, because on Linux the honest answer is the
trivial one: :func:`attach_parent_console` (a POSIX process already has its
parent's terminal), :func:`no_window_popen_kwargs` (there are no console
windows to hide) and :func:`authenticode_status` (there is no Authenticode, and
the caller already treats the answer as information only).

``docs/architecture.md`` lists what a real port would have to build: ``flock``
instead of the named mutex, XDG paths instead of ``%LOCALAPPDATA%`` and the Run
key, and "run from source" instead of swapping an executable.
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
    "attach_parent_console",
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

_MESSAGE = "Linux: previsto, non implementato (PLAN.md sezione 9.12, docs/architecture.md)"
"""The one sentence every unimplemented primitive raises.

It is in Italian on purpose: it is the message a user would see, and it says
the two things that matter - the port is *planned*, not forgotten, and where
the notes on what it needs live."""


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


def attach_parent_console() -> bool:
    """A POSIX process already inherits its parent's terminal."""
    return True


def no_window_popen_kwargs() -> dict[str, Any]:
    """Nothing to hide: no console windows are created on Linux."""
    return {}


def authenticode_status(path: Path | str) -> str:
    """No Authenticode on Linux: the caller treats this as information only."""
    return "unavailable"


# --------------------------------------------------------------------------- #
# Global hotkey: no equivalent in the stub (PLAN.md section 9.12)
# --------------------------------------------------------------------------- #

HOTKEY_MODIFIERS: dict[str, int] = {}


class HotkeyError(RuntimeError):
    """Raised by :func:`parse_hotkey` on this platform: nothing to bind to."""


def parse_hotkey(text: str) -> tuple[int, int]:
    """Always raises: a global hotkey needs a display server integration."""
    raise HotkeyError(f"global hotkeys are not implemented on this platform ({text!r})")


def format_hotkey(modifiers: int, virtual_key: int) -> str:
    """Round-trip helper kept for signature parity with the Windows backend."""
    return f"{modifiers}+{virtual_key}"


def normalize_hotkey(text: str) -> str:
    """Always raises, see :func:`parse_hotkey`."""
    raise HotkeyError(f"global hotkeys are not implemented on this platform ({text!r})")


class HotkeyListener:
    """Signature-compatible stub that never registers anything."""

    def __init__(self, combo: str, callback: object, **_ignored: object) -> None:
        self.combo = combo
        self._callback = callback
        self.registered = False
        self.error = "global hotkeys are not implemented on this platform"

    def start(self, timeout: float = 5.0) -> bool:
        return False

    def stop(self, timeout: float = 2.0) -> None:
        return None
