"""Tests for the Phase 7 CLI: ``kind``, ``diag``, ``profile``, ``status``.

Same safety rules as the other CLI test files: ``discover``, the update engine,
the network and the credential store are replaced, ``LOCALAPPDATA`` points at
``tmp_path`` so ``manager-config.json``, ``diag.json`` and ``status.json`` are
temporary files, and no test starts, stops, updates or registers anything real.
``umm diag start`` and ``umm profile apply`` reach a faked
``process_control``; their ``--dry-run`` paths are asserted to reach nothing at
all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from usage_monitor_manager import cli, diag, manager_config, process_control, profiles, status_file
from usage_monitor_manager.cli import EXIT_FAILED, EXIT_NO_MATCH, main
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair

TOOLS = Path(r"D:\Tools")


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


@pytest.fixture(autouse=True)
def quiet_logging() -> object:
    diag.reset_logging()
    yield
    diag.reset_logging()


def _monitor(kind_key: str, *, running: bool, suffix: str = "") -> Instance:
    kind = get_kind(kind_key)
    return Instance(
        kind=kind,
        config_dir=Path(rf"C:\Users\Marco\.{kind_key}{suffix}"),
        instance_suffix=suffix,
        exe_path=TOOLS / kind.exe_name,
        exe_version="1.0.0.0",
        processes=ProcessPair(bootloader_pid=10, app_pid=11) if running else None,
    )


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> list[Instance]:
    """Claude running, Copilot down: enough for a profile and for diag."""
    instances = [_monitor("claude", running=True), _monitor("copilot", running=False)]
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: list(instances))
    return instances


class FakeControl:
    """Records stops and starts done through either module under test."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeControl:
        for module in (diag.process_control, profiles.process_control):
            monkeypatch.setattr(module, "stop", self.stop)
            monkeypatch.setattr(module, "start", self.start)
        monkeypatch.setattr(profiles.time, "sleep", lambda _: None)
        return self

    def stop(self, instance: Instance, **_: object) -> process_control.StopResult:
        self.calls.append(f"stop {instance.instance_id}")
        return process_control.StopResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=True,
            detail="stopped",
        )

    def start(
        self,
        instance: Instance,
        wait_seconds: float = 0.0,
        on_event: object = None,
        *,
        extra_args: tuple[str, ...] = (),
        log_path: Path | None = None,
    ) -> process_control.StartResult:
        suffix = f" {' '.join(extra_args)}" if extra_args else ""
        self.calls.append(f"start {instance.instance_id}{suffix}")
        return process_control.StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=True,
            detail="started",
            argv=(str(instance.exe_path), *extra_args),
        )


@pytest.fixture
def control(monkeypatch: pytest.MonkeyPatch) -> FakeControl:
    return FakeControl().install(monkeypatch)


# --------------------------------------------------------------------------- #
# umm kind
# --------------------------------------------------------------------------- #


