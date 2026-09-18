"""Start / stop / restart of monitor instances (never of foreign processes).

Implements PLAN.md section 6.2. Three rules shape everything here:

1. **Never touch what is not ours.** A PID is only terminated when its
   ``exe()`` still equals the instance's executable (``os.path.normcase``
   comparison), re-checked immediately before every ``terminate()`` and every
   ``kill()``. Nothing is ever matched by process *name*. On top of that, the
   current process and its ancestors are always spared: the manager must not
   kill itself, nor whatever launched it.
2. **"Stopped" means the singleton is free.** A monitor is a PyInstaller
   onefile app holding a named mutex
   (``UsageMonitorForClaude_SingleInstance<suffix>``). Both PIDs can be gone
   while the kernel object lingers for a moment, and a new instance started in
   that window dies silently. ``stop`` therefore polls
   :func:`platform.mutex_is_held` until the name is free, and ``start`` refuses
   to launch while it is taken.
3. **A started instance is self-describing.** ``--config-dir`` is passed
   explicitly for every non-default directory, even when the original instance
   only had the environment variable, so that the Run key the monitor rewrites
   at startup stays correct.

Results are frozen dataclasses with ``to_dict()`` (the CLI's ``--json`` and,
later, the tray). Progress is reported through an optional ``on_event``
callback so a UI can follow a long restart without this module knowing about
any UI.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import psutil

from . import discovery, platform
from .identity import CONFIG_DIR_FLAG
from .model import Instance, JsonDict

__all__ = [
    "DEFAULT_GAP_SECONDS",
    "DEFAULT_START_WAIT",
    "DEFAULT_STOP_TIMEOUT",
    "INHERITED_ENV_BLOCKLIST",
    "EventCallback",
    "ProcessAction",
    "ProcessEvent",
    "RestartResult",
    "StartResult",
    "StopResult",
    "autostart_command",
    "build_argv",
    "build_env",
    "build_env_overrides",
    "inherited_env",
    "describe_start",
    "describe_stop",
    "get_instance_autostart",
    "is_default_config_dir",
    "mutex_name",
    "run_key_name",
    "set_instance_autostart",
    "restart",
    "restart_all",
    "spawn_detached",
    "start",
    "start_missing",
    "stop",
    "stop_all",
    "strips_env_var",
]

DEFAULT_STOP_TIMEOUT = 15.0
DEFAULT_START_WAIT = 5.0
DEFAULT_GAP_SECONDS = 2.0
"""Two seconds between starts, like the PowerShell updater: WebView2 children
of two instances of the same executable collide if they come up together."""

_MUTEX_POLL_INTERVAL = 0.25


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

ProcessAction = Literal["stop", "start", "restart"]

EventPhase = Literal[
    "stop_begin",
    "terminate",
    "kill",
    "exited",
    "skipped",
    "mutex_wait",
    "mutex_free",
    "mutex_busy",
    "stop_end",
    "start_begin",
    "spawned",
    "start_end",
    "wait",
    "error",
]


@dataclass(frozen=True, slots=True)
class ProcessEvent:
    """One step of a stop/start/restart, for logging and progress UI."""

    instance_id: str
    action: ProcessAction
    phase: EventPhase
    message: str
    pid: int | None = None

    def to_dict(self) -> JsonDict:
        return {
            "instance_id": self.instance_id,
            "action": self.action,
            "phase": self.phase,
            "message": self.message,
            "pid": self.pid,
        }


EventCallback = Callable[[ProcessEvent], None]


class _Reporter:
    """Turn ``on_event`` into a call that is always safe to make."""

    _action: ProcessAction
    _instance_id: str
    _on_event: EventCallback | None

    def __init__(self, action: ProcessAction, instance_id: str, on_event: EventCallback | None) -> None:
        self._action = action
        self._instance_id = instance_id
        self._on_event = on_event

    def __call__(self, phase: EventPhase, message: str, pid: int | None = None) -> None:
        if self._on_event is None:
            return
        self._on_event(
            ProcessEvent(
                instance_id=self._instance_id,
                action=self._action,
                phase=phase,
                message=message,
                pid=pid,
            )
        )


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StopResult:
    """Outcome of stopping one instance."""

    instance_id: str
    kind: str
    config_dir: Path
    ok: bool
    detail: str
    already_stopped: bool = False
    terminated_pids: tuple[int, ...] = ()
    killed_pids: tuple[int, ...] = ()
    skipped_pids: tuple[int, ...] = ()
    mutex_name: str = ""
    mutex_released: bool = True

    def to_dict(self) -> JsonDict:
        return {
            "instance_id": self.instance_id,
            "kind": self.kind,
            "config_dir": str(self.config_dir),
            "action": "stop",
            "ok": self.ok,
            "detail": self.detail,
            "already_stopped": self.already_stopped,
            "terminated_pids": list(self.terminated_pids),
            "killed_pids": list(self.killed_pids),
            "skipped_pids": list(self.skipped_pids),
            "mutex_name": self.mutex_name,
            "mutex_released": self.mutex_released,
        }


@dataclass(frozen=True, slots=True)
class StartResult:
    """Outcome of starting one instance."""

    instance_id: str
    kind: str
    config_dir: Path
    ok: bool
    detail: str
    already_running: bool = False
    argv: tuple[str, ...] = ()
    env_overrides: Mapping[str, str] = field(default_factory=lambda: {})
    spawned_pid: int | None = None
    bootloader_pid: int | None = None
    app_pid: int | None = None

    def to_dict(self) -> JsonDict:
        return {
            "instance_id": self.instance_id,
            "kind": self.kind,
            "config_dir": str(self.config_dir),
            "action": "start",
            "ok": self.ok,
            "detail": self.detail,
            "already_running": self.already_running,
            "argv": list(self.argv),
            "env_overrides": dict(self.env_overrides),
            "spawned_pid": self.spawned_pid,
            "bootloader_pid": self.bootloader_pid,
            "app_pid": self.app_pid,
        }


@dataclass(frozen=True, slots=True)
class RestartResult:
    """A restart is a batch: every stop, then every start."""

    stops: tuple[StopResult, ...] = ()
    starts: tuple[StartResult, ...] = ()

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.stops) and all(result.ok for result in self.starts)

    def to_dict(self) -> JsonDict:
        return {
            "action": "restart",
            "ok": self.ok,
            "stops": [result.to_dict() for result in self.stops],
            "starts": [result.to_dict() for result in self.starts],
        }


# --------------------------------------------------------------------------- #
# Small helpers (pure, unit-testable on their own)
# --------------------------------------------------------------------------- #


def _normcase(path: Path | str) -> str:
    return os.path.normcase(str(path))


def mutex_name(instance: Instance) -> str:
    """The instance's single-instance mutex, as the monitor itself names it."""
    return f"{instance.kind.mutex_base}{instance.instance_suffix}"


