"""Kind registry: data, not logic.

Two families live here (PLAN.md sections 4.3 and 9.9).

``usage-monitor`` entries describe a member of the Usage Monitor family: an
executable plus a config directory, an environment variable, an instance
suffix and a settings file. Several instances share one executable.

``github-exe`` entries describe the general case the registry was generalized
to: a portable executable kept up to date from GitHub Releases, with no config
directory, no instance suffix and no settings file. One instance per executable
path, and its environment is never read. ``AgentMonitorForClaude.exe`` (same
author, same folder, same release model as the monitors) is the first one.

Facts for the family come from PLAN.md section 1.3. Note that the Claude
releases are pulled from the upstream repo
``jens-duttke/usage-monitor-for-claude``, because the ``hybrid2102`` fork
publishes none.

The registry is *live*: :data:`KINDS` starts as the built-ins and
:func:`reload` merges the user-defined entries of
``manager_config.custom_kinds`` on top. Everything else in the manager reads
:data:`KINDS` through the functions below, so a custom kind is a first-class
citizen of discovery, the update engine, the watchdog and the UI.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from . import manager_config
from .model import MonitorKind

__all__ = [
    "BUILTIN_KINDS",
    "KINDS",
    "add_custom_kind",
    "all_kinds",
    "build_registry",
    "exe_names",
    "get_kind",
    "github_exe_kind",
    "github_exe_kinds",
    "kind_for_exe_name",
    "kind_for_product_name",
    "kind_keys",
    "reload",
    "remove_custom_kind",
    "set_custom_kinds",
    "sorted_kinds",
    "usage_monitor_kinds",
    "validate_custom_kind",
]

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _home_dir(name: str) -> Path:
    return Path.home() / name


def _monitor_kind(
    *,
    key: str,
    display_name: str,
    exe_name: str,
    pe_product_name: str,
    env_var: str,
    default_dir_name: str,
    repo: str,
    sha_sidecar: bool,
) -> MonitorKind:
    stem = Path(exe_name).stem
    return MonitorKind(
        key=key,
        display_name=display_name,
        exe_name=exe_name,
        pe_product_name=pe_product_name,
        repo=repo,
        asset_pattern=exe_name,
        sha_sidecar=sha_sidecar,
        settings_schema_id=key,
        mutex_base=f"{stem}_SingleInstance",
        run_key_base=stem,
        env_var=env_var,
        default_config_dir_factory=lambda: _home_dir(default_dir_name),
        family="usage-monitor",
    )


def github_exe_kind(
    *,
    key: str,
    display_name: str,
    exe_name: str,
    repo: str,
    asset_pattern: str | None = None,
    sha_sidecar: bool = False,
    pe_product_name: str | None = None,
    disabled_reason: str | None = None,
    source: str = "builtin",
) -> MonitorKind:
    """Build a ``github-exe`` entry: a portable exe updated from Releases.

    ``pe_product_name`` is optional and only used as a second identity check
    when the file carries a version resource; an empty one means "match on the
    file name alone".
    """
    stem = Path(exe_name).stem
    return MonitorKind(
        key=key,
        display_name=display_name,
        exe_name=exe_name,
        pe_product_name=pe_product_name or "",
        repo=repo,
        asset_pattern=asset_pattern or exe_name,
        sha_sidecar=sha_sidecar,
        settings_schema_id="",
        mutex_base=f"{stem}_SingleInstance",
        run_key_base=stem,
        env_var=None,
        family="github-exe",
        disabled_reason=disabled_reason,
        source=source,
    )


BUILTIN_KINDS: dict[str, MonitorKind] = {
    "claude": _monitor_kind(
        key="claude",
        display_name="Claude",
        exe_name="UsageMonitorForClaude.exe",
        pe_product_name="Usage Monitor for Claude",
        env_var="CLAUDE_CONFIG_DIR",
        default_dir_name=".claude",
        repo="jens-duttke/usage-monitor-for-claude",
        sha_sidecar=False,
    ),
    "copilot": _monitor_kind(
        key="copilot",
        display_name="Copilot",
        exe_name="UsageMonitorForCopilot.exe",
        pe_product_name="Usage Monitor for Copilot",
        env_var="COPILOT_HOME",
        default_dir_name=".copilot",
        repo="hybrid2102/usage-monitor-for-copilot",
        sha_sidecar=True,
    ),
    "codex": _monitor_kind(
        key="codex",
        display_name="Codex",
        exe_name="UsageMonitorForCodex.exe",
        pe_product_name="Usage Monitor for Codex",
        env_var="CODEX_HOME",
        default_dir_name=".codex",
        repo="hybrid2102/usage-monitor-for-codex",
        sha_sidecar=True,
    ),
    "agent-monitor-claude": github_exe_kind(
        key="agent-monitor-claude",
        display_name="Agent Monitor for Claude",
        exe_name="AgentMonitorForClaude.exe",
        repo="jens-duttke/agent-monitor-for-claude",
        pe_product_name="Agent Monitor for Claude",
        sha_sidecar=False,
    ),
}
"""The entries shipped with the manager.