def test_kind_list_shows_both_families(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kind", "list"]) == 0
    out = capsys.readouterr().out
    assert "KEY" in out and "FAMILY" in out and "SOURCE" in out
    assert "usage-monitor" in out and "github-exe" in out
    assert "AgentMonitorForClaude.exe" in out
    assert "builtin" in out


def test_kind_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kind", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["key"] for row in rows] == ["claude", "copilot", "codex", "agent-monitor-claude"]
    assert rows[-1]["family"] == "github-exe"


def test_kind_add_persists_and_shows_up_in_the_list(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "kind",
            "add",
            "statusline",
            "--display-name",
            "Statusline Helper",
            "--exe-name",
            "StatuslineHelper.exe",
            "--repo",
            "hybrid2102/statusline-helper",
        ]
    )
    assert code == 0
    assert "kind statusline added" in capsys.readouterr().out
    assert [entry.key for entry in manager_config.load().custom_kinds] == ["statusline"]

    assert main(["kind", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    added = next(row for row in rows if row["key"] == "statusline")
    assert added["source"] == "custom"
    assert added["asset_pattern"] == "StatuslineHelper.exe"


def test_kind_add_json_reports_the_problems_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["kind", "add", "statusline", "--exe-name", "x.bat", "--repo", "nope", "--json"])
    assert code == EXIT_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["added"] is False
    assert len(payload["problems"]) == 2
    assert manager_config.load().custom_kinds == ()


def test_kind_add_refuses_to_shadow_a_builtin(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["kind", "add", "claude", "--exe-name", "Mine.exe", "--repo", "me/mine"])
    assert code == EXIT_FAILED
    assert "built-in kind" in capsys.readouterr().out


def test_kind_add_needs_the_mandatory_flags() -> None:
    with pytest.raises(SystemExit):
        main(["kind", "add", "statusline"])


def test_kind_add_needs_a_key() -> None:
    with pytest.raises(SystemExit):
        main(["kind", "add"])


def test_kind_remove_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    main(["kind", "add", "statusline", "--exe-name", "StatuslineHelper.exe", "--repo", "me/helper"])
    capsys.readouterr()
    assert main(["kind", "remove", "statusline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"key": "statusline", "removed": True}
    assert manager_config.load().custom_kinds == ()


def test_kind_remove_of_a_builtin_is_a_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kind", "remove", "claude"]) == EXIT_NO_MATCH
    assert "built-in kinds cannot be removed" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# umm diag
# --------------------------------------------------------------------------- #


def test_diag_show_without_a_session(capsys: pytest.CaptureFixture[str], machine: list[Instance]) -> None:
    assert main(["diag", "show"]) == 0
    out = capsys.readouterr().out
    assert "no instance is running in diagnostic mode" in out
    assert "manager.log" in out


def test_diag_show_json_carries_the_manager_log_path(
    capsys: pytest.CaptureFixture[str], machine: list[Instance]
) -> None:
    assert main(["diag", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session"] is None
    assert payload["lines"] == []
    assert payload["manager_log"] == str(diag.manager_log_path())


def test_diag_start_dry_run_touches_nothing(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    assert main(["diag", "start", "--instance", "claude", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would stop claude" in out
    assert "--verbose" in out
    assert "would append stdout and stderr to" in out
    assert control.calls == []
    assert diag.read_state() is None


def test_diag_start_restarts_the_instance_verbose(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    assert main(["diag", "start", "--instance", "claude"]) == 0
    assert control.calls == ["stop claude", "start claude --verbose"]
    out = capsys.readouterr().out
    assert "claude:" in out
    assert "log:" in out
    session = diag.read_state()
    assert session is not None
    assert session.instance_id == "claude"


def test_diag_show_then_reports_the_session(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    main(["diag", "start", "--instance", "claude"])
    session = diag.read_state()
    assert session is not None
    session.log_path.parent.mkdir(parents=True, exist_ok=True)
    session.log_path.write_text("boot\nready\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["diag", "show", "--lines", "5", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session"]["instance_id"] == "claude"
    assert "ready" in payload["lines"]


def test_diag_stop_goes_back_to_normal_mode(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    main(["diag", "start", "--instance", "claude"])
    control.calls.clear()
    capsys.readouterr()

    assert main(["diag", "stop", "--json"]) == 0
    assert control.calls == ["stop claude", "start claude"], "restarted with no extra argument"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["detail"] == "back to normal mode"
    assert diag.read_state() is None


def test_diag_stop_dry_run_touches_nothing(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    assert main(["diag", "stop", "--instance", "claude", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would stop claude" in out
    assert "would start it again without --verbose" in out
    assert control.calls == []


def test_diag_start_without_a_selector_refuses_to_guess(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    assert main(["diag", "start"]) == EXIT_NO_MATCH
    assert "pass --instance ID" in capsys.readouterr().out
    assert control.calls == []


def test_diag_stop_defaults_to_the_recorded_instance(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    """Two instances, no --instance: the session says which one is verbose."""
    main(["diag", "start", "--instance", "copilot"])
    control.calls.clear()
    capsys.readouterr()
    assert main(["diag", "stop"]) == 0
    assert control.calls == ["start copilot"], "copilot was down, so only a start"


# --------------------------------------------------------------------------- #
# umm profile
# --------------------------------------------------------------------------- #


def test_profile_list_when_there_is_none(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "list"]) == 0
    assert "no launch profile configured" in capsys.readouterr().out


def test_profile_add_then_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "add", "Lavoro", "--instance", "claude", "--instance", "copilot"]) == 0
    capsys.readouterr()
    assert main(["profile", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"name": "Lavoro", "instance_ids": ["claude", "copilot"]}]


def test_profile_add_deduplicates_and_needs_a_member() -> None:
    assert main(["profile", "add", "P", "--instance", "claude", "--instance", "claude"]) == 0
    assert manager_config.load().profiles[0].instance_ids == ("claude",)
    with pytest.raises(SystemExit):
        main(["profile", "add", "P"])
    with pytest.raises(SystemExit):
        main(["profile", "apply"])


def test_profile_remove(capsys: pytest.CaptureFixture[str]) -> None:
    main(["profile", "add", "Lavoro", "--instance", "claude"])
    capsys.readouterr()
    assert main(["profile", "remove", "lavoro"]) == 0
    assert "removed" in capsys.readouterr().out
    assert main(["profile", "remove", "lavoro"]) == EXIT_NO_MATCH


def test_profile_show_prints_the_plan_without_applying_it(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Lavoro", instance_ids=("copilot",)))
    assert main(["profile", "show", "Lavoro"]) == 0
    out = capsys.readouterr().out
    assert "profile Lavoro:" in out
    assert "would stop claude" in out
    assert "would start copilot" in out
    assert control.calls == []


def test_profile_apply_dry_run_touches_nothing(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Lavoro", instance_ids=("copilot",)))
    assert main(["profile", "apply", "Lavoro", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["to_stop"] == ["claude"]
    assert payload["to_start"] == ["copilot"]
    assert any("would start" in line for line in payload["steps"])
    assert control.calls == []


def test_profile_apply_with_yes_stops_then_starts(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Lavoro", instance_ids=("copilot",)))
    assert main(["profile", "apply", "Lavoro", "--yes", "--json"]) == 0
    assert control.calls == ["stop claude", "start copilot"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["profile"] == "Lavoro"


def test_profile_apply_without_yes_asks_and_honours_a_no(
    capsys: pytest.CaptureFixture[str],
    machine: list[Instance],
    control: FakeControl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Lavoro", instance_ids=("copilot",)))
    monkeypatch.setattr(cli, "_confirm", lambda question: False)
    assert main(["profile", "apply", "Lavoro"]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert control.calls == []


def test_profile_apply_no_stop_others_only_starts(machine: list[Instance], control: FakeControl) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Lavoro", instance_ids=("copilot",)))
    assert main(["profile", "apply", "Lavoro", "--yes", "--no-stop-others", "--json"]) == 0
    assert control.calls == ["start copilot"]


def test_profile_apply_of_an_unknown_name(capsys: pytest.CaptureFixture[str], machine: list[Instance]) -> None:
    assert main(["profile", "apply", "Nope", "--yes"]) == EXIT_NO_MATCH
    assert "see 'umm profile list'" in capsys.readouterr().out


def test_profile_apply_of_a_matching_machine_does_nothing(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], control: FakeControl
) -> None:
    manager_config.upsert_profile(manager_config.Profile(name="Solo Claude", instance_ids=("claude",)))
    assert main(["profile", "apply", "Solo Claude", "--yes"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert control.calls == []


# --------------------------------------------------------------------------- #
# umm status --write-file
# --------------------------------------------------------------------------- #


def test_status_prints_a_summary(capsys: pytest.CaptureFixture[str], machine: list[Instance]) -> None:
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1/2 running" in out
    assert "claude" in out and "copilot" in out
    assert "updates: not checked (use --check)" in out
    assert "watchdog: nothing anomalous" in out


def test_status_json_is_the_document(capsys: pytest.CaptureFixture[str], machine: list[Instance]) -> None:
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] == 1
    assert [row["id"] for row in payload["instances"]] == ["claude", "copilot"]


def test_status_write_file_defaults_to_the_app_directory(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], local_appdata: Path
) -> None:
    assert main(["status", "--write-file"]) == 0
    target = local_appdata / "UsageMonitorManager" / "status.json"
    assert f"status file: {target}" in capsys.readouterr().out
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["running"] == 1
    assert payload["generated_at"]


def test_status_write_file_takes_a_path(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], tmp_path: Path
) -> None:
    target = tmp_path / "out" / "umm.json"
    assert main(["status", "--write-file", str(target)]) == 0
    capsys.readouterr()
    assert json.loads(target.read_text(encoding="utf-8"))["version"]


def test_status_write_file_takes_a_directory(machine: list[Instance], tmp_path: Path) -> None:
    assert main(["status", "--write-file", f"{tmp_path}"]) == 0
    assert (tmp_path / status_file.DEFAULT_FILENAME).is_file()


def test_status_write_file_with_json_says_where_it_wrote_on_stderr(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], tmp_path: Path
) -> None:
    target = tmp_path / "status.json"
    assert main(["status", "--write-file", str(target), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["running"] == 1, "stdout stays pure JSON for a consumer"
    assert f"written to {target}" in captured.err


def test_a_status_file_that_cannot_be_written_fails_loudly(
    capsys: pytest.CaptureFixture[str], machine: list[Instance], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object, **__: object) -> Path:
        raise OSError("access is denied")

    monkeypatch.setattr(status_file, "write_status_file", refuse)
    assert main(["status", "--write-file"]) == EXIT_FAILED
    assert "status file not written: access is denied" in capsys.readouterr().out


def test_status_without_write_file_writes_nothing(machine: list[Instance], local_appdata: Path) -> None:
    assert main(["status"]) == 0
    assert not (local_appdata / "UsageMonitorManager" / "status.json").exists()