def is_default_config_dir(instance: Instance) -> bool:
    """True when the instance needs no ``--config-dir`` on its command line.

    That is the case for an instance living in its kind's default directory
    (``~/.claude``) and, by construction, for every ``github-exe`` instance:
    those have no config dir at all, so there is nothing to pass and nothing
    to put in the environment (PLAN.md section 9.9).
    """
    if not instance.kind.has_config_dir:
        return True
    return _normcase(instance.config_dir) == _normcase(instance.kind.default_config_dir)


def build_argv(instance: Instance, extra_args: Sequence[str] = ()) -> list[str]:
    """``[exe]`` for the default config dir, ``[exe, --config-dir=PATH]`` else.

    Passing the flag explicitly is a deliberate choice (PLAN.md section 6.2):
    an instance we started is then identifiable from its command line alone,
    without having to read its environment.

    ``extra_args`` are appended verbatim, after the config dir. That is how
    the diagnostic mode of PLAN.md section 9.7 adds ``--verbose`` without a
    second launch path.
    """
    if instance.exe_path is None:
        raise ValueError(f"instance {instance.instance_id} has no executable path")
    argv = [str(instance.exe_path)]
    if not is_default_config_dir(instance):
        argv.append(f"{CONFIG_DIR_FLAG}={instance.config_dir}")
    argv.extend(extra_args)
    return argv