``agent-monitor-claude`` was verified against the live release feed on
2026-09-18 (``v0.8.0``, asset ``AgentMonitorForClaude.exe`` carrying a
``sha256:`` digest and no ``.sha256`` sidecar); the recorded answer is
``tests/fixtures/releases/jens-duttke_agent-monitor-for-claude.json``.
"""

KINDS: dict[str, MonitorKind] = dict(BUILTIN_KINDS)
"""The live registry: built-ins plus whatever :func:`reload` merged in."""


# --------------------------------------------------------------------------- #
# Custom kinds
# --------------------------------------------------------------------------- #


def validate_custom_kind(entry: manager_config.CustomKind) -> list[str]:
    """Everything wrong with one user-defined entry, as human-readable lines.

    An empty list means the entry is usable. Validation is deliberately strict
    about the key (it becomes an instance id and a CLI argument) and about the
    executable name (it is matched against process names, never a path).
    """
    problems: list[str] = []
    if not _KEY_RE.match(entry.key):
        problems.append(f"key {entry.key!r}: use lowercase letters, digits, '.', '-' or '_', starting alphanumeric")
    if entry.key in BUILTIN_KINDS:
        problems.append(f"key {entry.key!r} is a built-in kind and cannot be redefined")
    if not entry.display_name:
        problems.append("display_name is required")
    name = entry.exe_name
    if not name or Path(name).name != name:
        problems.append(f"exe_name {name!r} must be a bare file name, not a path")
    elif not name.casefold().endswith(".exe"):
        problems.append(f"exe_name {name!r} must end with .exe")
    if not _REPO_RE.match(entry.repo):
        problems.append(f"repo {entry.repo!r} must look like 'owner/name'")
    if not entry.asset_pattern:
        problems.append("asset_pattern must not be empty")
    return problems


def _to_kind(entry: manager_config.CustomKind) -> MonitorKind:
    return github_exe_kind(
        key=entry.key,
        display_name=entry.display_name,
        exe_name=entry.exe_name,
        repo=entry.repo,
        asset_pattern=entry.asset_pattern,
        sha_sidecar=entry.sha_sidecar,
        pe_product_name=entry.pe_product_name,
        source="custom",
    )


def build_registry(
    custom: Iterable[manager_config.CustomKind],
    *,
    builtins: dict[str, MonitorKind] | None = None,
) -> tuple[dict[str, MonitorKind], list[str]]:
    """Merge user entries onto the built-ins; returns ``(registry, problems)``.

    Pure: nothing is read from disk and :data:`KINDS` is left alone, which is
    what makes the merge rules testable. An invalid, duplicate or clashing
    entry is skipped and explained instead of taking the whole registry down -
    the file is hand-editable and one typo must not stop the manager.
    """
    registry = dict(BUILTIN_KINDS if builtins is None else builtins)
    problems: list[str] = []
    seen: set[str] = set()
    for entry in custom:
        errors = validate_custom_kind(entry)
        if entry.key in seen:
            errors.append(f"key {entry.key!r} is declared twice")
        if errors:
            problems.extend(errors)
            continue
        clash = next(
            (other.key for other in registry.values() if other.exe_name.casefold() == entry.exe_name.casefold()),
            None,
        )
        if clash is not None:
            problems.append(f"key {entry.key!r}: {entry.exe_name} is already claimed by kind {clash!r}")
            continue
        seen.add(entry.key)
        registry[entry.key] = _to_kind(entry)
    return registry, problems


def set_custom_kinds(custom: Iterable[manager_config.CustomKind]) -> list[str]:
    """Rebuild :data:`KINDS` from the built-ins plus ``custom`` (in place)."""
    registry, problems = build_registry(custom)
    KINDS.clear()
    KINDS.update(registry)
    return problems


def reload(config: manager_config.ManagerConfig | None = None, path: Path | None = None) -> list[str]:
    """Re-read the custom kinds from the manager config into :data:`KINDS`.

    Returns the validation problems (empty when everything merged). The CLI,
    the tray and the window call it once before anything is discovered.
    """
    resolved = config if config is not None else manager_config.load(path)
    return set_custom_kinds(resolved.custom_kinds)


def add_custom_kind(entry: manager_config.CustomKind, path: Path | None = None) -> list[str]:
    """Validate, persist and activate one user-defined kind.

    Returns the problems; when the list is empty the entry has been written to
    ``manager-config.json`` and :data:`KINDS` already contains it.
    """
    config = manager_config.load(path)
    others = [item for item in config.custom_kinds if item.key != entry.key]
    _, problems = build_registry([*others, entry])
    if problems:
        return problems
    manager_config.upsert_custom_kind(entry, path)
    reload(path=path)
    return []


def remove_custom_kind(key: str, path: Path | None = None) -> bool:
    """Drop one user-defined kind; ``False`` when there was none to drop."""
    removed = manager_config.remove_custom_kind(key, path)
    reload(path=path)
    return removed


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #


def get_kind(key: str) -> MonitorKind:
    """Return the kind registered under ``key``.

    Raises:
        KeyError: if no kind matches (message lists the known keys).
    """
    try:
        return KINDS[key]
    except KeyError:
        known = ", ".join(sorted(KINDS))
        raise KeyError(f"unknown monitor kind {key!r} (known: {known})") from None


def all_kinds() -> tuple[MonitorKind, ...]:
    """All registered kinds, in registration order."""
    return tuple(KINDS.values())


def usage_monitor_kinds() -> tuple[MonitorKind, ...]:
    """Only the Usage Monitor family (the kinds with a config dir)."""
    return tuple(kind for kind in KINDS.values() if kind.family == "usage-monitor")


def github_exe_kinds() -> tuple[MonitorKind, ...]:
    """Only the generic "portable exe from GitHub Releases" kinds."""
    return tuple(kind for kind in KINDS.values() if kind.is_github_exe)


def kind_keys() -> tuple[str, ...]:
    """Registered keys, in registration order (the CLI's ``--kind`` choices)."""
    return tuple(KINDS)


def kind_for_exe_name(name: str) -> MonitorKind | None:
    """Match an executable name (case-insensitive, path components ignored)."""
    base = Path(name).name.casefold()
    for kind in KINDS.values():
        if kind.exe_name.casefold() == base:
            return kind
    return None


def kind_for_product_name(name: str) -> MonitorKind | None:
    """Match a PE ``ProductName`` string (case-insensitive, whitespace trimmed)."""
    needle = name.strip().casefold()
    if not needle:
        return None
    for kind in KINDS.values():
        if kind.pe_product_name and kind.pe_product_name.casefold() == needle:
            return kind
    return None


def exe_names() -> tuple[str, ...]:
    """Every executable name the registry knows, in registration order."""
    return tuple(kind.exe_name for kind in KINDS.values())


def sorted_kinds(keys: Sequence[str]) -> list[MonitorKind]:
    """The kinds for ``keys``, unknown ones skipped, registration order kept."""
    wanted = {key.casefold() for key in keys}
    return [kind for kind in KINDS.values() if kind.key.casefold() in wanted]
