"""Tests for the launch profiles of PLAN.md section 9.13.

:func:`profiles.plan` is pure, so most of this file is a table of machines and
expected (start, stop, leave alone) sets. The one function that touches
processes, :func:`profiles.apply_plan`, is tested against a fake
``process_control``: nothing here starts or stops anything.

The invariant worth naming is the last one: a profile stops only the
``usage-monitor`` family. A ``github-exe`` entry missing from a profile is
left running and reported, because silently killing an agent monitor is
exactly the surprise this manager exists to avoid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usage_monitor_manager import manager_config, process_control, profiles
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.manager_config import Profile
from usage_monitor_manager.model import Instance, ProcessPair

TOOLS = Path(r"D:\Tools")


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


def _monitor(kind_key: str, *, suffix: str = "", running: bool, exe: Path | None = None) -> Instance:
    kind = get_kind(kind_key)
    return Instance(
        kind=kind,
        config_dir=Path(rf"C:\Users\Marco\.{kind_key}{suffix}"),
        instance_suffix=suffix,
        exe_path=TOOLS / kind.exe_name if exe is None else exe,
        exe_version="1.0.0.0",
        processes=ProcessPair(bootloader_pid=10, app_pid=11) if running else None,
    )


def _agent(*, running: bool) -> Instance:
    kind = get_kind("agent-monitor-claude")
    return Instance(
        kind=kind,
        config_dir=TOOLS,
        instance_suffix="",
        exe_path=TOOLS / kind.exe_name,
        exe_version="0.8.0.0",
        processes=ProcessPair(bootloader_pid=20, app_pid=21) if running else None,
    )


class FakeControl:
    """Records stops and starts; answers ok unless the test says otherwise."""

    def __init__(self, *, stop_ok: bool = True, start_ok: bool = True) -> None:
        self.stop_ok = stop_ok
        self.start_ok = start_ok
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeControl:
        monkeypatch.setattr(profiles.process_control, "stop", self.stop)
        monkeypatch.setattr(profiles.process_control, "start", self.start)
        monkeypatch.setattr(profiles.time, "sleep", lambda _: None)
        return self

    def stop(self, instance: Instance, **_: object) -> process_control.StopResult:
        self.calls.append(f"stop {instance.instance_id}")
        return process_control.StopResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.stop_ok,
            detail="stopped" if self.stop_ok else "the mutex is still held",
        )

    def start(self, instance: Instance, **_: object) -> process_control.StartResult:
        self.calls.append(f"start {instance.instance_id}")
        return process_control.StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.start_ok,
            detail="started" if self.start_ok else "it exited immediately",
        )


@pytest.fixture
def control(monkeypatch: pytest.MonkeyPatch) -> FakeControl:
    return FakeControl().install(monkeypatch)


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def test_the_plan_splits_the_machine_into_three_sets() -> None:
    claude = _monitor("claude", running=True)
    copilot = _monitor("copilot", running=False)
    codex = _monitor("codex", running=True)

    result = profiles.plan(Profile(name="Lavoro", instance_ids=("claude", "copilot")), [claude, copilot, codex])

    assert [instance.instance_id for instance in result.to_start] == ["copilot"]
    assert [instance.instance_id for instance in result.to_stop] == ["codex"]
    assert [instance.instance_id for instance in result.already_running] == ["claude"]
    assert result.unknown_ids == ()
    assert result.skipped == ()
    assert result.is_empty is False
    assert result.name == "Lavoro"


def test_membership_is_matched_case_insensitively() -> None:
    claude = _monitor("claude", suffix="_578560bbc610", running=False)
    result = profiles.plan(Profile(name="P", instance_ids=("CLAUDE_578560BBC610",)), [claude])
    assert [instance.instance_id for instance in result.to_start] == ["claude_578560bbc610"]


def test_a_matching_machine_is_an_empty_plan() -> None:
    claude = _monitor("claude", running=True)
    copilot = _monitor("copilot", running=False)
    result = profiles.plan(Profile(name="Solo Claude", instance_ids=("claude",)), [claude])
    assert result.is_empty is True
    assert "nothing to do" in profiles.describe(result)[-1]

    # and a dormant non-member is not "stopped": it is already down
    result = profiles.plan(Profile(name="Solo Claude", instance_ids=("claude",)), [claude, copilot])
    assert result.to_stop == ()
    assert result.is_empty is True


def test_an_unknown_member_is_reported_not_guessed() -> None:
    claude = _monitor("claude", running=True)
    result = profiles.plan(Profile(name="P", instance_ids=("claude", "gemini")), [claude])
    assert result.unknown_ids == ("gemini",)
    assert any("gemini: no such instance" in line for line in profiles.describe(result))


def test_a_member_without_an_executable_is_skipped() -> None:
    orphan = Instance(
        kind=get_kind("claude"),
        config_dir=Path(r"C:\Users\Marco\.claude"),
        instance_suffix="",
        exe_path=None,
    )
    result = profiles.plan(Profile(name="P", instance_ids=("claude",)), [orphan])
    assert result.to_start == ()
    assert result.skipped == ("claude: executable path unknown, not started",)


def test_a_profile_stops_only_the_usage_monitor_family() -> None:
    claude = _monitor("claude", running=False)
    agent = _agent(running=True)

    result = profiles.plan(Profile(name="Lavoro", instance_ids=("claude",)), [claude, agent])

    assert [instance.instance_id for instance in result.to_stop] == []
    assert [instance.instance_id for instance in result.to_start] == ["claude"]
    assert result.skipped == ("agent-monitor-claude: github-exe kind, left running",)


def test_a_github_exe_member_is_still_started_by_the_profile() -> None:
    agent = _agent(running=False)
    result = profiles.plan(Profile(name="Tutto", instance_ids=("agent-monitor-claude",)), [agent])
    assert [instance.instance_id for instance in result.to_start] == ["agent-monitor-claude"]
    assert result.skipped == ()


def test_no_stop_others_starts_without_stopping_anything() -> None:
    claude = _monitor("claude", running=False)
    codex = _monitor("codex", running=True)
    result = profiles.plan(Profile(name="P", instance_ids=("claude",)), [claude, codex], stop_others=False)
    assert [instance.instance_id for instance in result.to_start] == ["claude"]
    assert result.to_stop == ()
    assert result.skipped == ()


def test_the_plan_is_serializable() -> None:
    claude = _monitor("claude", running=True)
    copilot = _monitor("copilot", running=False)
    payload = profiles.plan(Profile(name="P", instance_ids=("copilot", "nope")), [claude, copilot]).to_dict()
    assert payload == {
        "name": "P",
        "to_start": ["copilot"],
        "to_stop": ["claude"],
        "already_running": [],
        "unknown_ids": ["nope"],
        "skipped": [],
    }


def test_describe_lists_stops_before_starts() -> None:
    claude = _monitor("claude", running=True)
    copilot = _monitor("copilot", running=False)
    lines = profiles.describe(profiles.plan(Profile(name="P", instance_ids=("copilot",)), [claude, copilot]))
    assert lines[0] == "profile P:"
    stop_at = next(index for index, line in enumerate(lines) if "would stop" in line)
    start_at = next(index for index, line in enumerate(lines) if "would start" in line)
    assert stop_at < start_at


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #


def test_apply_stops_everything_before_starting_anything(control: FakeControl) -> None:
    claude = _monitor("claude", running=True)
    codex = _monitor("codex", running=True)
    copilot = _monitor("copilot", running=False)
    plan = profiles.plan(Profile(name="P", instance_ids=("copilot",)), [claude, codex, copilot])

    result = profiles.apply_plan(plan)

    assert control.calls == ["stop claude", "stop codex", "start copilot"]
    assert result.ok is True
    assert [item.instance_id for item in result.stops] == ["claude", "codex"]
    assert [item.instance_id for item in result.starts] == ["copilot"]
    assert result.to_dict()["ok"] is True


def test_apply_reports_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeControl(start_ok=False).install(monkeypatch)
    copilot = _monitor("copilot", running=False)
    plan = profiles.plan(Profile(name="P", instance_ids=("copilot",)), [copilot])
    result = profiles.apply_plan(plan)
    assert result.ok is False


def test_apply_waits_between_two_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two instances of one executable must not come up together."""
    waits: list[float] = []
    control = FakeControl()
    control.install(monkeypatch)
    monkeypatch.setattr(profiles.time, "sleep", lambda seconds: waits.append(seconds))

    first = _monitor("claude", running=False)
    second = _monitor("claude", suffix="_578560bbc610", running=False)
    plan = profiles.plan(Profile(name="P", instance_ids=("claude", "claude_578560bbc610")), [first, second])

    profiles.apply_plan(plan, gap_seconds=2.0)
    assert waits == [2.0], "one gap, between the two starts"
    assert control.calls == ["start claude", "start claude_578560bbc610"]


