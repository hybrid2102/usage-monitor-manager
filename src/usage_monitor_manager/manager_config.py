"""Persistent manager state: ``%LOCALAPPDATA%\\UsageMonitorManager\\manager-config.json``.

What lives here is only what the manager cannot re-derive from the machine
(PLAN.md section 4.3: "nessuno stato implicito"). The truth about *running*
instances always comes from :mod:`.discovery`; this file holds the user's own
declarations:

* ``instances``: instances the user declared or labelled by hand (or that the
  legacy import found). They become discovery source ``(d)`` of PLAN.md
  section 6.1, i.e. dormant entries merged into the discovery result, and they
  are the only place a user-visible ``label`` is stored.
* ``preferences``: check interval, notifications, autostart of the manager,
  prerelease channel, UI language.
* ``last_check`` / ``skipped_versions``: per-repo bookkeeping for the update
  scheduler. ``skipped_versions`` is only *written* from Phase 6 on (the
  "salta versione" command of PLAN.md section 9.5) but the scheduler already
  honours it, so it is stored and round-tripped now.

Writing is atomic and keeps one backup: the new content goes to a temporary
file in the same directory, the previous file (if any) is copied to
``manager-config.json.bak``, then the temporary file is renamed over the
target. A crash therefore leaves either the old file or the new one, never a
half-written JSON document.

Every path is injectable (``path=`` on :func:`load` / :func:`save`) so tests
never touch the real ``%LOCALAPPDATA%``.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from .model import JsonDict

__all__ = [
    "APP_DIR_NAME",
    "CustomKind",
    "Profile",
    "find_profile",
    "remove_custom_kind",
    "remove_profile",
    "upsert_custom_kind",
    "upsert_profile",
    "set_prerelease_repo",
    "skip_version",
    "unskip_version",
    "CONFIG_FILENAME",
    "CONFIG_VERSION",
    "DeclaredInstance",
    "ManagerConfig",
    "Preferences",
    "app_dir",
    "config_path",
    "load",
    "save",
    "set_preference",
    "upsert_instance",
]

APP_DIR_NAME = "UsageMonitorManager"
CONFIG_FILENAME = "manager-config.json"
CONFIG_VERSION = 1

_LANGUAGES = ("it", "en")
_MIN_INTERVAL_HOURS = 1
_MAX_INTERVAL_HOURS = 24 * 7
_MIN_ARCHIVE_KEEP = 0
_MAX_ARCHIVE_KEEP = 20


def app_dir() -> Path:
    """``%LOCALAPPDATA%\\UsageMonitorManager``, falling back to ``~``."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_DIR_NAME


def config_path() -> Path:
    """Path of ``manager-config.json``."""
    return app_dir() / CONFIG_FILENAME


# --------------------------------------------------------------------------- #
# Coercion helpers: the file is user-editable, so nothing is trusted
# --------------------------------------------------------------------------- #


