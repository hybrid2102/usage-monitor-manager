"""Tests for the declarative kind registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from usage_monitor_manager import kinds


def test_builtin_kinds_registered() -> None:
    assert [k.key for k in kinds.all_kinds()] == [
        "claude",
        "copilot",
        "codex",
        "agent-monitor-claude",
    ]
    assert [k.key for k in kinds.usage_monitor_kinds()] == ["claude", "copilot", "codex"]
    assert [k.key for k in kinds.github_exe_kinds()] == ["agent-monitor-claude"]


@pytest.mark.parametrize(
    ("key", "env_var", "dir_name", "repo", "exe", "sidecar"),
    [
        (
            "claude",
            "CLAUDE_CONFIG_DIR",
            ".claude",
            "jens-duttke/usage-monitor-for-claude",
            "UsageMonitorForClaude.exe",
            False,
        ),
        (
            "copilot",
            "COPILOT_HOME",
            ".copilot",
            "hybrid2102/usage-monitor-for-copilot",
            "UsageMonitorForCopilot.exe",
            True,
        ),
        (
            "codex",
            "CODEX_HOME",
            ".codex",
            "hybrid2102/usage-monitor-for-codex",
            "UsageMonitorForCodex.exe",
            True,
        ),
    ],
)
def test_kind_fields(key: str, env_var: str, dir_name: str, repo: str, exe: str, sidecar: bool) -> None:
    kind = kinds.get_kind(key)
    assert kind.env_var == env_var
    assert kind.default_config_dir == Path.home() / dir_name
    assert kind.repo == repo
    assert kind.exe_name == exe
    assert kind.sha_sidecar is sidecar
    assert kind.asset_pattern == exe
    assert kind.settings_schema_id == key


def test_product_names_and_bases() -> None:
    for kind in kinds.usage_monitor_kinds():
        assert kind.pe_product_name == f"Usage Monitor for {kind.display_name}"
        assert kind.mutex_base == f"{kind.exe_stem}_SingleInstance"
        assert kind.run_key_base == kind.exe_stem


def test_default_config_dir_follows_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert kinds.get_kind("codex").default_config_dir == tmp_path / ".codex"


def test_lookup_by_exe_name_is_case_insensitive() -> None:
    assert kinds.kind_for_exe_name("usagemonitorforcopilot.exe") is kinds.get_kind("copilot")
    assert kinds.kind_for_exe_name(r"C:\Tools\UsageMonitorForCodex.EXE") is kinds.get_kind("codex")
    assert kinds.kind_for_exe_name("notepad.exe") is None


def test_lookup_by_product_name() -> None:
    assert kinds.kind_for_product_name("  usage monitor for claude ") is kinds.get_kind("claude")
    assert kinds.kind_for_product_name("Usage Monitor for Gemini") is None


def test_unknown_kind_raises() -> None:
    with pytest.raises(KeyError, match="unknown monitor kind"):
        kinds.get_kind("gemini")