def run_key_name(instance: Instance) -> str:
    """The autostart value name the monitor itself would use.

    ``UsageMonitorForClaude`` for the default directory,
    ``UsageMonitorForClaude_578560bbc610`` for ``~/.claude-valeria``: the same
    ``run_key_base + suffix`` the monitors write, which is what lets the
    manager toggle *their* entry instead of inventing a parallel one.
    """
    return f"{instance.kind.run_key_base}{instance.instance_suffix}"


def autostart_command(instance: Instance) -> str:
    """The exact command the monitor writes into its own Run entry.

    ``"<exe>"`` for a default config dir,
    ``"<exe>" --config-dir="<dir>"`` otherwise - quoted the way the monitors
    quote it, so that an entry written here is byte-comparable with one the
    monitor rewrites at its next startup.

    Raises:
        ValueError: when the instance has no known executable (a dormant entry
            discovered from a settings file alone cannot be autostarted).
    """
    if instance.exe_path is None:
        raise ValueError(f"instance {instance.instance_id} has no executable path")
    command = f'"{instance.exe_path}"'
    if not is_default_config_dir(instance):
        command += f' {CONFIG_DIR_FLAG}="{instance.config_dir}"'
    return command


def get_instance_autostart(instance: Instance, root: platform.RegistryRoot | None = None) -> str | None:
    """The instance's stored autostart command, ``None`` when it has none."""
    return platform.read_run_key(run_key_name(instance), root)


def set_instance_autostart(
    instance: Instance,
    enabled: bool,
    root: platform.RegistryRoot | None = None,
) -> str | None:
    """Write or remove the instance's autostart entry; returns what is stored.

    Raises:
        ValueError: see :func:`autostart_command`.
        OSError: when the registry write fails.
    """
    name = run_key_name(instance)
    if not enabled:
        platform.delete_run_key(name, root)
        return None
    command = autostart_command(instance)
    platform.write_run_key(name, command, root)
    return command


def build_env_overrides(instance: Instance) -> dict[str, str]:
    """The kind's environment variable, only for a non-default config dir."""
    env_var = instance.kind.env_var
    if env_var is None or is_default_config_dir(instance):
        return {}
    return {env_var: str(instance.config_dir)}


INHERITED_ENV_BLOCKLIST = ("_PYI_", "_MEI")
"""Environment prefixes that must never reach a child monitor.

The manager itself will be a PyInstaller one-file app, and the tests freeze a
dummy monitor with PyInstaller too. Such a process exports ``_MEIPASS2`` and
``_PYI_ARCHIVE_FILE`` / ``_PYI_APPLICATION_HOME_DIR`` /
``_PYI_PARENT_PROCESS_LEVEL``, which point at *its own* extracted bundle. A
child one-file executable that inherits them tries to load the parent's runtime
and dies with "Failed to load Python DLL ... _MEIxxxxx/pythonXY.dll".
The upstream monitors scrub exactly these variables before relaunching
themselves; the manager must do the same for every instance it starts.
"""


def strips_env_var(instance: Instance) -> bool:
    """True when the child's environment must have the kind's variable removed.

    The manager frequently runs inside a shell that already exports the kind's
    variable pointing at *another* instance's directory (a session started
    under ``CLAUDE_CONFIG_DIR`` set to another directory, say). Inheriting it while
    starting the *default* instance produces a second copy of the other
    instance instead - exactly the "wrong instance" bug this module exists to
    prevent. The variable is therefore never inherited: it is deleted for a
    default config dir and set explicitly otherwise.
    """
    env_var = instance.kind.env_var
    return env_var is not None and is_default_config_dir(instance) and env_var in os.environ


