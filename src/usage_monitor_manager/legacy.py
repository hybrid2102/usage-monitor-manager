"""Import of the legacy setup into ``manager-config.json`` (PLAN.md section 9.3).

Before the manager existed, the same information lived in three places, each
created by a different tool:

a. ``<exe> - <label>.lnk`` shortcuts next to the executables - the convention
   of the PowerShell updater (``UsageMonitorForClaude.exe - valeria.lnk`` with
   ``--config-dir=%USERPROFILE%\\.claude-valeria``);
b. the monitors' own ``HKCU\\...\\Run`` entries, which carry the executable and
   the ``--config-dir`` of every instance set to start with Windows;
c. the old config editor's profile registry,
   ``%LOCALAPPDATA%\\UsageMonitorConfigEditor\\profiles.json``, which is the
   only source that already has a *human* label
   (``"Claude Valeria"``).

:func:`import_legacy` reads all three, turns them into labelled
:class:`~.manager_config.DeclaredInstance` entries and reports exactly what it
found and what it would add. It runs once at first start (the
``legacy_import_done`` flag in the config), and is available forever after as
``umm import-legacy [--dry-run]`` and from the Manager page.

Nothing here writes to the registry, deletes a shortcut or touches the old
editor's file: the legacy setup keeps working untouched. The only write is the
manager's own config, and only when ``dry_run`` is false.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from . import discovery, kinds, manager_config, platform
from .identity import parse_config_dir
from .manager_config import DeclaredInstance
from .model import JsonDict, MonitorKind

__all__ = [
    "CONFIG_EDITOR_DIR_NAME",
    "PROFILES_FILENAME",
    "ImportReport",
    "LegacyFinding",
    "find_legacy",
    "import_legacy",
    "profiles_path",
    "run_once",
    "suggest_label",
]

CONFIG_EDITOR_DIR_NAME = "UsageMonitorConfigEditor"
PROFILES_FILENAME = "profiles.json"

SOURCE_SHORTCUT = "shortcut"
SOURCE_RUN_KEY = "run_key"
SOURCE_PROFILES = "profiles"

_SOURCE_ORDER = (SOURCE_PROFILES, SOURCE_SHORTCUT, SOURCE_RUN_KEY)
"""Preference order when the same instance is found twice: the config editor
wins, because it is the only source with a label the user typed."""


def profiles_path() -> Path:
    """``%LOCALAPPDATA%\\UsageMonitorConfigEditor\\profiles.json``."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / CONFIG_EDITOR_DIR_NAME / PROFILES_FILENAME


def _normcase(path: Path | str) -> str:
    return os.path.normcase(str(path))


# --------------------------------------------------------------------------- #
# Label heuristics
# --------------------------------------------------------------------------- #


