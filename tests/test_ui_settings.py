"""The settings editor half of the JavaScript bridge.

Same arrangement as ``test_ui_window``: a faked ``discovery.discover``, a
``tmp_path`` home *and* a ``tmp_path`` ``%LOCALAPPDATA%`` so the history goes
nowhere near the real one. ``process_control`` is faked as well - "Salva e
riavvia" must never touch a real monitor - and the quick action is a
``subprocess.run`` stub.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from usage_monitor_manager import discovery, history, process_control
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, JsonDict, ProcessPair
from usage_monitor_manager.settings import store
from usage_monitor_manager.ui.window import JsApi


def make_instance(kind_key: str, config_dir: Path, *, exe: Path, settings: Path | None) -> Instance:
    kind = get_kind(kind_key)
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=config_dir_suffix(config_dir, kind.default_config_dir),
        exe_path=exe,
        settings_path=settings,
        settings_exists=settings is not None,
        processes=ProcessPair(bootloader_pid=10, app_pid=11),
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = tmp_path / "home"
    tools = tmp_path / "Tools"
    for directory in (home, tools):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    exe = tools / "UsageMonitorForClaude.exe"
    exe.write_bytes(b"MZ")
    default_dir = home / ".claude"
    other_dir = home / ".claude-valeria"
    for directory in (default_dir, other_dir):
        directory.mkdir()
    settings = default_dir / "usage-monitor-settings.json"
    settings.write_text('{"bar_fg": "#4a9eff", "mia_chiave": 7}', encoding="utf-8")
    other_settings = other_dir / "usage-monitor-settings.json"
    other_settings.write_text('{"bar_fg": "#00cc66", "poll_interval": 60}', encoding="utf-8")

    main = make_instance("claude", default_dir, exe=exe, settings=settings)
    other = make_instance("claude", other_dir, exe=exe, settings=other_settings)
    # A third instance with no settings file at all.
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    codex = make_instance("codex", codex_dir, exe=tools / "UsageMonitorForCodex.exe", settings=None)
    instances = [main, other, codex]
    monkeypatch.setattr(discovery, "discover", lambda *a, **k: list(instances))

    api = JsApi(config_path=tmp_path / "manager-config.json")
    api.list_instances()
    return {"api": api, "main": main, "other": other, "codex": codex, "settings": settings}


def test_get_schema_and_validate_draft(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    assert api.get_schema("copilot")["kind"] == "copilot"
    assert api.get_schema("gemini")["ok"] is False
    report = api.validate_draft("claude", {"poll_interval": "x"})
    assert report["ok"] is False
    assert report["errors"][0]["key"] == "poll_interval"


def test_save_settings_writes_normalizes_and_archives(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    result = api.save_settings(
        world["main"].instance_id,
        {"alert_thresholds_five_hour": [95, 50], "quick_action_command": "notepad", "mia_chiave": 7},
    )
    assert result["ok"] is True
    on_disk = store.load(world["settings"]).data
    assert on_disk["alert_thresholds_five_hour"] == [50, 95]
    assert on_disk["quick_action_command"] == ["notepad"]
    assert on_disk["mia_chiave"] == 7  # unknown key preserved
    assert len(history.list_versions(world["main"])) == 1
    assert result["history"]


def test_save_settings_refuses_invalid_values(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    before = world["settings"].read_text(encoding="utf-8")
    result = api.save_settings(world["main"].instance_id, {"poll_interval": 0})
    assert result["ok"] is False
    assert result["validation"]["errors"][0]["key"] == "poll_interval"
    assert world["settings"].read_text(encoding="utf-8") == before


def test_save_and_restart_goes_through_process_control(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class Result:
        def to_dict(self) -> JsonDict:
            return {"ok": True, "detail": "riavviata"}

    def fake_restart(instances: Any, **kwargs: Any) -> Result:
        calls.append(len(list(instances)))
        return Result()

    monkeypatch.setattr(process_control, "restart", fake_restart)
    result = world["api"].save_settings(world["main"].instance_id, {"poll_interval": 200}, True)
    assert result["ok"] is True
    assert calls == [1]
    assert result["restart"][0]["ok"] is True


def test_create_settings_uses_the_suggested_path(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    result = api.create_settings(world["codex"].instance_id)
    assert result["ok"] is True
    assert Path(result["path"]).read_text(encoding="utf-8") == "{}\n"
    assert api.create_settings(world["codex"].instance_id)["ok"] is False


def test_history_list_version_and_restore(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    api.save_settings(world["main"].instance_id, {"poll_interval": 100})
    api.save_settings(world["main"].instance_id, {"poll_interval": 200})
    versions = api.list_history(world["main"].instance_id)["versions"]
    assert len(versions) == 2

    oldest = versions[-1]["stamp"]
    detail = api.get_history_version(world["main"].instance_id, oldest)
    assert detail["data"] == {"poll_interval": 100}
    assert detail["diff"] == [{"key": "poll_interval", "change": "changed", "before": 100, "after": 200}]

    restored = api.restore_history(world["main"].instance_id, oldest)
    assert restored["ok"] is True
    assert store.load(world["settings"]).data == {"poll_interval": 100}
    assert api.get_history_version(world["main"].instance_id, "nope")["ok"] is False


def test_export_and_import_settings(world: dict[str, Any], tmp_path: Path) -> None:
    api: JsApi = world["api"]
    profile = tmp_path / "profile.json"
    assert api.export_settings(world["main"].instance_id, str(profile))["ok"] is True
    api.save_settings(world["main"].instance_id, {})
    result = api.import_settings(world["main"].instance_id, str(profile))
    assert result["ok"] is True
    assert store.load(world["settings"]).data["bar_fg"] == "#4a9eff"


def test_copy_settings_from_another_instance_does_not_write(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    before = world["settings"].read_text(encoding="utf-8")
    result = api.copy_settings_from(world["main"].instance_id, world["other"].instance_id, ["popup_colors"])
    assert result["ok"] is True
    assert result["data"]["bar_fg"] == "#00cc66"
    assert result["data"]["mia_chiave"] == 7
    assert world["settings"].read_text(encoding="utf-8") == before


def test_copy_settings_refuses_a_different_kind(world: dict[str, Any]) -> None:
    result = world["api"].copy_settings_from(world["main"].instance_id, world["codex"].instance_id, ["popup_colors"])
    assert result["ok"] is False


def test_apply_preset_derives_bar_and_icon_colours(world: dict[str, Any]) -> None:
    result = world["api"].apply_preset(world["main"].instance_id, "#D97757", {})
    assert result["ok"] is True
    assert result["data"]["bar_fg"] == "#D97757"
    assert result["data"]["icon_light"]["fg"] == [242, 207, 196, 255]
    assert result["palette"]["dark"] == "#4C2A1E"
    assert world["api"].apply_preset(world["main"].instance_id, "rosso", {})["ok"] is False


def test_get_presets_lists_the_brands(world: dict[str, Any]) -> None:
    names = [entry["name"] for entry in world["api"].get_presets()]
    assert names[:3] == ["Claude", "ChatGPT", "Codex"]


def test_test_quick_action_runs_the_configured_command(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_run(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((command, kwargs.get("shell")))
        return subprocess.CompletedProcess(command, 0, "fatto\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = world["api"].test_quick_action(world["main"].instance_id, ["cmd", "/c", "echo"])
    assert result["ok"] is True
    assert result["stdout"] == "fatto\n"
    assert seen == [(["cmd", "/c", "echo"], False)]


def test_test_quick_action_without_a_command(world: dict[str, Any]) -> None:
    assert world["api"].test_quick_action(world["main"].instance_id)["ok"] is False
