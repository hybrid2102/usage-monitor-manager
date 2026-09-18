"""The health snapshot and the ``status.json`` file (PLAN.md section 9.11).

:func:`build_status` produces the document ``umm status --json`` prints: the
instances, the stored update bookkeeping and one watchdog pass. It is offline
unless ``check=True``, because a statusline script calls it every few seconds
and the scheduler's stored ``last_check`` already says what GitHub answered.

:func:`write_status_file` writes that same document, atomically, wherever the
user pointed ``preferences.status_file``. The tray rewrites it after every
discovery, watchdog and scheduler cycle, so a shell prompt or a statusline can
read a fresh file without ever starting a Python process. The schema (and an
example consumer) is documented in ``docs/status-file.md``.

Atomicity matters more here than anywhere else in the manager: the reader is a
script that runs constantly and would happily parse a half-written file. The
document therefore goes to a temporary file in the same directory and is then
renamed over the target, so a reader sees either the previous document or the
new one.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, manager_config
from .model import JsonDict

__all__ = ["DEFAULT_FILENAME", "build_status", "default_status_path", "resolve_path", "write_status_file"]

DEFAULT_FILENAME = "status.json"


def default_status_path(base: Path | None = None) -> Path:
    """``%LOCALAPPDATA%\\UsageMonitorManager\\status.json``."""
    return (base or manager_config.app_dir()) / DEFAULT_FILENAME


def resolve_path(raw: str | os.PathLike[str] | None, base: Path | None = None) -> Path:
    """A user-supplied path, or the default when nothing was given.

    A directory (existing, or spelled with a trailing separator) gets
    ``status.json`` appended, so ``umm status --write-file C:\\tmp`` does what
    it looks like it does.
    """
    if raw is None or str(raw).strip() == "":
        return default_status_path(base)
    text = str(raw)
    path = Path(text).expanduser()
    if text.endswith(("\\", "/")) or path.is_dir():
        return path / DEFAULT_FILENAME
    return path


def _normcase(path: Path | str) -> str:
    return os.path.normcase(str(path))


def build_status(*, check: bool = False) -> JsonDict:
    """Instances, the stored update bookkeeping and one watchdog pass.

    ``generated_at`` is always present and is what a consumer uses to notice a
    stale file (a manager that is no longer running).

    Raises:
        Nothing on the offline path. With ``check=True`` a GitHub failure is
        reported inside ``updates.error`` rather than raised.
    """
    from . import kinds, watchdog
    from .cli import build_plans, skipped_plans  # noqa: PLC0415 - avoids a cycle at import time
    from .discovery import discover
    from .github import GitHubError
    from .self_update import MANAGER_REPO, check_self
    from .updater import plan_status
    from .versions import VersionStatus, display_version

    config = manager_config.load()
    kinds.reload(config)
    instances = discover()
    rows = [
        {
            "id": instance.instance_id,
            "kind": instance.kind.key,
            "family": instance.kind.family,
            "label": instance.label or config.label_for(instance.kind.key, instance.config_dir),
            "config_dir": str(instance.config_dir) if instance.kind.has_config_dir else None,
            "exe_path": None if instance.exe_path is None else str(instance.exe_path),
            "local_version": display_version(instance.exe_version),
            "is_running": instance.is_running,
            "pid": None if instance.processes is None else instance.processes.app_pid,
        }
        for instance in instances
    ]

    updates: JsonDict = {
        "checked": bool(check),
        "last_check": dict(config.last_check),
        "skipped_versions": dict(config.skipped_versions),
        "plans": [],
        "available": 0,
        "error": None,
    }
    self_state: JsonDict | None = None
    if check:
        try:
            plans = build_plans(None, prerelease=config.preferences.include_prerelease)
        except GitHubError as error:
            updates["error"] = str(error)
            plans = []
        ignored = {_normcase(plan.exe_path) for plan in skipped_plans(plans)}
        updates["plans"] = [
            {
                "kind": plan.kind.key,
                "exe_path": str(plan.exe_path),
                "current_version": display_version(plan.current_version),
                "target_version": plan.target.version,
                "tag": plan.target.tag,
                "status": "skipped" if _normcase(plan.exe_path) in ignored else str(plan_status(plan)),
            }
            for plan in plans
        ]
        updates["available"] = sum(
            1
            for plan in plans
            if plan_status(plan) is VersionStatus.UPDATE_AVAILABLE and _normcase(plan.exe_path) not in ignored
        )
        self_state = check_self(
            include_prerelease=config.include_prerelease_for(MANAGER_REPO),
            skipped_tag=config.skipped_versions.get(MANAGER_REPO),
        ).to_dict()

    findings = watchdog.Watchdog().poll()

    return {
        "version": __version__,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "instances": rows,
        "running": sum(1 for row in rows if row["is_running"]),
        "updates": updates,
        "self_update": self_state,
        "watchdog": findings.to_dict(),
    }


def write_status_file(payload: JsonDict, path: Path | str | None = None, base: Path | None = None) -> Path:
    """Write ``payload`` as JSON, atomically; returns the path written to.

    Raises:
        OSError: when the directory cannot be created or the file not written.
            Callers on a background cycle (the tray) swallow it and carry on:
            a status file nobody can write is not a reason to stop managing
            instances.
    """
    target = resolve_path(path, base)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = dict(payload)
    document.setdefault("generated_at", datetime.now(tz=UTC).isoformat())
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target
