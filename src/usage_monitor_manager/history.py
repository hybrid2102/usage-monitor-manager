"""Dated settings history, diff, restore and profile export/import.

PLAN.md section 9.6: every successful save archives a copy under
``%LOCALAPPDATA%\\UsageMonitorManager\\history\\<instance id>\\<stamp>.json``,
the window shows the versions with a diff against the current file, and a
version can be restored (through :func:`store.save`, so the restore is itself
archived) or exported to a portable JSON profile.

The archive is a plain directory of JSON files on purpose: it survives the
manager being uninstalled, and a user can copy one file back by hand.

The timestamp is ISO-8601 with ``-`` instead of ``:`` (Windows forbids colons
in file names) and always UTC, so the lexicographic order of the file names is
the chronological order of the saves.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .manager_config import app_dir
from .model import Instance, JsonDict

__all__ = [
    "KEEP_VERSIONS",
    "DiffLine",
    "Version",
    "archive_settings",
    "diff",
    "export",
    "history_dir",
    "import_",
    "list_versions",
    "prune",
    "read_version",
    "restore",
]

KEEP_VERSIONS = 50
"""How many archived versions are kept per instance (oldest pruned first)."""

STAMP_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"


def history_dir(instance: Instance) -> Path:
    """``…\\UsageMonitorManager\\history\\<instance id>``.

    The directory name is the instance id (``claude``,
    ``claude_578560bbc610``, ...), i.e. the kind plus the config-dir suffix.
    """
    return app_dir() / "history" / instance.instance_id


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(tz=UTC)).strftime(STAMP_FORMAT)


@dataclass(frozen=True, slots=True)
class Version:
    """One archived settings snapshot."""

    stamp: str
    path: Path
    size: int
    saved_at: datetime | None

    def to_dict(self) -> JsonDict:
        return {
            "stamp": self.stamp,
            "path": str(self.path),
            "size": self.size,
            "saved_at": None if self.saved_at is None else self.saved_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DiffLine:
    """One key-level difference between two settings snapshots."""

    key: str
    change: str
    """``added``, ``removed`` or ``changed``."""
    before: Any = None
    after: Any = None

    def to_dict(self) -> JsonDict:
        return {"key": self.key, "change": self.change, "before": self.before, "after": self.after}


def _parse_stamp(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def archive_settings(
    instance: Instance,
    data: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> Path | None:
    """File a copy of ``data`` in the instance's history and prune the oldest.

    Never raises: an archive that cannot be written must not make a save fail.
    """
    from .settings.store import dumps

    directory = history_dir(instance)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{_stamp(now)}.json"
        counter = 1
        while target.exists():  # two saves inside the same microsecond
            target = directory / f"{_stamp(now)}-{counter}.json"
            counter += 1
        target.write_text(dumps(data), encoding="utf-8", newline="\n")
    except OSError:
        return None
    prune(instance)
    return target


def list_versions(instance: Instance) -> list[Version]:
    """Archived versions, newest first."""
    directory = history_dir(instance)
    try:
        files = sorted(directory.glob("*.json"), reverse=True)
    except OSError:
        return []
    versions: list[Version] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        stamp = path.stem
        versions.append(Version(stamp=stamp, path=path, size=size, saved_at=_parse_stamp(stamp)))
    return versions


def read_version(instance: Instance, stamp: str) -> JsonDict | None:
    """The data of one archived version (``None`` when missing or broken)."""
    path = history_dir(instance) / f"{stamp}.json"
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    from .settings.store import as_json_object

    return as_json_object(payload)


def prune(instance: Instance, keep: int = KEEP_VERSIONS) -> list[Path]:
    """Delete everything beyond the ``keep`` most recent versions."""
    removed: list[Path] = []
    for version in list_versions(instance)[keep:]:
        try:
            version.path.unlink()
        except OSError:
            continue
        removed.append(version.path)
    return removed


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[DiffLine]:
    """Key-level difference between two settings mappings, sorted by key."""
    lines: list[DiffLine] = []
    for key in sorted(set(before) | set(after)):
        in_before = key in before
        in_after = key in after
        if in_before and not in_after:
            lines.append(DiffLine(key=key, change="removed", before=before[key]))
        elif in_after and not in_before:
            lines.append(DiffLine(key=key, change="added", after=after[key]))
        elif before[key] != after[key]:
            lines.append(DiffLine(key=key, change="changed", before=before[key], after=after[key]))
    return lines


def restore(instance: Instance, stamp: str, *, path: Path | None = None) -> JsonDict | None:
    """Write an archived version back to the instance's settings file.

    The restore goes through :func:`store.save`, so it keeps a ``.bak`` of the
    file it replaces and is itself archived: nothing is ever lost by going
    back.
    """
    from .settings.store import save, suggested_path

    data = read_version(instance, stamp)
    if data is None:
        return None
    target = path or instance.settings_path or suggested_path(instance)
    save(target, data, instance=instance)
    return data


def export(instance: Instance, dest: Path | str, *, data: Mapping[str, Any] | None = None) -> Path:
    """Write the instance's current settings to ``dest`` as a portable profile."""
    from .settings.store import dumps, load, suggested_path

    payload = dict(data) if data is not None else load(instance.settings_path or suggested_path(instance)).data
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(payload), encoding="utf-8", newline="\n")
    return target


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of :func:`import_`: what was read and whether it was written."""

    ok: bool
    detail: str
    data: JsonDict
    errors: list[tuple[str, str]]
    unknown_keys: list[str]
    path: Path | None = None

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "data": self.data,
            "errors": [{"key": key, "message": message} for key, message in self.errors],
            "unknown_keys": list(self.unknown_keys),
            "path": None if self.path is None else str(self.path),
        }


def import_(instance: Instance, src: Path | str, *, apply: bool = True) -> ImportResult:
    """Read a profile, validate it, and (by default) install it.

    Validation comes first: a profile exported from another machine may carry
    a key this kind rejects, and the user must see that before the file lands
    where the monitor reads it.
    """
    from .settings.store import load, save, suggested_path, validate

    loaded = load(Path(src))
    if not loaded.exists:
        return ImportResult(False, f"{src} non esiste", {}, [], [])
    if loaded.error is not None:
        return ImportResult(False, loaded.error, {}, [], [])

    report = validate(instance.kind.key, loaded.data)
    if report.errors:
        return ImportResult(
            False,
            "il profilo contiene valori non validi",
            loaded.data,
            report.errors,
            report.unknown_keys,
        )
    if not apply:
        return ImportResult(True, "profilo valido (non applicato)", report.normalized, [], report.unknown_keys)

    target = instance.settings_path or suggested_path(instance)
    save(target, report.normalized, instance=instance)
    return ImportResult(
        True,
        f"profilo importato in {target}",
        report.normalized,
        [],
        report.unknown_keys,
        path=target,
    )


def versions_payload(instance: Instance) -> list[JsonDict]:
    """JSON-ready version list for the window's Storico tab."""
    return [version.to_dict() for version in list_versions(instance)]


def diff_payload(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[JsonDict]:
    """JSON-ready :func:`diff`."""
    return [line.to_dict() for line in diff(before, after)]
