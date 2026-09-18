"""Diagnostic mode and the manager's own log file (PLAN.md section 9.7).

Two things live here because they answer the same question - "what happened?" -
and share one directory, ``%LOCALAPPDATA%\\UsageMonitorManager\\logs``:

* **Diagnostic mode.** One instance at a time can be restarted with
  ``--verbose`` and both of its output streams appended to
  ``logs/<instance id>/<timestamp>.log``. ``stop`` puts it back the way it was.
  Which instance is in diagnostic mode is remembered in ``diag.json`` next to
  the manager config, because the flag lives in the *running process*, not in
  any file the monitor reads: without that note a manager restarted in the
  meantime could not tell the user that an instance is still verbose.
* **The manager log.** A rotating 1 MB x 3 file every layer of the manager
  writes to through :func:`get_logger`. The tray, the window, the scheduler,
  the watchdog and the updater log their events there, and the Manager page
  shows its tail.

Nothing here ever writes into an instance's config dir or settings file: the
logs are the manager's own, under the manager's own directory.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import cast

from . import manager_config, process_control
from .model import Instance, JsonDict

__all__ = [
    "DIAG_ARGS",
    "LOGGER_NAME",
    "LOG_BACKUP_COUNT",
    "LOG_DIR_NAME",
    "LOG_MAX_BYTES",
    "MANAGER_LOG_NAME",
    "STATE_FILENAME",
    "DiagResult",
    "DiagSession",
    "clear_session",
    "collect_report",
    "current_session",
    "describe_start",
    "get_logger",
    "instance_log_dir",
    "log_dir",
    "log_path_for",
    "manager_log_path",
    "read_state",
    "setup_manager_logging",
    "start_diagnostics",
    "stop_diagnostics",
    "tail",
    "tail_manager_log",
    "write_state",
]

DIAG_ARGS: tuple[str, ...] = ("--verbose",)
"""What the monitors themselves accept: ``--verbose`` opens their diagnostic
output. The manager adds nothing of its own to the command line."""

LOG_DIR_NAME = "logs"
STATE_FILENAME = "diag.json"
MANAGER_LOG_NAME = "manager.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
LOGGER_NAME = "umm"
DEFAULT_TAIL_LINES = 200


def log_dir(base: Path | None = None) -> Path:
    """``%LOCALAPPDATA%\\UsageMonitorManager\\logs``."""
    return (base or manager_config.app_dir()) / LOG_DIR_NAME


def instance_log_dir(instance_id: str, base: Path | None = None) -> Path:
    """The per-instance log directory; the id is sanitized, never trusted."""
    safe = "".join(char if char.isalnum() or char in "-._" else "_" for char in instance_id) or "instance"
    return log_dir(base) / safe


def log_path_for(instance_id: str, *, now: datetime | None = None, base: Path | None = None) -> Path:
    """``logs/<instance>/<YYYYmmdd-HHMMSS>.log`` for a new diagnostic run."""
    stamp = (now or datetime.now(tz=UTC)).astimezone().strftime("%Y%m%d-%H%M%S")
    return instance_log_dir(instance_id, base) / f"{stamp}.log"


def manager_log_path(base: Path | None = None) -> Path:
    """``logs/manager.log``, the rotating file every layer writes to."""
    return log_dir(base) / MANAGER_LOG_NAME


# --------------------------------------------------------------------------- #
# Persisted state
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DiagSession:
    """The instance currently running in diagnostic mode, and its log file."""

    instance_id: str
    log_path: Path
    started_at: datetime | None = None
    argv: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: object) -> DiagSession | None:
        if not isinstance(payload, dict):
            return None
        raw = cast("dict[object, object]", payload)
        data: dict[str, object] = {str(key): value for key, value in raw.items()}
        instance_id = data.get("instance_id")
        log_path = data.get("log_path")
        if not isinstance(instance_id, str) or not isinstance(log_path, str):
            return None
        raw_stamp = data.get("started_at")
        started_at: datetime | None = None
        if isinstance(raw_stamp, str):
            try:
                started_at = datetime.fromisoformat(raw_stamp)
            except ValueError:
                started_at = None
        raw_argv = data.get("argv")
        argv = tuple(str(item) for item in cast("list[object]", raw_argv)) if isinstance(raw_argv, list) else ()
        return cls(instance_id=instance_id, log_path=Path(log_path), started_at=started_at, argv=argv)

    def to_dict(self) -> JsonDict:
        return {
            "instance_id": self.instance_id,
            "log_path": str(self.log_path),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "argv": list(self.argv),
        }


def state_path(base: Path | None = None) -> Path:
    """Path of ``diag.json``."""
    return (base or manager_config.app_dir()) / STATE_FILENAME


def read_state(base: Path | None = None) -> DiagSession | None:
    """The recorded diagnostic session, or ``None``; never raises."""
    try:
        text = state_path(base).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload: object = json.loads(text)
    except ValueError:
        return None
    return DiagSession.from_dict(payload)


def write_state(session: DiagSession | None, base: Path | None = None) -> None:
    """Record (or, with ``None``, forget) the diagnostic session, atomically."""
    target = state_path(base)
    if session is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(session.to_dict(), indent=2, ensure_ascii=False) + "\n")
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def current_session(base: Path | None = None) -> DiagSession | None:
    """Alias of :func:`read_state`, for callers that read it as a question."""
    return read_state(base)


def clear_session(base: Path | None = None) -> None:
    """Forget the recorded diagnostic session."""
    write_state(None, base)


# --------------------------------------------------------------------------- #
# Start / stop
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DiagResult:
    """Outcome of entering or leaving diagnostic mode."""

    instance_id: str
    ok: bool
    detail: str
    log_path: Path | None = None
    argv: tuple[str, ...] = ()
    stop: process_control.StopResult | None = None
    start: process_control.StartResult | None = None

    def to_dict(self) -> JsonDict:
        return {
            "instance_id": self.instance_id,
            "ok": self.ok,
            "detail": self.detail,
            "log_path": None if self.log_path is None else str(self.log_path),
            "argv": list(self.argv),
            "stop": None if self.stop is None else self.stop.to_dict(),
            "start": None if self.start is None else self.start.to_dict(),
        }


def describe_start(instance: Instance, *, base: Path | None = None, now: datetime | None = None) -> list[str]:
    """What :func:`start_diagnostics` would do, touching nothing."""
    path = log_path_for(instance.instance_id, now=now, base=base)
    lines = [process_control.describe_stop(instance)]
    lines.append(process_control.describe_start(instance, DIAG_ARGS))
    lines.append(f"would append stdout and stderr to {path}")
    return lines


def start_diagnostics(
    instance: Instance,
    *,
    base: Path | None = None,
    now: datetime | None = None,
    stop_timeout: float = process_control.DEFAULT_STOP_TIMEOUT,
    wait_seconds: float = process_control.DEFAULT_START_WAIT,
    on_event: process_control.EventCallback | None = None,
) -> DiagResult:
    """Restart ``instance`` with ``--verbose`` and its output on a file.

    A running instance is stopped first (a monitor is a singleton: there is no
    way to make a live one verbose). If the stop fails nothing is launched -
    the same rule :func:`process_control.restart` follows.
    """
    log = get_logger("diag")
    path = log_path_for(instance.instance_id, now=now, base=base)
    stop_result: process_control.StopResult | None = None
    if instance.is_running:
        stop_result = process_control.stop(instance, timeout=stop_timeout, on_event=on_event)
        if not stop_result.ok:
            log.warning("diag start %s aborted: %s", instance.instance_id, stop_result.detail)
            return DiagResult(
                instance_id=instance.instance_id,
                ok=False,
                detail=f"not started in diagnostic mode: {stop_result.detail}",
                stop=stop_result,
            )
    start_result = process_control.start(
        instance,
        wait_seconds=wait_seconds,
        on_event=on_event,
        extra_args=DIAG_ARGS,
        log_path=path,
    )
    argv = tuple(start_result.argv)
    if start_result.ok:
        write_state(
            DiagSession(
                instance_id=instance.instance_id,
                log_path=path,
                started_at=(now or datetime.now(tz=UTC)),
                argv=argv,
            ),
            base,
        )
    log.info("diag start %s -> %s (%s)", instance.instance_id, start_result.ok, path)
    return DiagResult(
        instance_id=instance.instance_id,
        ok=start_result.ok,
        detail=start_result.detail,
        log_path=path,
        argv=argv,
        stop=stop_result,
        start=start_result,
    )


def stop_diagnostics(
    instance: Instance,
    *,
    base: Path | None = None,
    stop_timeout: float = process_control.DEFAULT_STOP_TIMEOUT,
    wait_seconds: float = process_control.DEFAULT_START_WAIT,
    on_event: process_control.EventCallback | None = None,
    restart: bool = True,
) -> DiagResult:
    """Put the instance back in normal mode (stop, then start without flags)."""
    log = get_logger("diag")
    session = read_state(base)
    stop_result: process_control.StopResult | None = None
    if instance.is_running:
        stop_result = process_control.stop(instance, timeout=stop_timeout, on_event=on_event)
        if not stop_result.ok:
            return DiagResult(
                instance_id=instance.instance_id,
                ok=False,
                detail=f"still in diagnostic mode: {stop_result.detail}",
                log_path=None if session is None else session.log_path,
                stop=stop_result,
            )
    start_result = process_control.start(instance, wait_seconds=wait_seconds, on_event=on_event) if restart else None
    clear_session(base)
    log.info("diag stop %s", instance.instance_id)
    ok = start_result is None or start_result.ok
    detail = "back to normal mode" if ok else f"restart failed: {start_result.detail if start_result else ''}"
    return DiagResult(
        instance_id=instance.instance_id,
        ok=ok,
        detail=detail,
        log_path=None if session is None else session.log_path,
        stop=stop_result,
        start=start_result,
    )


# --------------------------------------------------------------------------- #
# Reading logs
# --------------------------------------------------------------------------- #


def tail(path: Path, lines: int = DEFAULT_TAIL_LINES) -> list[str]:
    """The last ``lines`` lines of a log file; an unreadable file yields ``[]``.

    Decoding is lossy on purpose: a monitor writing in the console code page
    must not make the viewer fail.
    """
    if lines <= 0:
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                stream.seek(size)
                data = stream.read(step) + data
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return text.split("\n")[-lines:]


def tail_manager_log(lines: int = 50, base: Path | None = None) -> list[str]:
    """The tail of ``manager.log``, for the Manager page."""
    return tail(manager_log_path(base), lines)


def list_logs(instance_id: str, base: Path | None = None) -> list[Path]:
    """Diagnostic logs of one instance, newest last."""
    try:
        return sorted(instance_log_dir(instance_id, base).glob("*.log"))
    except OSError:  # pragma: no cover - unreadable directory
        return []


def collect_report(
    payload: JsonDict,
    *,
    instance: Instance | None = None,
    log_lines: Sequence[str] = (),
    manager_version: str = "",
) -> str:
    """The "Copia diagnostica" text block, ready to paste in a GitHub issue.

    Versions, the config dir and settings path of the instance, the last log
    lines and a trimmed ``umm status --json``: everything a maintainer asks for
    in the first reply, and nothing else (no environment dump, no token).
    """
    import platform as _platform
    import sys

    lines: list[str] = ["## Diagnostica Usage Monitor Manager", ""]
    lines.append(f"- manager: {manager_version or payload.get('version') or '?'}")
    lines.append(f"- python: {sys.version.split()[0]}")
    lines.append(f"- os: {_platform.platform()}")
    lines.append(f"- generato: {payload.get('generated_at') or datetime.now(tz=UTC).isoformat()}")
    if instance is not None:
        lines.extend(
            [
                "",
                f"### Istanza {instance.instance_id}",
                f"- kind: {instance.kind.key} ({instance.kind.family})",
                f"- exe: {instance.exe_path} ({instance.exe_version or '?'})",
                f"- config dir: {instance.config_dir if instance.kind.has_config_dir else '-'}",
                f"- settings: {instance.settings_path or '-'}",
                f"- in esecuzione: {'si' if instance.is_running else 'no'}",
            ]
        )
    trimmed = {key: value for key, value in payload.items() if key != "watchdog"}
    lines.extend(["", "### umm status --json", "```json", json.dumps(trimmed, indent=2, ensure_ascii=False), "```"])
    if log_lines:
        lines.extend(["", f"### Ultime {len(log_lines)} righe di log", "```", *log_lines, "```"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# The manager log
# --------------------------------------------------------------------------- #

_configured = False


def setup_manager_logging(base: Path | None = None, *, level: int = logging.INFO) -> logging.Logger:
    """Attach the rotating handler to the ``umm`` logger, once per process.

    A logging setup that cannot write (a read-only ``%LOCALAPPDATA%``, a
    directory held by something else) must not stop the manager from starting,
    so a failure leaves the logger without a file handler and is otherwise
    ignored.
    """
    global _configured  # noqa: PLW0603 - one handler per process is the point
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if _configured:
        return logger
    path = manager_log_path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        _configured = True
        return logger
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """``logging.getLogger("umm.<name>")``; never configures anything itself."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def reset_logging() -> None:
    """Drop the handler and let :func:`setup_manager_logging` run again (tests)."""
    global _configured  # noqa: PLW0603 - the inverse of the setter above
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _configured = False