def test_apply_carries_the_unknown_and_skipped_notes_through(control: FakeControl) -> None:
    agent = _agent(running=True)
    claude = _monitor("claude", running=False)
    plan = profiles.plan(Profile(name="P", instance_ids=("claude", "gemini")), [claude, agent])
    result = profiles.apply_plan(plan)
    assert result.unknown_ids == ("gemini",)
    assert result.skipped and "left running" in result.skipped[0]
    assert "stop agent-monitor-claude" not in control.calls


# --------------------------------------------------------------------------- #
# Storage and lookup
# --------------------------------------------------------------------------- #


def test_profiles_round_trip_through_the_config_file() -> None:
    assert profiles.list_profiles() == []
    manager_config.upsert_profile(Profile(name="Lavoro", instance_ids=("claude", "copilot")))
    manager_config.upsert_profile(Profile(name="Personale", instance_ids=("codex",)))

    assert [profile.name for profile in profiles.list_profiles()] == ["Lavoro", "Personale"]
    found = profiles.resolve("lavoro")
    assert found is not None
    assert found.instance_ids == ("claude", "copilot")
    assert profiles.resolve("nope") is None

    assert manager_config.remove_profile("LAVORO") is True
    assert [profile.name for profile in profiles.list_profiles()] == ["Personale"]
    assert manager_config.remove_profile("Lavoro") is False


def test_apply_profile_refuses_an_unknown_name(control: FakeControl) -> None:
    with pytest.raises(KeyError, match="unknown profile"):
        profiles.apply_profile("nope", [])
    assert control.calls == []


def test_apply_profile_looks_the_profile_up_and_applies_it(control: FakeControl) -> None:
    manager_config.upsert_profile(Profile(name="Lavoro", instance_ids=("copilot",)))
    claude = _monitor("claude", running=True)
    copilot = _monitor("copilot", running=False)

    result = profiles.apply_profile("Lavoro", [claude, copilot])
    assert result.name == "Lavoro"
    assert control.calls == ["stop claude", "start copilot"]
