"""Instance discovery: running processes, Run keys, shortcuts, settings files.

Implements PLAN.md section 6.1 step by step:

1. scan the process table with psutil and keep the candidates whose executable
   basename is in the kind registry, or whose PE ``ProductName`` starts with
   ``Usage Monitor for`` (so a future kind or a rename is still seen);
2. collapse the PyInstaller bootloader/app pairs;
3. resolve the config dir from ``--config-dir``, else from the kind's
   environment variable read out of the process, else the kind default - always
   recording *which* source answered;
4. compute the instance suffix with the monitors' own algorithm;
5. replay the monitors' settings search order and report both the active file
   and the shadowed candidates;
6. add the dormant instances (Run keys, legacy ``<exe> - *.lnk`` shortcuts,
   stray ``usage-monitor-settings.json`` files) and merge them with the running
   ones;
7. emit a stable, serializable, sorted list.

This module only reads. Nothing here starts, stops or writes anything.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psutil

from . import kinds, platform, versions
from .identity import config_dir_suffix, normalize_config_dir, parse_config_dir
from .model import ConfigDirSource, Instance, MonitorKind, ProcessPair

__all__ = [
    "PRODUCT_NAME_PREFIX",
    "SETTINGS_FILENAME",
    "ProcessSnapshot",
    "discover",
    "discover_instances",
    "scan_processes",
    "settings_candidates",
]

SETTINGS_FILENAME = "usage-monitor-settings.json"
PRODUCT_NAME_PREFIX = "usage monitor for"

SOURCE_PROCESS = "process"
SOURCE_RUN_KEY = "run_key"
SOURCE_SHORTCUT = "shortcut"
SOURCE_SETTINGS_FILE = "settings_file"


def _normcase(path: Path | str) -> str:
    return os.path.normcase(str(path))


# --------------------------------------------------------------------------- #
# Process scan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """One process as discovery needs it, decoupled from psutil.

    ``read_environ`` is a callable rather than a mapping because reading the
    environment of another process is the expensive, failure-prone part: it is
    only called for the processes that actually need it, and
    ``psutil.AccessDenied`` is handled at the call site. Tests build these
    snapshots directly.
    """

    pid: int
    name: str
    ppid: int | None = None
    exe: Path | None = None
    cmdline: tuple[str, ...] = ()
    create_time: float | None = None
    read_environ: Callable[[], Mapping[str, str]] = dict


def scan_processes() -> list[ProcessSnapshot]:
    """Snapshot the process table, skipping processes that vanish or say no."""
    snapshots: list[ProcessSnapshot] = []
    for proc in psutil.process_iter(["name", "ppid", "exe", "cmdline", "create_time"]):
        try:
            info: dict[str, Any] = proc.info  # pyright: ignore[reportUnknownMemberType]
            exe = cast(str | None, info.get("exe"))
            cmdline = cast(list[str] | None, info.get("cmdline"))
            snapshots.append(
                ProcessSnapshot(
                    pid=proc.pid,
                    name=cast(str | None, info.get("name")) or "",
                    ppid=cast(int | None, info.get("ppid")),
                    exe=Path(exe) if exe else None,
                    cmdline=tuple(cmdline or ()),
                    create_time=cast(float | None, info.get("create_time")),
                    read_environ=proc.environ,
                )
            )
        except (psutil.Error, OSError):
            continue
    return snapshots


class _KindResolver:
    """Map an executable path to a kind, caching the PE reads."""

    def __init__(self) -> None:
        self._cache: dict[str, MonitorKind | None] = {}

    def __call__(self, exe: Path | None) -> MonitorKind | None:
        if exe is None:
            return None
        key = _normcase(exe)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = self._resolve(exe)
        return self._cache[key]

    @staticmethod
    def _resolve(exe: Path) -> MonitorKind | None:
        by_name = kinds.kind_for_exe_name(exe.name)
        product = platform.file_product_name(exe)
        if product is None:
            # No version resource (or the file is gone): the name is all we have.
            return by_name
        by_product = kinds.kind_for_product_name(product)
        if by_product is not None:
            return by_product
        if product.strip().casefold().startswith(PRODUCT_NAME_PREFIX):
            # A future or renamed family member: keep it only if the name maps.
            return by_name
        # Same file name, different product: somebody else's executable.
        return None


def _collapse_pairs(
    snapshots: Sequence[ProcessSnapshot],
) -> list[tuple[ProcessSnapshot, ProcessSnapshot | None]]:
    """Return ``(app, bootloader | None)`` pairs for PyInstaller onefile apps.

    A candidate whose parent is another candidate with the same executable is
    the app; that parent is the bootloader. A candidate whose parent is gone (or
    is something else entirely) is an orphan app and is kept on its own.
    """
    by_pid = {snap.pid: snap for snap in snapshots}
    bootloader_pids: set[int] = set()
    parents: dict[int, ProcessSnapshot] = {}
    for snap in snapshots:
        parent = by_pid.get(snap.ppid) if snap.ppid is not None else None
        if parent is None or parent.pid == snap.pid or parent.exe is None or snap.exe is None:
            continue
        if _normcase(parent.exe) == _normcase(snap.exe):
            bootloader_pids.add(parent.pid)
            parents[snap.pid] = parent
    return [(snap, parents.get(snap.pid)) for snap in snapshots if snap.pid not in bootloader_pids]


def _started_at(snapshot: ProcessSnapshot) -> datetime | None:
    if snapshot.create_time is None:
        return None
    try:
        return datetime.fromtimestamp(snapshot.create_time, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _config_dir_from_process(
    snapshot: ProcessSnapshot,
    kind: MonitorKind,
) -> tuple[Path, ConfigDirSource, dict[str, str]]:
    """Resolve config dir: command line, then environment, then the default."""
    from_cmdline = parse_config_dir(snapshot.cmdline)
    if from_cmdline is not None:
        return from_cmdline, "cmdline", {}
    try:
        environ = snapshot.read_environ()
    except (psutil.Error, OSError):
        # PLAN.md section 8: mark it instead of guessing.
        return kind.default_config_dir, "unknown", {}
    raw = environ.get(kind.env_var)
    from_env = normalize_config_dir(raw) if raw else None
    if from_env is not None:
        return from_env, "env", {kind.env_var: str(from_env)}
    return kind.default_config_dir, "default", {}


# --------------------------------------------------------------------------- #
# Settings files
# --------------------------------------------------------------------------- #


def settings_candidates(kind: MonitorKind, config_dir: Path, exe_path: Path | None) -> list[Path]:
    """The monitors' settings search order, most specific first.

    ``config_dir`` is only searched when it differs from the kind default (the
    monitors skip it otherwise), then the executable's own directory, then the
    default directory.
    """
    default_dir = kind.default_config_dir
    candidates: list[Path] = []
    if _normcase(config_dir) != _normcase(default_dir):
        candidates.append(config_dir / SETTINGS_FILENAME)
    if exe_path is not None:
        candidates.append(exe_path.parent / SETTINGS_FILENAME)
    candidates.append(default_dir / SETTINGS_FILENAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normcase(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _exists(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _resolve_settings(candidates: Sequence[Path]) -> tuple[Path | None, list[Path]]:
    """Return ``(active, shadowed)``: the first existing file and the rest."""
    existing = [candidate for candidate in candidates if _exists(candidate)]
    if not existing:
        return None, []
    return existing[0], existing[1:]


# --------------------------------------------------------------------------- #
# Drafts and merging
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Draft:
    """Mutable accumulator: one per ``(kind, config dir)`` merge key."""

    kind: MonitorKind
    config_dir: Path
    config_dir_source: ConfigDirSource
    sources: list[str] = field(default_factory=lambda: [])
    exe_path: Path | None = None
    launch_args: tuple[str, ...] = ()
    launch_env: dict[str, str] = field(default_factory=lambda: {})
    autostart_command: str | None = None
    processes: ProcessPair | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind.key, _normcase(self.config_dir))

    def add_source(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)


def _merge(drafts: dict[tuple[str, str], _Draft], incoming: _Draft) -> None:
    """Merge a draft into the map; a running instance always wins the details."""
    existing = drafts.get(incoming.key)
    if existing is None:
        drafts[incoming.key] = incoming
        return

    for source in incoming.sources:
        existing.add_source(source)
    if existing.processes is None and incoming.processes is not None:
        # The running view is authoritative for everything it knows.
        existing.processes = incoming.processes
        existing.config_dir_source = incoming.config_dir_source
        existing.exe_path = incoming.exe_path or existing.exe_path
        existing.launch_args = incoming.launch_args or existing.launch_args
        existing.launch_env = incoming.launch_env or existing.launch_env
        return
    existing.exe_path = existing.exe_path or incoming.exe_path
    existing.launch_args = existing.launch_args or incoming.launch_args
    if not existing.launch_env:
        existing.launch_env = incoming.launch_env


# --------------------------------------------------------------------------- #
# Dormant instances
# --------------------------------------------------------------------------- #


def _drafts_from_run_entries(
    run_entries: Mapping[str, str],
    resolve_kind: _KindResolver,
) -> list[_Draft]:
    drafts: list[_Draft] = []
    for command in run_entries.values():
        exe, args = platform.parse_run_command(command)
        if exe is None:
            continue
        kind = resolve_kind(exe) or kinds.kind_for_exe_name(exe.name)
        if kind is None:
            continue
        from_args = parse_config_dir(args)
        config_dir = from_args or kind.default_config_dir
        drafts.append(
            _Draft(
                kind=kind,
                config_dir=config_dir,
                config_dir_source="cmdline" if from_args else "default",
                sources=[SOURCE_RUN_KEY],
                exe_path=exe,
                launch_args=tuple(args),
            )
        )
    return drafts


def _shortcut_paths(exe_dirs: Iterable[Path]) -> list[Path]:
    """Legacy convention of the PowerShell updater: ``<exe> - <label>.lnk``."""
    prefixes = tuple(f"{kind.exe_name.casefold()} - " for kind in kinds.all_kinds())
    prefixes += tuple(f"{kind.exe_stem.casefold()} - " for kind in kinds.all_kinds())
    found: list[Path] = []
    seen: set[str] = set()
    for directory in exe_dirs:
        try:
            entries = sorted(directory.glob("*.lnk"))
        except OSError:
            continue
        for entry in entries:
            if entry.name.casefold().startswith(prefixes) and _normcase(entry) not in seen:
                seen.add(_normcase(entry))
                found.append(entry)
    return found


def _drafts_from_shortcuts(exe_dirs: Iterable[Path], resolve_kind: _KindResolver) -> list[_Draft]:
    links = _shortcut_paths(exe_dirs)
    if not links:
        return []
    drafts: list[_Draft] = []
    for target, args in platform.read_shortcuts(links).values():
        if target is None:
            continue
        kind = resolve_kind(target) or kinds.kind_for_exe_name(target.name)
        if kind is None:
            continue
        from_args = parse_config_dir(args)
        drafts.append(
            _Draft(
                kind=kind,
                config_dir=from_args or kind.default_config_dir,
                config_dir_source="cmdline" if from_args else "default",
                sources=[SOURCE_SHORTCUT],
                exe_path=target,
                launch_args=tuple(args),
            )
        )
    return drafts


def _settings_roots() -> list[Path]:
    roots: list[Path] = [Path.home()]
    for variable in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = _normcase(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _kind_for_config_dir_name(name: str) -> MonitorKind | None:
    """Guess the kind from a config directory name (``.claude-valeria``)."""
    folded = name.casefold()
    best: MonitorKind | None = None
    best_length = 0
    for kind in kinds.all_kinds():
        default_name = kind.default_config_dir.name.casefold()
        if default_name and folded.startswith(default_name) and len(default_name) > best_length:
            best, best_length = kind, len(default_name)
    return best


def _drafts_from_settings_files(exe_by_kind: Mapping[str, Path]) -> list[_Draft]:
    """One level deep under ``~``, ``%LOCALAPPDATA%``, ``%APPDATA%``.

    Same rule as the old config editor (PLAN.md section 1.5): never a disk scan.
    """
    drafts: list[_Draft] = []
    for root in _settings_roots():
        try:
            children = sorted(entry for entry in root.iterdir() if entry.is_dir())
        except OSError:
            continue
        for child in children:
            if not _exists(child / SETTINGS_FILENAME):
                continue
            kind = _kind_for_config_dir_name(child.name)
            if kind is None:
                continue
            drafts.append(
                _Draft(
                    kind=kind,
                    config_dir=child,
                    config_dir_source="default",
                    sources=[SOURCE_SETTINGS_FILE],
                    exe_path=exe_by_kind.get(kind.key),
                )
            )
    return drafts


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def _running_drafts(resolve_kind: _KindResolver) -> list[_Draft]:
    candidates = [snap for snap in scan_processes() if resolve_kind(snap.exe) is not None]
    drafts: list[_Draft] = []
    for app, bootloader in _collapse_pairs(candidates):
        kind = resolve_kind(app.exe)
        if kind is None:  # pragma: no cover - filtered above
            continue
        config_dir, source, launch_env = _config_dir_from_process(app, kind)
        drafts.append(
            _Draft(
                kind=kind,
                config_dir=config_dir,
                config_dir_source=source,
                sources=[SOURCE_PROCESS],
                exe_path=app.exe,
                launch_args=tuple(app.cmdline[1:]),
                launch_env=launch_env,
                processes=ProcessPair(
                    bootloader_pid=None if bootloader is None else bootloader.pid,
                    app_pid=app.pid,
                    started_at=_started_at(bootloader or app),
                ),
            )
        )
    return drafts


def _finalize(draft: _Draft, run_entries: Mapping[str, str]) -> Instance:
    suffix = config_dir_suffix(draft.config_dir, draft.kind.default_config_dir)
    active, shadowed = _resolve_settings(settings_candidates(draft.kind, draft.config_dir, draft.exe_path))
    run_name = f"{draft.kind.run_key_base}{suffix}"
    autostart_command = draft.autostart_command
    for name, command in run_entries.items():
        if name.casefold() == run_name.casefold():
            autostart_command = command
            break
    return Instance(
        kind=draft.kind,
        config_dir=draft.config_dir,
        instance_suffix=suffix,
        exe_path=draft.exe_path,
        exe_version=versions.exe_version(draft.exe_path),
        exe_mtime=versions.exe_mtime(draft.exe_path),
        settings_path=active,
        settings_exists=active is not None,
        shadow_settings_paths=tuple(shadowed),
        autostart_entry=autostart_command is not None,
        autostart_command=autostart_command,
        launch_args=draft.launch_args,
        launch_env=dict(draft.launch_env),
        sources=tuple(draft.sources),
        config_dir_source=draft.config_dir_source,
        processes=draft.processes,
    )


def discover(include_dormant: bool = True) -> list[Instance]:
    """Every instance of the family, running first, then the dormant ones.

    The result is sorted by kind (registry order) and then by config dir, and is
    fully serializable through :meth:`Instance.to_dict`. Merge key is
    ``(kind, normcase(config dir))``: the same instance seen as a process, a Run
    key and a settings file appears once, with all three sources listed.
    """
    resolve_kind = _KindResolver()
    run_entries = platform.read_run_entries()

    drafts: dict[tuple[str, str], _Draft] = {}
    for draft in _running_drafts(resolve_kind):
        _merge(drafts, draft)

    if include_dormant:
        for draft in _drafts_from_run_entries(run_entries, resolve_kind):
            _merge(drafts, draft)

        exe_dirs = [draft.exe_path.parent for draft in drafts.values() if draft.exe_path is not None]
        for draft in _drafts_from_shortcuts(exe_dirs, resolve_kind):
            _merge(drafts, draft)

        exe_by_kind: dict[str, Path] = {}
        for draft in drafts.values():
            if draft.exe_path is not None:
                exe_by_kind.setdefault(draft.kind.key, draft.exe_path)
        for draft in _drafts_from_settings_files(exe_by_kind):
            _merge(drafts, draft)

    order = {kind.key: index for index, kind in enumerate(kinds.all_kinds())}
    instances = [_finalize(draft, run_entries) for draft in drafts.values()]
    instances.sort(key=lambda instance: (order.get(instance.kind.key, 99), _normcase(instance.config_dir)))
    return instances


def discover_instances() -> Sequence[Instance]:
    """Backwards-compatible alias of :func:`discover`."""
    return discover()