def _as_dict(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    raw = cast(dict[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _as_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _as_str_map(value: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, raw in _as_dict(value).items():
        text = _as_str(raw)
        if text is not None:
            result[str(key)] = text
    return result


def _as_bool_map(value: object) -> dict[str, bool]:
    return {str(key): raw for key, raw in _as_dict(value).items() if isinstance(raw, bool)}


def _as_int(value: object, fallback: int, *, low: int, high: int) -> int:
    number = value if isinstance(value, int) and not isinstance(value, bool) else fallback
    return max(low, min(high, number))


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Preferences:
    """User preferences; the defaults are the ones PLAN.md prescribes."""

    check_interval_hours: int = 6
    notify_updates: bool = True
    autostart_manager: bool = False
    include_prerelease: bool = False
    language: str = "it"
    watchdog_enabled: bool = True
    """Run the instance watchdog of PLAN.md section 9.2 (crash/duplicate/orphan)."""
    auto_restart_crashed: bool = False
    """Restart a crashed instance by itself instead of only notifying.

    Off by default: restarting something the user killed on purpose would be
    worse than a missed notification, and the watchdog cannot tell the two
    apart beyond the manager's own "expected down" set.
    """
    archive_keep: int = 3
    """How many previous builds of each executable ``.umm-archive`` keeps."""
    status_file: str | None = None
    """Where the tray rewrites ``status.json`` after every cycle (section 9.11).

    ``None`` disables the file entirely; the default, because a manager that
    writes into a path nobody asked for is exactly the "stato implicito" the
    plan forbids.
    """
    hotkey: str | None = None
    """Global hotkey toggling the manager window, e.g. ``ctrl+alt+u``
    (section 9.14). ``None`` registers nothing."""

    @classmethod
    def from_dict(cls, payload: object) -> Preferences:
        data = _as_dict(payload)
        default = cls()
        language = _as_str(data.get("language")) or default.language
        return cls(
            check_interval_hours=_as_int(
                data.get("check_interval_hours"),
                default.check_interval_hours,
                low=_MIN_INTERVAL_HOURS,
                high=_MAX_INTERVAL_HOURS,
            ),
            notify_updates=_as_bool(data.get("notify_updates"), default.notify_updates),
            autostart_manager=_as_bool(data.get("autostart_manager"), default.autostart_manager),
            include_prerelease=_as_bool(data.get("include_prerelease"), default.include_prerelease),
            language=language if language in _LANGUAGES else default.language,
            watchdog_enabled=_as_bool(data.get("watchdog_enabled"), default.watchdog_enabled),
            auto_restart_crashed=_as_bool(data.get("auto_restart_crashed"), default.auto_restart_crashed),
            archive_keep=_as_int(
                data.get("archive_keep"),
                default.archive_keep,
                low=_MIN_ARCHIVE_KEEP,
                high=_MAX_ARCHIVE_KEEP,
            ),
            status_file=_as_str(data.get("status_file")),
            hotkey=_as_str(data.get("hotkey")),
        )

    def to_dict(self) -> JsonDict:
        return {
            "check_interval_hours": self.check_interval_hours,
            "notify_updates": self.notify_updates,
            "autostart_manager": self.autostart_manager,
            "include_prerelease": self.include_prerelease,
            "language": self.language,
            "watchdog_enabled": self.watchdog_enabled,
            "auto_restart_crashed": self.auto_restart_crashed,
            "archive_keep": self.archive_keep,
            "status_file": self.status_file,
            "hotkey": self.hotkey,
        }

    def with_value(self, key: str, value: object) -> Preferences:
        """Return a copy with ``key`` set, validated through :meth:`from_dict`.

        Raises:
            KeyError: when ``key`` is not a known preference.
        """
        data = self.to_dict()
        if key not in data:
            known = ", ".join(sorted(data))
            raise KeyError(f"unknown preference {key!r} (known: {known})")
        data[key] = value
        return Preferences.from_dict(data)


@dataclass(frozen=True, slots=True)
class DeclaredInstance:
    """An instance the user declared: kind, config dir, label, enabled flag."""

    kind: str
    config_dir: Path
    label: str | None = None
    enabled: bool = True

    @property
    def merge_key(self) -> tuple[str, str]:
        """Same key discovery merges on: ``(kind, normcase(config dir))``."""
        return (self.kind.casefold(), os.path.normcase(str(self.config_dir)))

    @classmethod
    def from_dict(cls, payload: object) -> DeclaredInstance | None:
        data = _as_dict(payload)
        kind = _as_str(data.get("kind"))
        config_dir = _as_str(data.get("config_dir"))
        if kind is None or config_dir is None:
            return None
        return cls(
            kind=kind,
            config_dir=Path(config_dir),
            label=_as_str(data.get("label")),
            enabled=_as_bool(data.get("enabled"), True),
        )

    def to_dict(self) -> JsonDict:
        return {
            "kind": self.kind,
            "config_dir": str(self.config_dir),
            "label": self.label,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class CustomKind:
    """A user-defined ``github-exe`` registry entry (PLAN.md section 9.9).

    The manager ships three monitors and one portable executable; everything
    else the user wants kept up to date from GitHub Releases is declared here
    and merged into the kind registry at load time by ``kinds.reload``.
    Validation lives in ``kinds.validate_custom_kind``, next to the merge.
    """

    key: str
    display_name: str
    exe_name: str
    repo: str
    asset_pattern: str = ""
    sha_sidecar: bool = False
    pe_product_name: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_pattern:
            object.__setattr__(self, "asset_pattern", self.exe_name)

    @classmethod
    def from_dict(cls, payload: object) -> CustomKind | None:
        data = _as_dict(payload)
        key = _as_str(data.get("key"))
        exe_name = _as_str(data.get("exe_name"))
        repo = _as_str(data.get("repo"))
        if key is None or exe_name is None or repo is None:
            return None
        return cls(
            key=key.casefold(),
            display_name=_as_str(data.get("display_name")) or key,
            exe_name=exe_name,
            repo=repo,
            asset_pattern=_as_str(data.get("asset_pattern")) or exe_name,
            sha_sidecar=_as_bool(data.get("sha_sidecar"), False),
            pe_product_name=_as_str(data.get("pe_product_name")),
        )

    def to_dict(self) -> JsonDict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "exe_name": self.exe_name,
            "repo": self.repo,
            "asset_pattern": self.asset_pattern,
            "sha_sidecar": self.sha_sidecar,
            "pe_product_name": self.pe_product_name,
        }


@dataclass(frozen=True, slots=True)
class Profile:
    """A launch profile: a name and the instance ids it starts (section 9.13)."""

    name: str
    instance_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: object) -> Profile | None:
        data = _as_dict(payload)
        name = _as_str(data.get("name"))
        if name is None:
            return None
        raw = data.get("instance_ids")
        ids: list[str] = []
        if isinstance(raw, list):
            for item in cast(list[object], raw):
                text = _as_str(item)
                if text is not None and text not in ids:
                    ids.append(text)
        return cls(name=name, instance_ids=tuple(ids))

    def to_dict(self) -> JsonDict:
        return {"name": self.name, "instance_ids": list(self.instance_ids)}


@dataclass(frozen=True, slots=True)
class ManagerConfig:
    """The whole file, parsed and validated.

    ``extra`` keeps top-level keys written by a *newer* manager so that
    downgrading does not silently delete them - the same "preserve unknown
    keys" rule the settings store follows for the monitors' own files.
    """

    version: int = CONFIG_VERSION
    instances: tuple[DeclaredInstance, ...] = ()
    preferences: Preferences = field(default_factory=Preferences)
    last_check: Mapping[str, str] = field(default_factory=lambda: {})
    skipped_versions: Mapping[str, str] = field(default_factory=lambda: {})
    prerelease_repos: Mapping[str, bool] = field(default_factory=lambda: {})
    """Per-repo override of ``preferences.include_prerelease`` (PLAN.md section 9.5)."""
    custom_kinds: tuple[CustomKind, ...] = ()
    """User-defined registry entries, merged by ``kinds.reload``."""
    profiles: tuple[Profile, ...] = ()
    """Launch profiles of PLAN.md section 9.13."""
    legacy_import_done: bool = False
    extra: Mapping[str, Any] = field(default_factory=lambda: {})

    @classmethod
    def from_dict(cls, payload: object) -> ManagerConfig:
        """Parse (and migrate) a decoded JSON document; never raises."""
        data = _as_dict(payload)
        known = {
            "version",
            "instances",
            "preferences",
            "last_check",
            "skipped_versions",
            "prerelease_repos",
            "custom_kinds",
            "profiles",
            "legacy_import_done",
        }
        raw_instances = data.get("instances")
        entries: list[DeclaredInstance] = []
        seen: set[tuple[str, str]] = set()
        if isinstance(raw_instances, list):
            for item in cast(list[object], raw_instances):
                entry = DeclaredInstance.from_dict(item)
                if entry is not None and entry.merge_key not in seen:
                    seen.add(entry.merge_key)
                    entries.append(entry)
        custom: list[CustomKind] = []
        custom_seen: set[str] = set()
        raw_custom = data.get("custom_kinds")
        if isinstance(raw_custom, list):
            for item in cast(list[object], raw_custom):
                kind_entry = CustomKind.from_dict(item)
                if kind_entry is not None and kind_entry.key not in custom_seen:
                    custom_seen.add(kind_entry.key)
                    custom.append(kind_entry)
        profiles: list[Profile] = []
        profile_seen: set[str] = set()
        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, list):
            for item in cast(list[object], raw_profiles):
                profile = Profile.from_dict(item)
                if profile is not None and profile.name.casefold() not in profile_seen:
                    profile_seen.add(profile.name.casefold())
                    profiles.append(profile)
        return cls(
            version=CONFIG_VERSION,
            instances=tuple(entries),
            custom_kinds=tuple(custom),
            profiles=tuple(profiles),
            preferences=Preferences.from_dict(data.get("preferences")),
            last_check=_as_str_map(data.get("last_check")),
            skipped_versions=_as_str_map(data.get("skipped_versions")),
            prerelease_repos=_as_bool_map(data.get("prerelease_repos")),
            legacy_import_done=_as_bool(data.get("legacy_import_done"), False),
            extra={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> JsonDict:
        payload: JsonDict = dict(self.extra)
        payload.update(
            {
                "version": self.version,
                "instances": [entry.to_dict() for entry in self.instances],
                "preferences": self.preferences.to_dict(),
                "last_check": dict(self.last_check),
                "skipped_versions": dict(self.skipped_versions),
                "prerelease_repos": dict(self.prerelease_repos),
                "custom_kinds": [entry.to_dict() for entry in self.custom_kinds],
                "profiles": [entry.to_dict() for entry in self.profiles],
                "legacy_import_done": self.legacy_import_done,
            }
        )
        return payload

    def label_for(self, kind: str, config_dir: Path | str) -> str | None:
        """The user label declared for one instance, if any."""
        wanted = (kind.casefold(), os.path.normcase(str(config_dir)))
        for entry in self.instances:
            if entry.merge_key == wanted:
                return entry.label
        return None

    def include_prerelease_for(self, repo: str) -> bool:
        """Prerelease channel for one repo: the override, else the global flag."""
        override = self.prerelease_repos.get(repo)
        return self.preferences.include_prerelease if override is None else override

    def is_skipped(self, repo: str, tag: str) -> bool:
        """True when the user pressed "Salta questa versione" for this release."""
        return bool(tag) and self.skipped_versions.get(repo) == tag

    def with_instances(self, entries: Iterable[DeclaredInstance]) -> ManagerConfig:
        deduped: list[DeclaredInstance] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            if entry.merge_key not in seen:
                seen.add(entry.merge_key)
                deduped.append(entry)
        return replace(self, instances=tuple(deduped))


# --------------------------------------------------------------------------- #
# Load / save
# --------------------------------------------------------------------------- #


def load(path: Path | None = None) -> ManagerConfig:
    """Load the manager state; a missing or broken file yields the defaults.

    A file that cannot be parsed is *not* an error: the manager must still
    start. The unreadable content stays on disk (it is only overwritten by the
    next :func:`save`, which backs it up first).
    """
    target = path or config_path()
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ManagerConfig()
    try:
        payload: object = json.loads(text)
    except ValueError:
        return ManagerConfig()
    return ManagerConfig.from_dict(payload)


def save(config: ManagerConfig, path: Path | None = None) -> Path:
    """Write the state atomically, keeping the previous file as ``.bak``.

    Returns the path written to.

    Raises:
        OSError: when the directory cannot be created or the file not written
            (the caller decides whether that is fatal; the UI only reports it).
    """
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"

    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            backup = target.with_name(target.name + ".bak")
            try:
                backup.write_bytes(target.read_bytes())
            except OSError:
                pass  # a lost backup must not stop the save itself
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target


def set_preference(key: str, value: object, path: Path | None = None) -> ManagerConfig:
    """Load, change one preference, save, return the new config."""
    config = load(path)
    updated = replace(config, preferences=config.preferences.with_value(key, value))
    save(updated, path)
    return updated


def upsert_instance(entry: DeclaredInstance, path: Path | None = None) -> ManagerConfig:
    """Add or replace one declared instance (matched on kind + config dir)."""
    config = load(path)
    others: Sequence[DeclaredInstance] = [i for i in config.instances if i.merge_key != entry.merge_key]
    updated = config.with_instances([*others, entry])
    save(updated, path)
    return updated


def skip_version(repo: str, tag: str, path: Path | None = None) -> ManagerConfig:
    """Record "salta questa versione" for one repo (PLAN.md section 9.5)."""
    config = load(path)
    skipped = dict(config.skipped_versions)
    skipped[repo] = tag
    updated = replace(config, skipped_versions=skipped)
    save(updated, path)
    return updated


def unskip_version(repo: str, path: Path | None = None) -> ManagerConfig:
    """Forget the skipped release of one repo; a repo with none is a no-op."""
    config = load(path)
    skipped = dict(config.skipped_versions)
    skipped.pop(repo, None)
    updated = replace(config, skipped_versions=skipped)
    save(updated, path)
    return updated


def set_prerelease_repo(repo: str, value: bool | None, path: Path | None = None) -> ManagerConfig:
    """Override (or, with ``None``, clear) the prerelease channel of one repo."""
    config = load(path)
    overrides = dict(config.prerelease_repos)
    if value is None:
        overrides.pop(repo, None)
    else:
        overrides[repo] = value
    updated = replace(config, prerelease_repos=overrides)
    save(updated, path)
    return updated


def upsert_custom_kind(entry: CustomKind, path: Path | None = None) -> ManagerConfig:
    """Add or replace one user-defined kind (matched on its key)."""
    config = load(path)
    others = [item for item in config.custom_kinds if item.key != entry.key]
    updated = replace(config, custom_kinds=(*others, entry))
    save(updated, path)
    return updated


def remove_custom_kind(key: str, path: Path | None = None) -> bool:
    """Remove one user-defined kind; ``False`` when there was none."""
    config = load(path)
    wanted = key.casefold()
    others = tuple(item for item in config.custom_kinds if item.key.casefold() != wanted)
    if len(others) == len(config.custom_kinds):
        return False
    save(replace(config, custom_kinds=others), path)
    return True


def find_profile(config: ManagerConfig, name: str) -> Profile | None:
    """The profile called ``name`` (case-insensitive), or ``None``."""
    wanted = name.casefold()
    for profile in config.profiles:
        if profile.name.casefold() == wanted:
            return profile
    return None


def upsert_profile(profile: Profile, path: Path | None = None) -> ManagerConfig:
    """Add or replace one launch profile (matched on its name, folded)."""
    config = load(path)
    wanted = profile.name.casefold()
    others = [item for item in config.profiles if item.name.casefold() != wanted]
    updated = replace(config, profiles=(*others, profile))
    save(updated, path)
    return updated


def remove_profile(name: str, path: Path | None = None) -> bool:
    """Remove one launch profile; ``False`` when there was none."""
    config = load(path)
    wanted = name.casefold()
    others = tuple(item for item in config.profiles if item.name.casefold() != wanted)
    if len(others) == len(config.profiles):
        return False
    save(replace(config, profiles=others), path)
    return True
