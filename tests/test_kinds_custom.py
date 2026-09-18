"""Tests for the ``github-exe`` family of the kind registry (PLAN.md 9.9).

Three things are checked here and nowhere else:

* the merge rules of :func:`kinds.build_registry` - which is pure, so the
  whole validation matrix is a parametrized table;
* what a ``github-exe`` kind looks like once merged (no config dir, no
  environment variable, no settings schema);
* that adding and removing a custom kind round-trips through
  ``manager-config.json``, which the autouse ``local_appdata`` fixture keeps
  inside ``tmp_path``.

Nothing here reaches the network, the registry or a real config file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usage_monitor_manager import kinds, manager_config
from usage_monitor_manager.manager_config import CustomKind


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


def _entry(**overrides: object) -> CustomKind:
    data: dict[str, object] = {
        "key": "statusline",
        "display_name": "Statusline Helper",
        "exe_name": "StatuslineHelper.exe",
        "repo": "hybrid2102/statusline-helper",
    }
    data.update(overrides)
    return CustomKind(**data)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_valid_entry_has_no_problems() -> None:
    assert kinds.validate_custom_kind(_entry()) == []


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"key": "Statusline"}, "lowercase letters"),
        ({"key": "-nope"}, "lowercase letters"),
        ({"key": "with space"}, "lowercase letters"),
        ({"key": "claude"}, "built-in kind"),
        ({"key": "agent-monitor-claude"}, "built-in kind"),
        ({"display_name": ""}, "display_name is required"),
        ({"exe_name": r"C:\Tools\Thing.exe"}, "bare file name"),
        ({"exe_name": ""}, "bare file name"),
        ({"exe_name": "thing.bat"}, "must end with .exe"),
        ({"repo": "no-slash"}, "owner/name"),
        ({"repo": "a/b/c"}, "owner/name"),
    ],
)
def test_invalid_entries_are_explained(overrides: dict[str, object], needle: str) -> None:
    problems = kinds.validate_custom_kind(_entry(**overrides))
    assert problems, f"{overrides} should have been rejected"
    assert any(needle in problem for problem in problems), problems


def test_asset_pattern_defaults_to_the_exe_name() -> None:
    assert _entry().asset_pattern == "StatuslineHelper.exe"
    assert kinds.validate_custom_kind(_entry(asset_pattern="Statusline-win.exe")) == []


# --------------------------------------------------------------------------- #
# Merging
# --------------------------------------------------------------------------- #


def test_build_registry_merges_on_top_of_the_builtins() -> None:
    registry, problems = kinds.build_registry([_entry()])
    assert problems == []
    assert set(kinds.BUILTIN_KINDS) < set(registry)
    merged = registry["statusline"]
    assert merged.family == "github-exe"
    assert merged.is_github_exe is True
    assert merged.has_config_dir is False
    assert merged.env_var is None
    assert merged.settings_schema_id == ""
    assert merged.source == "custom"
    assert merged.repo == "hybrid2102/statusline-helper"
    assert merged.asset_pattern == "StatuslineHelper.exe"


def test_build_registry_is_pure() -> None:
    before = dict(kinds.KINDS)
    kinds.build_registry([_entry()])
    assert kinds.KINDS == before


def test_a_bad_entry_is_skipped_and_the_rest_still_merge() -> None:
    registry, problems = kinds.build_registry([_entry(repo="nope"), _entry(key="other", exe_name="Other.exe")])
    assert "statusline" not in registry
    assert "other" in registry
    assert any("owner/name" in problem for problem in problems)


def test_the_same_key_twice_keeps_the_first_and_reports_the_second() -> None:
    registry, problems = kinds.build_registry([_entry(), _entry(exe_name="Another.exe")])
    assert registry["statusline"].exe_name == "StatuslineHelper.exe"
    assert any("declared twice" in problem for problem in problems)


def test_an_exe_name_already_claimed_is_refused() -> None:
    registry, problems = kinds.build_registry([_entry(key="clone", exe_name="AgentMonitorForClaude.exe")])
    assert "clone" not in registry
    assert any("already claimed by kind 'agent-monitor-claude'" in problem for problem in problems)


def test_the_clash_check_ignores_case() -> None:
    _, problems = kinds.build_registry([_entry(key="clone", exe_name="usagemonitorforclaude.EXE")])
    assert any("already claimed by kind 'claude'" in problem for problem in problems)


def test_set_custom_kinds_rewrites_the_live_registry() -> None:
    assert kinds.set_custom_kinds([_entry()]) == []
    assert kinds.get_kind("statusline").display_name == "Statusline Helper"
    assert [kind.key for kind in kinds.github_exe_kinds()] == ["agent-monitor-claude", "statusline"]
    assert "statusline" in kinds.kind_keys()
    assert "StatuslineHelper.exe" in kinds.exe_names()
    # and a second call with nothing merges back down to the built-ins
    assert kinds.set_custom_kinds([]) == []
    assert set(kinds.KINDS) == set(kinds.BUILTIN_KINDS)


# --------------------------------------------------------------------------- #
# Lookups for a github-exe kind
# --------------------------------------------------------------------------- #


def test_kind_for_exe_name_finds_the_builtin_github_exe() -> None:
    kind = kinds.kind_for_exe_name("AgentMonitorForClaude.exe")
    assert kind is not None
    assert kind.key == "agent-monitor-claude"
    assert kind.family == "github-exe"
    assert kinds.kind_for_exe_name(r"D:\Tools\AI\agentmonitorforclaude.EXE") is kind
    assert kinds.kind_for_product_name("Agent Monitor for Claude") is kind


def test_kind_for_exe_name_finds_a_custom_kind_once_merged() -> None:
    assert kinds.kind_for_exe_name("StatuslineHelper.exe") is None
    kinds.set_custom_kinds([_entry()])
    found = kinds.kind_for_exe_name("statuslinehelper.exe")
    assert found is not None
    assert found.key == "statusline"
    # a custom entry without a PE ProductName matches on the file name alone
    assert found.pe_product_name == ""
    assert kinds.kind_for_product_name("Statusline Helper") is None


def test_a_custom_kind_can_declare_a_product_name() -> None:
    kinds.set_custom_kinds([_entry(pe_product_name="Statusline Helper")])
    assert kinds.kind_for_product_name(" statusline helper ") is kinds.get_kind("statusline")


def test_github_exe_kind_naming_matches_the_family() -> None:
    kind = kinds.get_kind("agent-monitor-claude")
    assert kind.mutex_base == "AgentMonitorForClaude_SingleInstance"
    assert kind.run_key_base == "AgentMonitorForClaude"
    assert kind.repo == "jens-duttke/agent-monitor-for-claude"
    assert kind.sha_sidecar is False


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_add_and_remove_round_trip_through_the_config_file() -> None:
    assert kinds.add_custom_kind(_entry()) == []
    assert [entry.key for entry in manager_config.load().custom_kinds] == ["statusline"]
    assert "statusline" in kinds.KINDS

    assert kinds.remove_custom_kind("statusline") is True
    assert manager_config.load().custom_kinds == ()
    assert "statusline" not in kinds.KINDS


def test_add_refuses_an_invalid_entry_without_writing_anything() -> None:
    problems = kinds.add_custom_kind(_entry(repo="nope"))
    assert problems
    assert manager_config.load().custom_kinds == ()
    assert "statusline" not in kinds.KINDS


def test_add_can_replace_an_entry_with_the_same_key() -> None:
    assert kinds.add_custom_kind(_entry()) == []
    assert kinds.add_custom_kind(_entry(display_name="Renamed")) == []
    stored = manager_config.load().custom_kinds
    assert [entry.display_name for entry in stored] == ["Renamed"]


def test_remove_reports_that_a_builtin_was_not_removed() -> None:
    assert kinds.remove_custom_kind("claude") is False
    assert "claude" in kinds.KINDS


def test_reload_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    manager_config.save(manager_config.ManagerConfig(custom_kinds=(_entry(),)), path)
    assert kinds.reload(path=path) == []
    assert "statusline" in kinds.KINDS


def test_reload_takes_a_config_object_without_touching_the_disk() -> None:
    config = manager_config.ManagerConfig(custom_kinds=(_entry(), _entry(key="BAD")))
    problems = kinds.reload(config)
    assert "statusline" in kinds.KINDS
    assert any("lowercase letters" in problem for problem in problems)
