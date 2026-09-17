"""Domain model: frozen dataclasses shared by every layer of the manager.

The types here mirror PLAN.md section 5. They are pure data: no I/O, no
platform calls. Every type exposes ``to_dict()`` producing JSON-ready values
(``Path`` as ``str``, ``datetime`` as ISO-8601) so the CLI can emit ``--json``
without a serializer layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "Instance",
    "JsonDict",
    "MonitorKind",
    "ProcessPair",
    "ReleaseInfo",
    "ReleaseAsset",
    "UpdatePlan",
]

JsonDict = dict[str, Any]


def _path_str(value: Path | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@dataclass(frozen=True, slots=True)
class MonitorKind:
    """Declarative description of one family member (claude / copilot / codex).

    ``default_config_dir_factory`` is a callable instead of a ``Path`` so the
    registry can be imported without freezing ``Path.home()`` at import time
    (tests monkeypatch the home directory).
    """

    key: str
    display_name: str
    exe_name: str
    pe_product_name: str
    env_var: str
    default_config_dir_factory: Callable[[], Path]
    repo: str
    asset_pattern: str
    sha_sidecar: bool
    settings_schema_id: str
    mutex_base: str
    run_key_base: str

    @property
    def default_config_dir(self) -> Path:
        """Resolve the kind's default config directory (``~/.claude``, ...)."""
        return self.default_config_dir_factory()

    @property
    def exe_stem(self) -> str:
        """Executable name without the ``.exe`` suffix."""
        return Path(self.exe_name).stem

    def to_dict(self) -> JsonDict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "exe_name": self.exe_name,
            "pe_product_name": self.pe_product_name,
            "env_var": self.env_var,
            "default_config_dir": str(self.default_config_dir),
            "repo": self.repo,
            "asset_pattern": self.asset_pattern,
            "sha_sidecar": self.sha_sidecar,
            "settings_schema_id": self.settings_schema_id,
            "mutex_base": self.mutex_base,
            "run_key_base": self.run_key_base,
        }


@dataclass(frozen=True, slots=True)
class ProcessPair:
    """A PyInstaller onefile instance: bootloader parent + application child."""

    bootloader_pid: int | None
    app_pid: int
    started_at: datetime | None = None

    def to_dict(self) -> JsonDict:
        return {
            "bootloader_pid": self.bootloader_pid,
            "app_pid": self.app_pid,
            "started_at": _iso(self.started_at),
        }


@dataclass(frozen=True, slots=True)
class Instance:
    """One monitor instance: a kind plus the config dir that identifies it."""

    kind: MonitorKind
    config_dir: Path
    instance_suffix: str
    exe_path: Path | None = None
    exe_version: str | None = None
    exe_mtime: datetime | None = None
    settings_path: Path | None = None
    settings_exists: bool = False
    shadow_settings_paths: Sequence[Path] = ()
    autostart_entry: bool = False
    autostart_command: str | None = None
    launch_args: Sequence[str] = ()
    launch_env: Mapping[str, str] = field(default_factory=lambda: {})
    label: str | None = None
    sources: Sequence[str] = ()
    config_dir_unknown: bool = False
    processes: ProcessPair | None = None

    @property
    def instance_id(self) -> str:
        """Stable identifier: ``<kind key><suffix>``, e.g. ``claude_578560bbc610``."""
        return f"{self.kind.key}{self.instance_suffix}"

    @property
    def is_running(self) -> bool:
        return self.processes is not None

    def to_dict(self) -> JsonDict:
        return {
            "id": self.instance_id,
            "kind": self.kind.key,
            "config_dir": str(self.config_dir),
            "instance_suffix": self.instance_suffix,
            "exe_path": _path_str(self.exe_path),
            "exe_version": self.exe_version,
            "exe_mtime": _iso(self.exe_mtime),
            "settings_path": _path_str(self.settings_path),
            "settings_exists": self.settings_exists,
            "shadow_settings_paths": [str(p) for p in self.shadow_settings_paths],
            "autostart_entry": self.autostart_entry,
            "autostart_command": self.autostart_command,
            "launch_args": list(self.launch_args),
            "launch_env": dict(self.launch_env),
            "label": self.label,
            "sources": list(self.sources),
            "config_dir_unknown": self.config_dir_unknown,
            "is_running": self.is_running,
            "processes": None if self.processes is None else self.processes.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """A downloadable release asset plus whatever integrity data GitHub gives."""

    name: str
    url: str
    size: int
    digest: str | None = None

    def to_dict(self) -> JsonDict:
        return {"name": self.name, "url": self.url, "size": self.size, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """A GitHub release reduced to what the update engine needs."""

    tag: str
    version: str
    published_at: datetime | None
    html_url: str
    body_markdown: str
    asset: ReleaseAsset | None = None
    sidecar_sha_url: str | None = None
    is_prerelease: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "tag": self.tag,
            "version": self.version,
            "published_at": _iso(self.published_at),
            "html_url": self.html_url,
            "body_markdown": self.body_markdown,
            "asset": None if self.asset is None else self.asset.to_dict(),
            "sidecar_sha_url": self.sidecar_sha_url,
            "is_prerelease": self.is_prerelease,
        }


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """An update is per executable; the restart afterwards is per instance."""

    exe_path: Path
    kind: MonitorKind
    current_version: str | None
    target: ReleaseInfo
    affected_instances: Sequence[Instance] = ()

    def to_dict(self) -> JsonDict:
        return {
            "exe_path": str(self.exe_path),
            "kind": self.kind.key,
            "current_version": self.current_version,
            "target": self.target.to_dict(),
            "affected_instances": [i.to_dict() for i in self.affected_instances],
        }