def inherited_env() -> dict[str, str]:
    """This process' environment minus the PyInstaller bootstrap variables.

    The base every child of the manager gets, monitors and the manager's own
    replacement alike (``self_update.apply_self`` relaunches the new executable
    with exactly this environment: a one-file build that inherited its parent's
    ``_MEIPASS2`` would try to load the *old* bundle and die).
    """
    return {key: value for key, value in os.environ.items() if not key.startswith(INHERITED_ENV_BLOCKLIST)}


def build_env(instance: Instance) -> dict[str, str]:
    """The child's environment: inherited, scrubbed, then made unambiguous."""
    env = inherited_env()
    env_var = instance.kind.env_var
    if env_var is None:
        return env
    if is_default_config_dir(instance):
        env.pop(env_var, None)
    else:
        env.update(build_env_overrides(instance))
    return env


def _sleep(seconds: float) -> None:
    """Indirection so tests can run the waits instantly."""
    if seconds > 0:
        time.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def open_process(pid: int) -> psutil.Process:
    """Indirection over ``psutil.Process`` so tests can inject fake processes."""
    return psutil.Process(pid)


def _self_and_ancestors() -> set[int]:
    """PIDs we refuse to terminate under any circumstance.

    A discovered "bootloader" can be the process that launched us (the CLI run
    from a shell, or pytest spawning a dummy monitor): same executable, so the
    exe guard would happily let it through. Killing it is never what the user
    asked for.
    """
    pids = {os.getpid()}
    try:
        current = open_process(os.getpid())
        for parent in current.parents():
            pids.add(parent.pid)
    except (psutil.Error, OSError):  # pragma: no cover - only on a hostile box
        pass
    return pids


def _exe_of(proc: psutil.Process) -> str | None:
    try:
        exe = proc.exe()
    except (psutil.Error, OSError):
        return None
    return _normcase(exe) if exe else None


def _is_ours(proc: psutil.Process, exe_path: Path) -> bool:
    """The exe-path guard, re-evaluated at every call site that kills."""
    return _exe_of(proc) == _normcase(exe_path)


# --------------------------------------------------------------------------- #
# stop
# --------------------------------------------------------------------------- #


def _stop_pids(instance: Instance) -> list[int]:
    """App PID first, then the bootloader: the child must go before the parent."""
    processes = instance.processes
    if processes is None:
        return []
    pids = [processes.app_pid]
    if processes.bootloader_pid is not None and processes.bootloader_pid != processes.app_pid:
        pids.append(processes.bootloader_pid)
    return pids


def _stop_targets(instance: Instance, exe_path: Path) -> list[psutil.Process]:
    """The processes to signal, children of the same executable first.

    A PyInstaller onefile instance is two processes running the same file, and
    ``discovery`` can legitimately have caught only one of them - the child may
    not have existed yet when the table was snapshotted. Signalling only the
    bootloader then leaves the *app* alive, holding the single-instance mutex,
    and ``stop`` would report a success that is not one. So each recorded PID
    contributes its own same-executable descendants as well, deepest first.
    WebView2 and any other foreign child is left untouched by the exe guard: it
    goes away on its own when its parent does.
    """
    targets: list[psutil.Process] = []
    seen: set[int] = set()
    for pid in _stop_pids(instance):
        try:
            proc = open_process(pid)
        except (psutil.Error, OSError):
            continue
        try:
            descendants = list(proc.children(recursive=True))
        except (psutil.Error, OSError):  # pragma: no cover - defensive
            descendants = []
        for child in reversed(descendants):
            if child.pid not in seen and _is_ours(child, exe_path):
                seen.add(child.pid)
                targets.append(child)
        if proc.pid not in seen:
            seen.add(proc.pid)
            targets.append(proc)
    return targets


def _wait_for_mutex(
    name: str,
    deadline: float,
    report: _Reporter,
) -> bool:
    """Poll until the single-instance mutex is free, or the deadline passes."""
    if not platform.mutex_is_held(name):
        return True
    report("mutex_wait", f"waiting for the {name} mutex to be released")
    while _monotonic() < deadline:
        _sleep(_MUTEX_POLL_INTERVAL)
        if not platform.mutex_is_held(name):
            return True
    return not platform.mutex_is_held(name)


