"""Tests for the window's JavaScript bridge, with a completely faked core.

No webview is imported, no process is touched, no registry key is written and
no HTTP request is made: ``discovery.discover``, ``process_control``,
``updater`` and ``github`` are all monkeypatched, and the scheduler is a stub
holding a hand-built :class:`UpdateState`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from usage_monitor_manager import discovery, github, manager_config, process_control, updater
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, JsonDict, ProcessPair, ReleaseInfo, UpdatePlan
from usage_monitor_manager.scheduler import UpdateState
from usage_monitor_manager.ui.window import JsApi

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def make_instance(
    kind_key: str,
    config_dir: Path,
    *,
    exe: Path,
    running: bool = True,
    version: str = "1.22.0.0",
    settings: Path | None = None,
    autostart: str | None = None,
) -> Instance:
    kind = get_kind(kind_key)
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=config_dir_suffix(config_dir, kind.default_config_dir),
        exe_path=exe,
        exe_version=version,
        exe_mtime=NOW - timedelta(days=3),
        settings_path=settings,
        settings_exists=settings is not None,
        shadow_settings_paths=() if settings is None else (settings.parent / "shadow.json",),
        autostart_entry=autostart is not None,
        autostart_command=autostart,
        processes=ProcessPair(
            bootloader_pid=100,
            app_pid=101,
            started_at=datetime.now(tz=UTC) - timedelta(hours=2),
        )
        if running
        else None,
    )


def make_plan(instance: Instance, *, tag: str, version: str, body: str = "## Note\n\n- una cosa") -> UpdatePlan:
    return UpdatePlan(
        exe_path=instance.exe_path or Path("x"),
        kind=instance.kind,
        current_version="0.3.1",
        target=ReleaseInfo(
            tag=tag,
            version=version,
            published_at=NOW - timedelta(days=1),
            html_url="https://example.invalid/r",
            body_markdown=body,
        ),
        affected_instances=(instance,),
    )


class StubScheduler:
    """Just enough scheduler for the API: a state and a counting check_now."""

    def __init__(self, state: UpdateState | None = None) -> None:
        self.state = state if state is not None else UpdateState()
        self.checks = 0

    def check_now(self) -> UpdateState:
        self.checks += 1
        return self.state


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake machine: two instances, one of them with a pending update."""
    home = tmp_path / "home"
    tools = tmp_path / "Tools"
    for directory in (home, tools):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    claude_exe = tools / "UsageMonitorForClaude.exe"
    copilot_exe = tools / "UsageMonitorForCopilot.exe"
    for exe in (claude_exe, copilot_exe):
        exe.write_bytes(b"MZ")
    claude_dir = home / ".claude"
    copilot_dir = home / ".copilot"
    for directory in (claude_dir, copilot_dir):
        directory.mkdir()
    settings = claude_dir / "usage-monitor-settings.json"
    settings.write_text('{"bar_fg": "#4a9eff"}', encoding="utf-8")

    claude = make_instance("claude", claude_dir, exe=claude_exe, settings=settings, autostart='"x" ')
    copilot = make_instance("copilot", copilot_dir, exe=copilot_exe, running=False, version="0.3.1.0")
    instances = [claude, copilot]
    monkeypatch.setattr(discovery, "discover", lambda *a, **k: list(instances))

    plan = make_plan(copilot, tag="v0.3.2", version="0.3.2")
    scheduler = StubScheduler(UpdateState(last_check=NOW, plans=(plan,), available=(plan,)))

    config_path = tmp_path / "manager-config.json"
    manager_config.save(
        manager_config.ManagerConfig(
            instances=(manager_config.DeclaredInstance(kind="copilot", config_dir=copilot_dir, label="Copilot"),)
        ),
        config_path,
    )

    events: list[JsonDict] = []
    api = JsApi(emit=events.append, scheduler=scheduler, config_path=config_path)
    return {
        "api": api,
        "events": events,
        "scheduler": scheduler,
        "instances": instances,
        "claude": claude,
        "copilot": copilot,
        "plan": plan,
        "config_path": config_path,
        "tools": tools,
    }


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


def test_list_instances_builds_the_dashboard_row(world: dict[str, Any]) -> None:
    rows = world["api"].list_instances()
    assert [row["kind"] for row in rows] == ["claude", "copilot"]

    claude = rows[0]
    assert claude["kind_display"] == "Claude"
    assert claude["local_version"] == "1.22.0"  # PE padding dropped
    assert claude["is_running"] is True
    assert claude["update_available"] is False
    assert claude["remote_version"] is None
    assert claude["uptime_seconds"] is not None and claude["uptime_seconds"] > 0
    assert claude["autostart_entry"] is True
    assert claude["autostart_run_name"] == "UsageMonitorForClaude"
    assert claude["exe_dir"] == str(world["tools"])

    copilot = rows[1]
    assert copilot["label"] == "Copilot"  # from manager-config.json
    assert copilot["update_available"] is True
    assert copilot["remote_version"] == "0.3.2"
    assert copilot["uptime_seconds"] is None
    assert copilot["is_running"] is False