def suggest_label(kind: MonitorKind, config_dir: Path) -> str:
    """A label a human would recognise, from the config directory name.

    ``~/.claude`` (the kind default) is just ``"Claude"``; ``~/.claude-valeria``
    is ``"Claude Valeria"``; an unrelated directory keeps its own name after
    the kind, e.g. ``D:\\work`` under Codex becomes ``"Codex work"``.
    """
    if _normcase(config_dir) == _normcase(kind.default_config_dir):
        return kind.display_name
    name = config_dir.name.lstrip(".")
    default_name = kind.default_config_dir.name.lstrip(".").casefold()
    parts = [part for part in name.replace("_", "-").replace(".", "-").split("-") if part]
    if parts and parts[0].casefold() == default_name:
        parts = parts[1:]
    if not parts:
        return kind.display_name
    tail = " ".join(part[:1].upper() + part[1:] for part in parts)
    return f"{kind.display_name} {tail}"


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LegacyFinding:
    """One instance found in one legacy source, with where it came from."""

    kind: str
    config_dir: Path
    label: str
    source: str
    detail: str
    """Human-readable origin: the ``.lnk`` path, the Run value name, the profile label."""

    @property
    def merge_key(self) -> tuple[str, str]:
        return (self.kind.casefold(), _normcase(self.config_dir))

    def as_declared(self) -> DeclaredInstance:
        return DeclaredInstance(kind=self.kind, config_dir=self.config_dir, label=self.label, enabled=True)

    def to_dict(self) -> JsonDict:
        return {
            "kind": self.kind,
            "config_dir": str(self.config_dir),
            "label": self.label,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What the wizard found, what it would add, and whether it did.

    ``added`` and ``already_known`` partition the deduplicated findings against
    the instances the config already declares, so a dry run prints exactly the
    same lists a real run would write.
    """

    findings: tuple[LegacyFinding, ...] = ()
    added: tuple[DeclaredInstance, ...] = ()
    already_known: tuple[DeclaredInstance, ...] = ()
    notes: tuple[str, ...] = ()
    applied: bool = False

    @property
    def total_found(self) -> int:
        return len(self.findings)

    def to_dict(self) -> JsonDict:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "added": [entry.to_dict() for entry in self.added],
            "already_known": [entry.to_dict() for entry in self.already_known],
            "notes": list(self.notes),
            "applied": self.applied,
            "total_found": self.total_found,
        }

    def lines(self) -> list[str]:
        """The report as the CLI and the Manager page print it."""
        out: list[str] = []
        if not self.findings:
            out.append("nessuna configurazione legacy trovata")
        for finding in self.findings:
            out.append(f"  [{finding.source}] {finding.label}: {finding.config_dir}  ({finding.detail})")
        if self.added:
            verb = "aggiunte" if self.applied else "da aggiungere"
            out.append(f"{verb}: {len(self.added)}")
            for entry in self.added:
                out.append(f"  + {entry.label} ({entry.kind}) {entry.config_dir}")
        if self.already_known:
            out.append(f"gia' presenti: {len(self.already_known)}")
            for entry in self.already_known:
                out.append(f"  = {entry.label or entry.kind} {entry.config_dir}")
        out.extend(f"nota: {note}" for note in self.notes)
        return out


# --------------------------------------------------------------------------- #
# The three sources
# --------------------------------------------------------------------------- #


def _kind_from_exe(exe: Path) -> MonitorKind | None:
    return kinds.kind_for_exe_name(exe.name)


def _findings_from_shortcuts(exe_dirs: Sequence[Path], notes: list[str]) -> list[LegacyFinding]:
    links = discovery.shortcut_paths(exe_dirs)
    if not links:
        return []
    found: list[LegacyFinding] = []
    resolved = platform.read_shortcuts(links)
    if not resolved and links:
        notes.append(f"{len(links)} shortcut trovati ma non leggibili (WScript.Shell non disponibile)")
    for link, (target, args) in sorted(resolved.items()):
        if target is None:
            continue
        kind = _kind_from_exe(target)
        if kind is None:
            continue
        config_dir = parse_config_dir(args) or kind.default_config_dir
        found.append(
            LegacyFinding(
                kind=kind.key,
                config_dir=config_dir,
                label=suggest_label(kind, config_dir),
                source=SOURCE_SHORTCUT,
                detail=link.name,
            )
        )
    return found


def _findings_from_run_entries(run_entries: Mapping[str, str]) -> list[LegacyFinding]:
    found: list[LegacyFinding] = []
    for name, command in sorted(run_entries.items()):
        exe, args = platform.parse_run_command(command)
        if exe is None:
            continue
        kind = _kind_from_exe(exe)
        if kind is None:
            continue
        config_dir = parse_config_dir(args) or kind.default_config_dir
        found.append(
            LegacyFinding(
                kind=kind.key,
                config_dir=config_dir,
                label=suggest_label(kind, config_dir),
                source=SOURCE_RUN_KEY,
                detail=name,
            )
        )
    return found


def _findings_from_profiles(path: Path, notes: list[str]) -> list[LegacyFinding]:
    """Read the old config editor's ``profiles.json``.

    Shape (v0.2.0)::

        {"version": 1, "profiles": [{"key": ..., "label": "Claude Valeria",
                                     "path": "C:\\\\...\\\\.claude-valeria\\\\usage-monitor-settings.json"}]}

    The profile's ``path`` points at the *settings file*, so the config dir is
    its parent; the kind is guessed from that directory's name, the same way
    discovery does it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        payload: object = json.loads(text)
    except ValueError:
        notes.append(f"{path} non e' JSON valido: ignorato")
        return []
    if not isinstance(payload, dict):
        return []
    raw = cast(dict[str, object], payload).get("profiles")
    if not isinstance(raw, list):
        return []
    found: list[LegacyFinding] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        settings_path = entry.get("path")
        if not isinstance(settings_path, str) or not settings_path.strip():
            continue
        config_dir = Path(settings_path).parent
        kind = discovery.kind_for_config_dir_name(config_dir.name)
        if kind is None:
            notes.append(f"profilo '{settings_path}': tipo di monitor non riconosciuto, ignorato")
            continue
        raw_label = entry.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else None
        found.append(
            LegacyFinding(
                kind=kind.key,
                config_dir=config_dir,
                label=label or suggest_label(kind, config_dir),
                source=SOURCE_PROFILES,
                detail=f"profiles.json ({label or 'senza etichetta'})",
            )
        )
    return found


def _default_exe_dirs() -> list[Path]:
    """Directories that hold a known monitor executable, from discovery."""
    dirs: list[Path] = []
    seen: set[str] = set()
    try:
        instances = discovery.discover()
    except Exception:  # noqa: BLE001 - the import must work even if psutil chokes
        return dirs
    for instance in instances:
        if instance.exe_path is None:
            continue
        parent = instance.exe_path.parent
        if _normcase(parent) not in seen:
            seen.add(_normcase(parent))
            dirs.append(parent)
    return dirs


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def find_legacy(
    *,
    exe_dirs: Sequence[Path] | None = None,
    run_entries: Mapping[str, str] | None = None,
    profiles_file: Path | None = None,
) -> tuple[list[LegacyFinding], list[str]]:
    """Scan the three legacy sources; returns ``(findings, notes)``.

    Findings are deduplicated on ``(kind, config dir)`` with the config
    editor's label winning over a generated one, and are ordered by kind
    (registry order) then config dir, so the report is stable.
    """
    notes: list[str] = []
    directories = list(exe_dirs) if exe_dirs is not None else _default_exe_dirs()
    entries = run_entries if run_entries is not None else platform.read_run_entries()
    profiles = profiles_file if profiles_file is not None else profiles_path()

    raw: list[LegacyFinding] = []
    raw.extend(_findings_from_profiles(profiles, notes))
    raw.extend(_findings_from_shortcuts(directories, notes))
    raw.extend(_findings_from_run_entries(entries))

    best: dict[tuple[str, str], LegacyFinding] = {}
    for finding in raw:
        current = best.get(finding.merge_key)
        if current is None or _SOURCE_ORDER.index(finding.source) < _SOURCE_ORDER.index(current.source):
            best[finding.merge_key] = finding

    order = {kind.key: index for index, kind in enumerate(kinds.all_kinds())}
    findings = sorted(best.values(), key=lambda f: (order.get(f.kind, 99), _normcase(f.config_dir)))
    return findings, notes


def import_legacy(
    *,
    dry_run: bool = False,
    config_path: Path | None = None,
    exe_dirs: Sequence[Path] | None = None,
    run_entries: Mapping[str, str] | None = None,
    profiles_file: Path | None = None,
) -> ImportReport:
    """Run the wizard: scan, diff against the config, and (unless dry) save.

    An instance the config already declares is never overwritten - its label
    may have been edited by hand since. ``legacy_import_done`` is set on a real
    run (and only then), so the wizard does not reappear at every start.
    """
    findings, notes = find_legacy(exe_dirs=exe_dirs, run_entries=run_entries, profiles_file=profiles_file)
    config = manager_config.load(config_path)
    known = {entry.merge_key: entry for entry in config.instances}

    added: list[DeclaredInstance] = []
    already: list[DeclaredInstance] = []
    for finding in findings:
        existing = known.get(finding.merge_key)
        if existing is not None:
            already.append(existing)
        else:
            added.append(finding.as_declared())

    if not dry_run:
        updated = config.with_instances([*config.instances, *added])
        try:
            manager_config.save(replace(updated, legacy_import_done=True), config_path)
        except OSError as error:
            notes.append(f"salvataggio non riuscito: {error}")
            return ImportReport(tuple(findings), tuple(added), tuple(already), tuple(notes), applied=False)

    return ImportReport(
        findings=tuple(findings),
        added=tuple(added),
        already_known=tuple(already),
        notes=tuple(notes),
        applied=not dry_run,
    )


def run_once(config_path: Path | None = None, **kwargs: object) -> ImportReport | None:
    """Import at first start only; ``None`` when it already ran.

    Called from the tray's startup path. Any failure is swallowed into a note
    inside the report: a legacy import is never a reason not to start.
    """
    if manager_config.load(config_path).legacy_import_done:
        return None
    exe_dirs = cast("Sequence[Path] | None", kwargs.get("exe_dirs"))
    run_entries = cast("Mapping[str, str] | None", kwargs.get("run_entries"))
    profiles_file = cast("Path | None", kwargs.get("profiles_file"))
    try:
        return import_legacy(
            config_path=config_path,
            exe_dirs=exe_dirs,
            run_entries=run_entries,
            profiles_file=profiles_file,
        )
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        return ImportReport(notes=(f"import legacy non riuscito: {type(error).__name__}: {error}",))