def stop(
    instance: Instance,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    on_event: EventCallback | None = None,
) -> StopResult:
    """Terminate the app PID then the bootloader, then confirm the mutex is free.

    ``terminate()`` first, ``wait()`` within the remaining budget, ``kill()``
    as a fallback; every PID is re-verified against ``instance.exe_path``
    immediately before each signal. The instance is only declared stopped once
    its single-instance mutex has actually been released, so that a following
    ``start`` cannot race the dying process.
    """
    report = _Reporter("stop", instance.instance_id, on_event)
    name = mutex_name(instance)
    deadline = _monotonic() + max(timeout, 0.0)

    def result(ok: bool, detail: str, **extra: Any) -> StopResult:
        report("stop_end", detail)
        return StopResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=ok,
            detail=detail,
            mutex_name=name,
            **extra,
        )

    pids = _stop_pids(instance)
    if not pids:
        report("stop_begin", "no process recorded for this instance")
        if platform.mutex_is_held(name):
            report("mutex_busy", f"the {name} mutex is held by an unknown process")
            return result(
                False,
                f"not running for us, but the {name} mutex is still held: run 'umm list' again",
                already_stopped=True,
                mutex_released=False,
            )
        return result(True, "already stopped", already_stopped=True)

    if instance.exe_path is None:
        report("error", "executable path unknown: refusing to terminate anything")
        return result(False, "executable path unknown: refusing to terminate a PID we cannot verify")

    report("stop_begin", f"stopping {instance.instance_id} ({', '.join(str(pid) for pid in pids)})")
    protected = _self_and_ancestors()
    terminated: list[int] = []
    skipped: list[int] = []
    killed: list[int] = []
    pending: list[psutil.Process] = []

    for proc in _stop_targets(instance, instance.exe_path):
        pid = proc.pid
        if pid in protected:
            skipped.append(pid)
            report("skipped", f"pid {pid} is this process or one of its ancestors", pid)
            continue
        if not _is_ours(proc, instance.exe_path):
            skipped.append(pid)
            report("skipped", f"pid {pid} does not run {instance.exe_path}", pid)
            continue
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            report("exited", f"pid {pid} exited before we asked", pid)
            continue
        except (psutil.Error, OSError) as error:
            skipped.append(pid)
            report("error", f"cannot terminate pid {pid}: {error}", pid)
            continue
        terminated.append(pid)
        pending.append(proc)
        report("terminate", f"terminate sent to pid {pid}", pid)

    for proc in pending:
        remaining = max(deadline - _monotonic(), 0.0)
        try:
            proc.wait(timeout=remaining)
            report("exited", f"pid {proc.pid} exited", proc.pid)
            continue
        except psutil.NoSuchProcess:
            report("exited", f"pid {proc.pid} exited", proc.pid)
            continue
        except psutil.TimeoutExpired:
            pass
        except (psutil.Error, OSError) as error:  # pragma: no cover - defensive
            report("error", f"cannot wait for pid {proc.pid}: {error}", proc.pid)
            continue
        # Fallback: re-verify the exe before the harder signal.
        if not _is_ours(proc, instance.exe_path):
            report("skipped", f"pid {proc.pid} no longer runs {instance.exe_path}", proc.pid)
            continue
        try:
            proc.kill()
            killed.append(proc.pid)
            report("kill", f"kill sent to pid {proc.pid}", proc.pid)
            proc.wait(timeout=max(deadline - _monotonic(), 1.0))
            report("exited", f"pid {proc.pid} exited", proc.pid)
        except psutil.NoSuchProcess:
            report("exited", f"pid {proc.pid} exited", proc.pid)
        except (psutil.Error, OSError) as error:
            report("error", f"cannot kill pid {proc.pid}: {error}", proc.pid)

    released = _wait_for_mutex(name, deadline, report)
    counters = {
        "terminated_pids": tuple(terminated),
        "killed_pids": tuple(killed),
        "skipped_pids": tuple(skipped),
        "mutex_released": released,
    }
    if not released:
        report("mutex_busy", f"the {name} mutex is still held")
        return result(False, f"processes signalled but the {name} mutex is still held", **counters)
    report("mutex_free", f"the {name} mutex is free")
    if not terminated and not killed:
        detail = "nothing we were allowed to stop" if skipped else "already stopped"
        return result(not skipped, detail, already_stopped=not skipped, **counters)
    stopped = sorted({*terminated, *killed})
    return result(True, f"stopped ({', '.join(str(pid) for pid in stopped)})", **counters)


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #


