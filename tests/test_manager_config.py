"""Tests for the manager's own persistent state.

Every test passes an explicit ``path`` under ``tmp_path``: the real
``%LOCALAPPDATA%\\UsageMonitorManager\\manager-config.json`` is never read and
never written by the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from usage_monitor_manager import manager_config
from usage_monitor_manager.manager_config import DeclaredInstance, ManagerConfig, Preferences


def test_defaults_match_the_plan() -> None:
    preferences = Preferences()
    assert preferences.check_interval_hours == 6
    assert preferences.notify_updates is True
    assert preferences.autostart_manager is False
    assert preferences.include_prerelease is False
    assert preferences.language == "it"


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    config = manager_config.load(tmp_path / "nope.json")
    assert config == ManagerConfig()
    assert config.instances == ()
    assert config.legacy_import_done is False


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    original = ManagerConfig(
        instances=(
            DeclaredInstance(kind="claude", config_dir=Path(r"C:\Users\Marco\.claude-valeria"), label="Claude Valeria"),
            DeclaredInstance(kind="codex", config_dir=Path(r"C:\Users\Marco\.codex"), enabled=False),
        ),
        preferences=Preferences(check_interval_hours=12, notify_updates=False, language="en"),
        last_check={"hybrid2102/usage-monitor-for-copilot": "2026-09-18T10:00:00+00:00"},
        skipped_versions={"hybrid2102/usage-monitor-for-copilot": "v0.3.2"},
        legacy_import_done=True,
    )
    manager_config.save(original, path)
    assert manager_config.load(path) == original


def test_save_is_atomic_and_keeps_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    manager_config.save(ManagerConfig(legacy_import_done=False), path)
    first = path.read_text(encoding="utf-8")

    manager_config.save(ManagerConfig(legacy_import_done=True), path)
    backup = path.with_name(path.name + ".bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == first
    assert manager_config.load(path).legacy_import_done is True
    # no temporary file left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manager-config.json", "manager-config.json.bak"]


def test_broken_json_does_not_stop_the_manager(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    path.write_text("{ not json at all", encoding="utf-8")
    assert manager_config.load(path) == ManagerConfig()


def test_migration_from_a_bare_document(tmp_path: Path) -> None:
    """A file written by an older (or hand-edited) manager is filled in."""
    path = tmp_path / "manager-config.json"
    path.write_text(json.dumps({"instances": [{"kind": "claude", "config_dir": "C:/x/.claude"}]}), encoding="utf-8")
    config = manager_config.load(path)
    assert config.version == manager_config.CONFIG_VERSION
    assert config.preferences == Preferences()
    assert config.instances == (DeclaredInstance(kind="claude", config_dir=Path("C:/x/.claude")),)
    assert config.last_check == {}
    assert config.skipped_versions == {}


def test_unknown_top_level_keys_survive_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    path.write_text(json.dumps({"version": 99, "future_feature": {"a": 1}}), encoding="utf-8")
    config = manager_config.load(path)
    assert config.extra == {"future_feature": {"a": 1}}
    manager_config.save(config, path)
    assert json.loads(path.read_text(encoding="utf-8"))["future_feature"] == {"a": 1}


def test_invalid_values_are_coerced(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    path.write_text(
        json.dumps(
            {
                "preferences": {
                    "check_interval_hours": 0,
                    "notify_updates": "yes",
                    "language": "klingon",
                },
                "instances": [
                    {"kind": "claude"},  # no config_dir: dropped
                    "nonsense",
                    {"kind": "claude", "config_dir": "C:/x/.claude"},
                    {"kind": "CLAUDE", "config_dir": "C:/X/.CLAUDE"},  # duplicate merge key
                ],
                "skipped_versions": {"repo": 5},
            }
        ),
        encoding="utf-8",
    )
    config = manager_config.load(path)
    assert config.preferences.check_interval_hours == 1  # clamped to the minimum
    assert config.preferences.notify_updates is True  # non-bool ignored
    assert config.preferences.language == "it"
    assert len(config.instances) == 1
    assert config.skipped_versions == {}


def test_preference_with_value_validates() -> None:
    preferences = Preferences()
    assert preferences.with_value("check_interval_hours", 999).check_interval_hours == 24 * 7
    assert preferences.with_value("autostart_manager", True).autostart_manager is True
    with pytest.raises(KeyError):
        preferences.with_value("nope", 1)


def test_set_preference_persists(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    updated = manager_config.set_preference("notify_updates", False, path)
    assert updated.preferences.notify_updates is False
    assert manager_config.load(path).preferences.notify_updates is False


def test_upsert_instance_replaces_on_the_merge_key(tmp_path: Path) -> None:
    path = tmp_path / "manager-config.json"
    manager_config.upsert_instance(DeclaredInstance(kind="claude", config_dir=Path("C:/x/.claude"), label="A"), path)
    config = manager_config.upsert_instance(
        DeclaredInstance(kind="claude", config_dir=Path("C:/X/.CLAUDE"), label="B"),
        path,
    )
    assert len(config.instances) == 1
    assert config.instances[0].label == "B"


def test_label_for_is_case_insensitive_on_the_path() -> None:
    config = ManagerConfig(
        instances=(DeclaredInstance(kind="claude", config_dir=Path(r"C:\Users\M\.claude-valeria"), label="Valeria"),)
    )
    assert config.label_for("claude", r"c:\users\m\.claude-valeria") == "Valeria"
    assert config.label_for("codex", r"C:\Users\M\.claude-valeria") is None


def test_app_dir_follows_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert manager_config.app_dir() == tmp_path / "UsageMonitorManager"
    assert manager_config.config_path().name == "manager-config.json"
