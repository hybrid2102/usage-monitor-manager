"""Executable history in ``.umm-archive`` plus version rollback (PLAN.md section 9.4).

``updater.apply`` keeps ``<exe>.bak`` only until the post-update check passes,
then deletes it: five minutes after an update the previous executable is gone
and "torna alla 1.21.0" is impossible. This module is the long-term half of
that story.

Layout, next to the executable the manager updates::

    D:\\Tools\\AI\\
        UsageMonitorForCopilot.exe
        .umm-archive\\
            manifest.json
            UsageMonitorForCopilot-0.3.1.exe
            UsageMonitorForCopilot-0.3.0.exe

Rules:

* one archive directory per executable *directory*, shared by every kind that
  lives there; entries are keyed by the executable stem, and the retention
  count (default 3, ``preferences.archive_keep``) applies **per executable**,
  so archiving Copilot never evicts a Claude build;
* the manifest records ``version``, ``sha256``, ``archived_at`` and
  ``source_release_tag`` for each file. The tag is only known when the manager
  itself installed that build, which is why :func:`record_current` exists: on
  every successful update the updater writes down "this executable is now tag
  T", and when that build is later displaced the tag travels into its archive
  entry;
* archiving **moves**, never copies. The file being archived is the ``.bak``
  the updater was about to delete (or, during a restore, the executable being
  replaced), so nothing is duplicated and a 15 MB executable costs one rename.

:func:`restore` is the user-facing operation ("Ripristina" in the window,
``umm rollback --kind K --version V`` in the CLI). It reuses
``updater.FileSwapper`` for every move, so an antivirus holding the file for a
few seconds is retried exactly as it is during an update, and it returns the
same :class:`~.updater.UpdateResult` the rest of the manager already renders.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from . import process_control, versions
from .integrity import sha256_file
from .model import Instance, JsonDict

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .updater import EventCallback, FileSwapper, UpdateResult, UpdateStep

__all__ = [
    "ARCHIVE_DIR_NAME",
    "DEFAULT_KEEP",
    "MANIFEST_NAME",
    "ArchivedVersion",
    "archive_dir",
    "archive_exe",
    "current_release_tag",
    "current_version",
    "find",
    "list_archived",
    "prune",
    "record_current",
    "restore",
]

ARCHIVE_DIR_NAME = ".umm-archive"
MANIFEST_NAME = "manifest.json"
DEFAULT_KEEP = 3
"""How many previous builds of one executable are kept (PLAN.md section 9.4)."""

MANIFEST_VERSION = 1


def _sleep(seconds: float) -> None:
    """Indirection so a test can restore without waiting between restarts."""
    if seconds > 0:
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class ArchivedVersion:
    """One kept build: the file plus what the manifest remembers about it."""

    path: Path
    exe_stem: str
    version: str
    sha256: str | None = None
    archived_at: datetime | None = None
    source_release_tag: str | None = None

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def to_dict(self) -> JsonDict:
        return {
            "path": str(self.path),
            "file": self.path.name,
            "exe_stem": self.exe_stem,
            "version": self.version,
            "sha256": self.sha256,
            "archived_at": None if self.archived_at is None else self.archived_at.isoformat(),
            "source_release_tag": self.source_release_tag,
            "exists": self.exists,
        }


# --------------------------------------------------------------------------- #
# Paths and manifest
# --------------------------------------------------------------------------- #


def archive_dir(exe_path: Path | str) -> Path:
    """``<exe_dir>/.umm-archive`` for that executable."""
    return Path(exe_path).parent / ARCHIVE_DIR_NAME


def _manifest_path(exe_path: Path | str) -> Path:
    return archive_dir(exe_path) / MANIFEST_NAME


def _read_manifest(exe_path: Path | str) -> JsonDict:
    try:
        raw = _manifest_path(exe_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload: object = json.loads(raw)
    except ValueError:
        return {}
    return _as_dict(payload)


def _write_manifest(exe_path: Path | str, payload: JsonDict) -> None:
    """Atomic write; a manifest we cannot write is reported to the caller."""
    directory = archive_dir(exe_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MANIFEST_NAME
    temp = target.with_name(f".{MANIFEST_NAME}.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, target)


def _as_dict(value: object) -> JsonDict:
    """Any decoded JSON value as a string-keyed dict; anything else is empty."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def _entries(payload: JsonDict) -> list[JsonDict]:
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return []
    return [_as_dict(item) for item in cast("list[object]", raw)]


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _archived_name(exe_path: Path, version: str) -> str:
    """``UsageMonitorForCopilot-0.3.1.exe``; a missing version becomes ``unknown``."""
    stem = Path(exe_path).stem
    safe = "".join(character for character in (version or "unknown") if character.isalnum() or character in "._-")
    return f"{stem}-{safe or 'unknown'}{Path(exe_path).suffix}"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def list_archived(exe_path: Path | str, *, existing_only: bool = True) -> list[ArchivedVersion]:
    """Archived builds of that executable, newest first.

    Only entries for the same executable stem are returned, so a shared
    ``Tools\\AI`` directory does not mix Claude and Codex builds. Entries whose
    file has been deleted by hand are dropped unless ``existing_only`` is off.
    """
    exe = Path(exe_path)
    directory = archive_dir(exe)
    stem = exe.stem.casefold()
    found: list[ArchivedVersion] = []
    for entry in _entries(_read_manifest(exe)):
        if str(entry.get("exe_stem", "")).casefold() != stem:
            continue
        name = _as_text(entry.get("file"))
        if name is None:
            continue
        item = ArchivedVersion(
            path=directory / name,
            exe_stem=str(entry.get("exe_stem", "")),
            version=str(entry.get("version") or "unknown"),
            sha256=_as_text(entry.get("sha256")),
            archived_at=_parse_stamp(entry.get("archived_at")),
            source_release_tag=_as_text(entry.get("source_release_tag")),
        )
        if existing_only and not item.exists:
            continue
        found.append(item)
    found.sort(key=lambda item: item.archived_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return found


def find(exe_path: Path | str, version: str | None = None) -> ArchivedVersion | None:
    """The archived build for ``version``, or the most recent one when ``None``."""
    archived = list_archived(exe_path)
    if not archived:
        return None
    if version is None:
        return archived[0]
    wanted = versions.format_version(version)
    for item in archived:
        if versions.format_version(item.version) == wanted:
            return item
    return None


def current_version(exe_path: Path | str) -> str | None:
    """The version the manager last installed, per the manifest.

    Used as a fallback when the PE carries no version resource, so a restore
    still archives the displaced build under a real number instead of
    ``unknown``.
    """
    current = _as_dict(_read_manifest(exe_path).get("current"))
    return _as_text(current.get("version"))


def current_release_tag(exe_path: Path | str) -> str | None:
    """The release tag the manager last installed on that executable, if any."""
    current = _as_dict(_read_manifest(exe_path).get("current"))
    return _as_text(current.get("tag"))


def record_current(exe_path: Path | str, *, version: str | None, tag: str | None) -> None:
    """Remember which release the executable currently holds.

    Called by ``updater.apply`` after a successful swap. The value is read back
    by :func:`archive_exe` the next time this build is displaced, which is how
    ``source_release_tag`` gets filled in without guessing.
    """
    payload = _read_manifest(exe_path)
    payload["version"] = MANIFEST_VERSION
    payload.setdefault("entries", [])
    payload["current"] = {
        "exe": Path(exe_path).name,
        "version": version,
        "tag": tag,
        "installed_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        _write_manifest(exe_path, payload)
    except OSError:
        return


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def archive_exe(
    exe_path: Path | str,
    version: str | None,
    *,
    source_path: Path | None = None,
    source_release_tag: str | None = None,
    keep: int = DEFAULT_KEEP,
    move: Callable[[Path, Path], None] | None = None,
) -> ArchivedVersion | None:
    """Move a displaced build into the archive and record it in the manifest.

    ``source_path`` is the file to archive - the ``<exe>.bak`` the updater is
    about to delete, or the executable itself during a restore. It defaults to
    the ``.bak``. ``None`` is returned (never an exception) when there is
    nothing to archive or the move fails: losing the archive must never fail an
    update that already succeeded.
    """
    exe = Path(exe_path)
    source = Path(source_path) if source_path is not None else exe.with_name(f"{exe.name}.bak")
    if not source.is_file():
        return None

    label = versions.display_version(version) or "unknown"
    directory = archive_dir(exe)
    destination = directory / _archived_name(exe, label)
    try:
        digest = sha256_file(source)
    except OSError:
        digest = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        if move is not None:
            move(source, destination)
        else:
            os.replace(source, destination)
    except OSError:
        return None

    tag = source_release_tag or current_release_tag(exe)
    item = ArchivedVersion(
        path=destination,
        exe_stem=exe.stem,
        version=label,
        sha256=digest,
        archived_at=datetime.now(tz=UTC),
        source_release_tag=tag,
    )

    payload = _read_manifest(exe)
    payload["version"] = MANIFEST_VERSION
    kept = [
        entry
        for entry in _entries(payload)
        if not (
            str(entry.get("exe_stem", "")).casefold() == exe.stem.casefold()
            and str(entry.get("file", "")) == destination.name
        )
    ]
    kept.append(
        {
            "file": destination.name,
            "exe_stem": item.exe_stem,
            "version": item.version,
            "sha256": item.sha256,
            "archived_at": None if item.archived_at is None else item.archived_at.isoformat(),
            "source_release_tag": item.source_release_tag,
        }
    )
    payload["entries"] = kept
    try:
        _write_manifest(exe, payload)
    except OSError:
        return item
    prune(exe, keep=keep)
    return item


def prune(exe_path: Path | str, *, keep: int = DEFAULT_KEEP) -> list[ArchivedVersion]:
    """Keep the ``keep`` newest builds of that executable; return what went.

    Entries whose file has disappeared are dropped from the manifest as well,
    so a directory cleaned by hand does not leave ghosts in the list.
    """
    exe = Path(exe_path)
    limit = max(0, keep)
    stem = exe.stem.casefold()

    payload = _read_manifest(exe)
    mine = list_archived(exe, existing_only=False)
    removed: list[ArchivedVersion] = []
    doomed: set[str] = set()
    survivors = [item for item in mine if item.exists]
    for item in mine:
        if not item.exists:
            doomed.add(item.path.name)
    for item in survivors[limit:]:
        try:
            item.path.unlink(missing_ok=True)
        except OSError:
            continue
        doomed.add(item.path.name)
        removed.append(item)

    if not doomed:
        return removed
    payload["entries"] = [
        entry
        for entry in _entries(payload)
        if not (str(entry.get("exe_stem", "")).casefold() == stem and str(entry.get("file", "")) in doomed)
    ]
    payload["version"] = MANIFEST_VERSION
    try:
        _write_manifest(exe, payload)
    except OSError:
        pass
    return removed


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #


def restore(
    exe_path: Path | str,
    version: str | None = None,
    *,
    instances: Sequence[Instance] | None = None,
    restart: bool = True,
    keep: int = DEFAULT_KEEP,
    on_event: EventCallback | None = None,
    swapper: FileSwapper | None = None,
    gap_seconds: float = process_control.DEFAULT_GAP_SECONDS,
) -> UpdateResult:
    """Put an archived build back: stop, swap, restart, archive what was there.

    The executable currently in place is archived too, so a restore is
    reversible: going back to 0.3.1 leaves 0.3.2 in the archive and
    ``umm rollback --kind copilot --version 0.3.2`` returns.

    Returns an :class:`~.updater.UpdateResult` (``ok`` plus the event log), the
    same shape ``umm update`` and the window already render.
    """
    from .updater import FileSwapper as _Swapper
    from .updater import SwapError, UpdateEvent, UpdateResult

    exe = Path(exe_path)
    events: list[UpdateEvent] = []

    def log(step: UpdateStep, message: str) -> None:
        event = UpdateEvent(step=step, message=message)
        events.append(event)
        if on_event is not None:
            on_event(event)

    affected = _instances_for(exe, instances)
    kind_key = affected[0].kind.key if affected else ""

    def result(ok: bool, detail: str, **extra: object) -> UpdateResult:
        log("done" if ok else "abort", detail)
        return UpdateResult(
            exe_path=exe,
            kind=kind_key,
            ok=ok,
            detail=detail,
            from_version=versions.display_version(versions.exe_version(exe)),
            events=tuple(events),
            **extra,  # type: ignore[arg-type]
        )

    target = find(exe, version)
    if target is None:
        available = ", ".join(item.version for item in list_archived(exe)) or "none"
        asked = f" {version}" if version else ""
        return result(False, f"no archived build{asked} for {exe.name} (available: {available})")

    swap = swapper if swapper is not None else _Swapper(on_retry=lambda message: log("restore", message))
    current = versions.display_version(versions.exe_version(exe)) or current_version(exe)
    log("plan", f"restoring {exe.name} {current or '?'} -> {target.version} from {target.path.name}")

    was_running = [instance for instance in affected if instance.is_running]
    stopped: list[str] = []
    for instance in was_running:
        outcome = process_control.stop(instance)
        log("stop", f"{instance.instance_id}: {outcome.detail}")
        if not outcome.ok:
            return result(
                False,
                f"aborted: could not stop {instance.instance_id} ({outcome.detail}); the executable was not touched",
                stopped=tuple(stopped),
            )
        stopped.append(instance.instance_id)

    if exe.is_file():
        kept = archive_exe(exe, current, source_path=exe, keep=keep, move=swap.move)
        if kept is None:
            return result(
                False,
                f"aborted: {exe.name} could not be moved into {ARCHIVE_DIR_NAME}; nothing was changed",
                stopped=tuple(stopped),
            )
        log("backup", f"{exe.name} ({current or '?'}) archived as {kept.path.name}")

    try:
        swap.move(target.path, exe)
    except SwapError as error:
        return result(False, f"the restore failed: {error}", stopped=tuple(stopped))
    log("swap", f"{target.path.name} is now {exe.name}")
    _forget(exe, target)
    record_current(exe, version=target.version, tag=target.source_release_tag)

    restarted: list[str] = []
    if restart:
        for index, instance in enumerate(was_running):
            if index:
                _sleep(gap_seconds)  # the same two-second gap between starts the updater uses
            outcome = process_control.start(instance)
            log("start", f"{instance.instance_id}: {outcome.detail}")
            if outcome.ok:
                restarted.append(instance.instance_id)
    elif was_running:
        log("start", "--no-restart: the instances stay stopped")

    installed = versions.display_version(versions.exe_version(exe)) or target.version
    return result(
        True,
        f"restored {target.version} (the executable now reports {installed})",
        stopped=tuple(stopped),
        restarted=tuple(restarted),
    )


def _forget(exe_path: Path, item: ArchivedVersion) -> None:
    """Drop one entry from the manifest (its file has just been promoted)."""
    payload = _read_manifest(exe_path)
    payload["entries"] = [
        entry
        for entry in _entries(payload)
        if not (
            str(entry.get("exe_stem", "")).casefold() == item.exe_stem.casefold()
            and str(entry.get("file", "")) == item.path.name
        )
    ]
    try:
        _write_manifest(exe_path, payload)
    except OSError:
        return


def _instances_for(exe_path: Path, instances: Sequence[Instance] | None) -> list[Instance]:
    from . import discovery

    known = list(instances) if instances is not None else list(discovery.discover())
    wanted = os.path.normcase(str(exe_path))
    return [i for i in known if i.exe_path is not None and os.path.normcase(str(i.exe_path)) == wanted]