def _spawn(
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
    log_path: Path | None = None,
) -> int:
    """Launch detached and windowless; returns the PID of the bootloader.

    ``log_path`` redirects both standard streams to that file, appending. The
    handle is closed here: the child keeps its own inherited copy, which is
    what makes the diagnostic log keep growing after we return.
    """
    kwargs: dict[str, Any] = platform.no_window_popen_kwargs()
    creationflags = int(kwargs.pop("creationflags", 0)) | platform.DETACHED_PROCESS
    stream = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("ab")
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is built from the kind registry
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            creationflags=creationflags,
            close_fds=stream is None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if stream is None else stream,
            stderr=subprocess.DEVNULL if stream is None else subprocess.STDOUT,
            **kwargs,
        )
    finally:
        if stream is not None:
            stream.close()
    return process.pid


def spawn_detached(
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
    log_path: Path | None = None,
) -> int:
    """Public name of the detached, windowless launch used across the manager."""
    return _spawn(argv, env, cwd, log_path)


def _find_running(instance: Instance) -> Instance | None:
    """Re-discover the ``(kind, config dir)`` pair we just launched."""
    for found in discovery.discover(include_dormant=False):
        if found.kind.key != instance.kind.key:
            continue
        if _normcase(found.config_dir) != _normcase(instance.config_dir):
            continue
        if found.is_running:
            return found
    return None


def start(
    instance: Instance,
    wait_seconds: float = DEFAULT_START_WAIT,
    on_event: EventCallback | None = None,
    *,
    extra_args: Sequence[str] = (),
    log_path: Path | None = None,
) -> StartResult:
    """Launch the instance detached, then confirm a running pair came up.

    Refuses to start while the single-instance mutex is held: the monitor would
    exit immediately and the user would be left wondering why nothing happened.

    ``extra_args`` and ``log_path`` are the diagnostic mode of PLAN.md
    section 9.7: the instance is started with ``--verbose`` and its two output
    streams are appended to a file instead of being thrown away.
    """
    report = _Reporter("start", instance.instance_id, on_event)
    name = mutex_name(instance)

    def result(ok: bool, detail: str, **extra: Any) -> StartResult:
        report("start_end", detail)
        return StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=ok,
            detail=detail,
            **extra,
        )

    if instance.exe_path is None:
        report("error", "executable path unknown")
        return result(False, "executable path unknown: nothing to launch")
    exe = instance.exe_path
    if not exe.is_file():
        report("error", f"{exe} does not exist")
        return result(False, f"executable not found: {exe}")
    if platform.mutex_is_held(name):
        report("mutex_busy", f"the {name} mutex is held")
        return result(
            False,
            f"already running: the {name} mutex is held by a live instance",
            already_running=True,
        )

    argv = tuple(build_argv(instance, extra_args))
    overrides = build_env_overrides(instance)
    env = build_env(instance)
    report("start_begin", f"launching {' '.join(argv)}")
    if log_path is not None:
        report("start_begin", f"output redirected to {log_path}")
    try:
        pid = _spawn(argv, env, exe.parent, log_path)
    except (OSError, ValueError) as error:
        report("error", f"cannot launch {exe}: {error}")
        return result(False, f"launch failed: {error}", argv=argv, env_overrides=overrides)
    report("spawned", f"spawned pid {pid}", pid)

    report("wait", f"waiting {wait_seconds:g}s for the instance to come up")
    _sleep(wait_seconds)
    found = _find_running(instance)
    if found is None or found.processes is None:
        return result(
            False,
            f"launched pid {pid} but no running instance was found for {instance.config_dir} "
            f"after {wait_seconds:g}s (check the config dir and the settings file)",
            argv=argv,
            env_overrides=overrides,
            spawned_pid=pid,
        )
    pair = found.processes
    return result(
        True,
        f"started ({pair.bootloader_pid or '-'}/{pair.app_pid})",
        argv=argv,
        env_overrides=overrides,
        spawned_pid=pid,
        bootloader_pid=pair.bootloader_pid,
        app_pid=pair.app_pid,
    )


