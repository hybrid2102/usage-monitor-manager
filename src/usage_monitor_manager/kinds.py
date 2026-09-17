"""Kind registry: data, not logic.

One entry per member of the "Usage Monitor" family. Adding a future monitor
(e.g. ``usage-monitor-for-gemini``) means adding an entry here and a settings
schema; no other module needs to change.

Facts come from PLAN.md section 1.3. Note that the Claude releases are pulled
from the upstream repo ``jens-duttke/usage-monitor-for-claude``, because the
``hybrid2102`` fork publishes none.
"""

from __future__ import annotations

from pathlib import Path

from .model import MonitorKind

__all__ = ["KINDS", "all_kinds", "get_kind", "kind_for_exe_name", "kind_for_product_name"]


def _home_dir(name: str) -> Path:
    return Path.home() / name


def _kind(
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
        env_var=env_var,
        default_config_dir_factory=lambda: _home_dir(default_dir_name),
        repo=repo,
        asset_pattern=exe_name,
        sha_sidecar=sha_sidecar,
        settings_schema_id=key,
        mutex_base=f"{stem}_SingleInstance",
        run_key_base=stem,
    )


KINDS: dict[str, MonitorKind] = {
    "claude": _kind(
        key="claude",
        display_name="Claude",
        exe_name="UsageMonitorForClaude.exe",
        pe_product_name="Usage Monitor for Claude",
        env_var="CLAUDE_CONFIG_DIR",
        default_dir_name=".claude",
        repo="jens-duttke/usage-monitor-for-claude",
        sha_sidecar=False,
    ),
    "copilot": _kind(
        key="copilot",
        display_name="Copilot",
        exe_name="UsageMonitorForCopilot.exe",
        pe_product_name="Usage Monitor for Copilot",
        env_var="COPILOT_HOME",
        default_dir_name=".copilot",
        repo="hybrid2102/usage-monitor-for-copilot",
        sha_sidecar=True,
    ),
    "codex": _kind(
        key="codex",
        display_name="Codex",
        exe_name="UsageMonitorForCodex.exe",
        pe_product_name="Usage Monitor for Codex",
        env_var="CODEX_HOME",
        default_dir_name=".codex",
        repo="hybrid2102/usage-monitor-for-codex",
        sha_sidecar=True,
    ),
}


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
    for kind in KINDS.values():
        if kind.pe_product_name.casefold() == needle:
            return kind
    return None