def test_row_label_falls_back_to_nothing_when_undeclared(world: dict[str, Any]) -> None:
    assert world["api"].list_instances()[0]["label"] is None


def test_get_settings_raw_is_read_only(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    api.list_instances()
    payload = api.get_settings_raw("claude")
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["exists"] is True
    assert payload["text"] == '{"bar_fg": "#4a9eff"}'
    assert payload["shadowed"] == [str(Path(payload["path"]).parent / "shadow.json")]


def test_get_settings_raw_for_an_unknown_instance(world: dict[str, Any]) -> None:
    payload = world["api"].get_settings_raw("nope")
    assert payload["ok"] is False


def test_get_settings_raw_without_a_file(world: dict[str, Any]) -> None:
    payload = world["api"].get_settings_raw("copilot")
    assert payload["ok"] is True
    assert payload["path"] is None
    assert payload["text"] is None


# --------------------------------------------------------------------------- #
# Process control
# --------------------------------------------------------------------------- #


def test_restart_targets_only_the_requested_ids(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    class Result:
        def to_dict(self) -> JsonDict:
            return {"ok": True, "detail": "riavviata"}

    def fake_restart(instances: Sequence[Instance], **kwargs: Any) -> Result:
        seen.append([i.instance_id for i in instances])
        callback = kwargs.get("on_event")
        if callback is not None:
            callback(process_control.ProcessEvent("claude", "restart", "start", "via"))
        return Result()

    monkeypatch.setattr(process_control, "restart", fake_restart)
    api: JsApi = world["api"]
    api.list_instances()
    results = api.restart(["claude"])

    assert seen == [["claude"]]
    assert results == [{"ok": True, "detail": "riavviata"}]
    assert world["events"][0]["type"] == "process_event"
    assert world["events"][0]["event"]["message"] == "via"


def test_stop_and_start_are_per_instance(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

        def to_dict(self) -> JsonDict:
            return {"detail": self.name}

    monkeypatch.setattr(process_control, "stop", lambda instance, **k: Result(f"stop {instance.instance_id}"))
    monkeypatch.setattr(process_control, "start", lambda instance, **k: Result(f"start {instance.instance_id}"))
    api: JsApi = world["api"]
    api.list_instances()
    assert api.stop(["claude", "copilot"]) == [{"detail": "stop claude"}, {"detail": "stop copilot"}]
    assert api.start(["copilot"]) == [{"detail": "start copilot"}]


def test_bulk_actions_pick_the_right_instances(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[str] = []
    started: list[str] = []

    class Result:
        def to_dict(self) -> JsonDict:
            return {}

    monkeypatch.setattr(
        process_control,
        "stop",
        lambda instance, **k: (stopped.append(instance.instance_id), Result())[1],
    )
    monkeypatch.setattr(
        process_control,
        "start",
        lambda instance, **k: (started.append(instance.instance_id), Result())[1],
    )
    api: JsApi = world["api"]
    api.stop_all()
    api.start_missing()
    assert stopped == ["claude"]  # only the running one
    assert started == ["copilot"]  # only the stopped one


def test_unknown_ids_do_nothing(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    assert api.restart(["ghost"]) == []
    assert api.stop(["ghost"]) == []


# --------------------------------------------------------------------------- #
# Updates
# --------------------------------------------------------------------------- #


def test_check_updates_delegates_to_the_scheduler(world: dict[str, Any]) -> None:
    payload = world["api"].check_updates()
    assert world["scheduler"].checks == 1
    assert payload["update_count"] == 1
    assert world["events"][-1]["type"] == "update_state"


def test_get_changelog_returns_the_markdown(world: dict[str, Any]) -> None:
    exe = str(world["copilot"].exe_path)
    payload = world["api"].get_changelog(exe)
    assert payload["tag"] == "v0.3.2"
    assert payload["kind_display"] == "Copilot"
    assert payload["body_markdown"].startswith("## Note")
    assert world["api"].get_changelog(r"Z:\nothing.exe") == {}


def test_get_changelog_matches_the_path_case_insensitively(world: dict[str, Any]) -> None:
    exe = str(world["copilot"].exe_path).upper()
    assert world["api"].get_changelog(exe)["tag"] == "v0.3.2"


def test_apply_update_streams_progress_and_refreshes(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        ok = True

        def to_dict(self) -> JsonDict:
            return {"ok": True, "detail": "aggiornato a 0.3.2"}

    def fake_apply(plan: UpdatePlan, **kwargs: Any) -> Result:
        assert plan is world["plan"]
        callback = kwargs.get("on_event")
        if callback is not None:
            callback(updater.UpdateEvent("download", "scarico", 0.5))
            callback(updater.UpdateEvent("done", "fatto", None))
        return Result()

    monkeypatch.setattr(updater, "apply", fake_apply)
    result = world["api"].apply_update(str(world["copilot"].exe_path))

    assert result == {"ok": True, "detail": "aggiornato a 0.3.2"}
    progress = [e for e in world["events"] if e["type"] == "update_event"]
    assert [e["event"]["step"] for e in progress] == ["download", "done"]
    assert progress[0]["event"]["progress"] == 0.5
    assert world["scheduler"].checks == 1  # re-checked after the swap


def test_apply_update_without_a_plan_is_refused(world: dict[str, Any]) -> None:
    result = world["api"].apply_update(r"Z:\unknown.exe")
    assert result["ok"] is False
    assert "nessun aggiornamento" in result["detail"]


def test_rollback_delegates_with_the_known_instances(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Result:
        def to_dict(self) -> JsonDict:
            return {"ok": True, "detail": "ripristinata 0.3.1"}

    def fake_rollback(exe_path: Path, **kwargs: Any) -> Result:
        captured["exe"] = exe_path
        captured["instances"] = list(kwargs.get("instances") or [])
        return Result()

    monkeypatch.setattr(updater, "rollback", fake_rollback)
    result = world["api"].rollback(str(world["copilot"].exe_path))
    assert result["ok"] is True
    assert captured["exe"] == world["copilot"].exe_path
    assert len(captured["instances"]) == 2


# --------------------------------------------------------------------------- #
# Configuration and the Manager page
# --------------------------------------------------------------------------- #


def test_get_config_includes_the_path(world: dict[str, Any]) -> None:
    payload = world["api"].get_config()
    assert payload["config_path"] == str(world["config_path"])
    assert payload["preferences"]["check_interval_hours"] == 6


def test_set_preference_persists_and_validates(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]
    assert api.set_preference("check_interval_hours", 12)["preferences"]["check_interval_hours"] == 12
    assert manager_config.load(world["config_path"]).preferences.check_interval_hours == 12
    assert api.set_preference("nonexistent", 1)["ok"] is False


def test_set_manager_autostart_writes_the_registry_and_the_preference(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from usage_monitor_manager import platform as platform_module

    written: list[tuple[bool, object]] = []
    monkeypatch.setattr(
        platform_module,
        "set_manager_autostart",
        lambda enabled, cmd=None, root=None: (written.append((enabled, root)), '"x" tray' if enabled else None)[1],
    )
    result = world["api"].set_manager_autostart(True)
    assert result == {"ok": True, "enabled": True, "command": '"x" tray'}
    assert written == [(True, None)]
    assert manager_config.load(world["config_path"]).preferences.autostart_manager is True


def test_set_manager_autostart_reports_a_registry_failure(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from usage_monitor_manager import platform as platform_module

    def boom(enabled: bool, cmd: object = None, root: object = None) -> None:
        raise OSError("accesso negato")

    monkeypatch.setattr(platform_module, "set_manager_autostart", boom)
    result = world["api"].set_manager_autostart(True)
    assert result["ok"] is False
    assert "accesso negato" in result["detail"]


def test_set_instance_autostart(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        process_control,
        "set_instance_autostart",
        lambda instance, enabled, root=None: (calls.append((instance.instance_id, enabled)), '"x"')[1],
    )
    api: JsApi = world["api"]
    api.list_instances()
    assert api.set_instance_autostart("claude", True) == {"ok": True, "enabled": True, "command": '"x"'}
    assert calls == [("claude", True)]
    assert api.set_instance_autostart("ghost", True)["ok"] is False


def test_get_manager_info(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from usage_monitor_manager import platform as platform_module

    monkeypatch.setattr(platform_module, "get_manager_autostart", lambda root=None: None)
    monkeypatch.setattr(github, "resolve_token", lambda token=None: "ghp_fake")
    monkeypatch.setattr(github, "rate_limit_state", lambda: None)
    info = world["api"].get_manager_info()
    assert info["autostart"] is False
    assert info["github_token_present"] is True  # presence only, never the value
    assert "ghp_fake" not in str(info)
    assert info["rate_limit"] is None
    assert info["config_path"] == str(world["config_path"])


def test_import_legacy_from_the_window(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from usage_monitor_manager import legacy

    monkeypatch.setattr(legacy, "find_legacy", lambda **kwargs: ([], []))
    payload = world["api"].import_legacy(True)
    assert payload["applied"] is False
    assert payload["lines"] == ["nessuna configurazione legacy trovata"]


def test_open_folder_refuses_a_missing_directory(world: dict[str, Any]) -> None:
    result = world["api"].open_folder(r"Z:\definitely\not\here")
    assert result["ok"] is False


def test_open_folder_uses_the_parent_of_a_file(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    result = world["api"].open_folder(str(target))
    assert result["ok"] is True
    assert opened == [str(tmp_path)]


def test_api_without_a_scheduler_is_still_usable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(discovery, "discover", lambda *a, **k: [])
    api = JsApi(config_path=tmp_path / "c.json")
    assert api.list_instances() == []
    assert api.get_update_state()["update_count"] == 0
    assert api.check_updates()["update_count"] == 0
    assert api.get_changelog("x") == {}


def test_a_broken_event_sink_never_breaks_an_operation(world: dict[str, Any]) -> None:
    api: JsApi = world["api"]

    def boom(_payload: JsonDict) -> None:
        raise RuntimeError("finestra chiusa")

    api.set_event_sink(boom)
    assert api.check_updates()["update_count"] == 1
    api.set_event_sink(None)
    assert api.check_updates()["update_count"] == 1