# --------------------------------------------------------------------------- #
# Batch operations
# --------------------------------------------------------------------------- #


def restart(
    instances: Sequence[Instance],
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    start_wait: float = DEFAULT_START_WAIT,
    on_event: EventCallback | None = None,
) -> RestartResult:
    """Stop every instance, then start them one by one with a gap in between.

    Stopping everything first matters when several instances share one
    executable: they all release their mutex before any of them comes back.
    An instance whose stop failed is not started again - that would risk a
    second copy of a still-living monitor.
    """
    stops = tuple(stop(instance, timeout=stop_timeout, on_event=on_event) for instance in instances)
    failed = {result.instance_id for result in stops if not result.ok}

    starts: list[StartResult] = []
    first = True
    for instance in instances:
        if instance.instance_id in failed:
            starts.append(
                StartResult(
                    instance_id=instance.instance_id,
                    kind=instance.kind.key,
                    config_dir=instance.config_dir,
                    ok=False,
                    detail="not started: the stop did not complete",
                )
            )
            continue
        if not first:
            _sleep(gap_seconds)
        first = False
        starts.append(start(instance, wait_seconds=start_wait, on_event=on_event))
    return RestartResult(stops=stops, starts=tuple(starts))


def stop_all(
    instances: Sequence[Instance] | None = None,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    on_event: EventCallback | None = None,
) -> list[StopResult]:
    """Stop every running instance (discovered when none are given)."""
    targets = _running(instances)
    return [stop(instance, timeout=timeout, on_event=on_event) for instance in targets]


def restart_all(
    instances: Sequence[Instance] | None = None,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    on_event: EventCallback | None = None,
) -> RestartResult:
    """Restart every running instance (discovered when none are given)."""
    return restart(_running(instances), gap_seconds=gap_seconds, on_event=on_event)


def start_missing(
    instances: Sequence[Instance] | None = None,
    wait_seconds: float = DEFAULT_START_WAIT,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    on_event: EventCallback | None = None,
) -> list[StartResult]:
    """Start the dormant instances only; the running ones are left alone."""
    targets = [instance for instance in _known(instances) if not instance.is_running]
    results: list[StartResult] = []
    for index, instance in enumerate(targets):
        if index:
            _sleep(gap_seconds)
        results.append(start(instance, wait_seconds=wait_seconds, on_event=on_event))
    return results


def _known(instances: Sequence[Instance] | None) -> list[Instance]:
    return list(instances) if instances is not None else list(discovery.discover())


def _running(instances: Sequence[Instance] | None) -> list[Instance]:
    return [instance for instance in _known(instances) if instance.is_running]


def describe_stop(instance: Instance) -> str:
    """One-line preview of what :func:`stop` would do (``--dry-run``)."""
    pids = _stop_pids(instance)
    if not pids:
        return f"would skip {instance.instance_id}: not running"
    return (
        f"would stop {instance.instance_id} pids {', '.join(str(pid) for pid in pids)} "
        f"then wait for the {mutex_name(instance)} mutex"
    )


def describe_start(instance: Instance, extra_args: Sequence[str] = ()) -> str:
    """One-line preview of what :func:`start` would do (``--dry-run``)."""
    if instance.exe_path is None:
        return f"would skip {instance.instance_id}: executable path unknown"
    argv = " ".join(build_argv(instance, extra_args))
    env = "".join(f" {key}={value}" for key, value in sorted(build_env_overrides(instance).items()))
    if strips_env_var(instance):
        env = f" (without {instance.kind.env_var})"
    return f"would start {instance.instance_id}: {argv}{env}"
