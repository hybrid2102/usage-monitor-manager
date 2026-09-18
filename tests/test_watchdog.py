"""Tests for the instance watchdog (PLAN.md section 9.2).

Every snapshot here is built by hand: no process is scanned, none is started,
none is killed. The clock and the snapshot provider are injected, so a whole
sequence of polls runs instantly and deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from usage_monitor_manager import manager_config, watchdog
from usage_monitor_manager.manager_config import ManagerConfig, Preferences
from usage_monitor_manager.watchdog import InstanceState, Snapshot, Watchdog, WatchdogEvent

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def state(
    instance_id: str = "copilot",
    *,
    app_pids: tuple[int, ...] = (1000,),
    bootloader_pids: tuple[int, ...] = (999,),
    incomplete: int = 0,
) -> InstanceState:
    return InstanceState(
        instance_id=instance_id,
        kind=instance_id.split("_")[0],
        instance_suffix="",
        config_dir=Path.home() / ".copilot",
        exe_path=Path(r"D:\Tools\UsageMonitorForCopilot.exe"),
        app_pids=app_pids,
        bootloader_pids=bootloader_pids,
        incomplete_pairs=incomplete,
    )


def snapshot(*states: InstanceState, at: datetime = NOW) -> Snapshot:
    return Snapshot(taken_at=at, states={item.instance_id: item for item in states})


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "manager-config.json"
    manager_config.save(ManagerConfig(), path)
    return path


def make(snapshots: list[Snapshot], config_file: Path, clock: FakeClock | None = None) -> Watchdog:
    """A watchdog whose provider walks a scripted list of snapshots."""
    pending = list(snapshots)

    def provider() -> Snapshot:
        return pending.pop(0) if pending else Snapshot(taken_at=NOW, states={})

    return Watchdog(snapshot_provider=provider, config_path=config_file, clock=clock or FakeClock())


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #


def test_first_poll_has_nothing_to_compare(config_file: Path) -> None:
    dog = make([snapshot(state())], config_file)
    assert dog.poll().events == ()  # a first snapshot is a baseline, not a verdict


def test_a_disappearing_instance_is_a_crash(config_file: Path) -> None:
    dog = make([snapshot(state()), snapshot()], config_file)
    dog.poll()
    findings = dog.poll()
    assert [event.type for event in findings.events] == ["crashed"]
    assert findings.events[0].instance_id == "copilot"
    assert findings.events[0].pids == (1000,)
    assert findings.status_of("copilot") == "crashed"


def test_an_expected_stop_is_not_a_crash(config_file: Path) -> None:
    """The manager stopping an instance must not raise an alarm about itself."""
    clock = FakeClock()
    expected = watchdog.ExpectedDown(clock=clock)
    dog = Watchdog(
        snapshot_provider=lambda: next(provider),
        config_path=config_file,
        clock=clock,
        expected=expected,
    )
    provider = iter([snapshot(state()), snapshot()])
    dog.poll()
    expected.expect("copilot", ttl=90.0)
    assert dog.poll().events == ()  # the manager took it down, not a crash


def test_an_expired_announcement_stops_suppressing(config_file: Path) -> None:
    """The suppression is short lived: a crash two minutes later is still seen."""
    clock = FakeClock()
    expected = watchdog.ExpectedDown(clock=clock)
    provider = iter([snapshot(state()), snapshot()])
    dog = Watchdog(
        snapshot_provider=lambda: next(provider),
        config_path=config_file,
        clock=clock,
        expected=expected,
    )
    expected.expect("copilot", ttl=90.0)
    dog.poll()
    clock.advance(120.0)
    assert [event.type for event in dog.poll().events] == ["crashed"]


def test_coming_back_clears_the_expectation(config_file: Path) -> None:
    clock = FakeClock()
    expected = watchdog.ExpectedDown(clock=clock)
    expected.expect("copilot")
    provider = iter([snapshot(state()), snapshot(state()), snapshot()])
    dog = Watchdog(
        snapshot_provider=lambda: next(provider),
        config_path=config_file,
        clock=clock,
        expected=expected,
    )
    dog.poll()
    dog.poll()  # still running: the restart succeeded, forget the announcement
    assert expected.is_expected("copilot") is False
    assert [event.type for event in dog.poll().events] == ["crashed"]


def test_two_app_processes_are_a_duplicate(config_file: Path) -> None:
    dog = make([snapshot(state(app_pids=(1000, 2000)))], config_file)
    findings = dog.poll()
    assert [event.type for event in findings.events] == ["duplicate"]
    assert findings.events[0].pids == (1000, 2000)


def test_an_incomplete_pair_is_an_orphan(config_file: Path) -> None:
    dog = make([snapshot(state(bootloader_pids=(), incomplete=1))], config_file)
    findings = dog.poll()
    assert [event.type for event in findings.events] == ["orphan"]
    assert "incomplete process pair" in findings.events[0].detail


def test_a_healthy_snapshot_reports_nothing(config_file: Path) -> None:
    dog = make([snapshot(state()), snapshot(state())], config_file)
    dog.poll()
    assert dog.poll().events == ()
    assert dog.state.status_of("copilot") is None


def test_the_worst_anomaly_wins_the_status_column(config_file: Path) -> None:
    findings = watchdog.WatchdogState(
        last_poll=NOW,
        events=(
            WatchdogEvent(type="orphan", instance_id="copilot", kind="copilot", detail="", at=NOW),
            WatchdogEvent(type="crashed", instance_id="copilot", kind="copilot", detail="", at=NOW),
        ),
    )
    assert findings.status_of("copilot") == "crashed"


# --------------------------------------------------------------------------- #
# Auto restart
# --------------------------------------------------------------------------- #


def test_auto_restart_is_off_by_default(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    dog = make([snapshot(state()), snapshot()], config_file)
    monkeypatch.setattr(dog, "repair", lambda event: calls.append(event.type) or {"ok": True})
    dog.poll()
    findings = dog.poll()
    assert calls == []
    assert findings.events[0].repaired is False


def test_auto_restart_calls_repair_when_enabled(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager_config.save(ManagerConfig(preferences=Preferences(auto_restart_crashed=True)), config_file)
    calls: list[str] = []
    dog = make([snapshot(state()), snapshot()], config_file)
    monkeypatch.setattr(
        dog,
        "repair",
        lambda event: calls.append(event.instance_id) or {"ok": True, "detail": "started"},
    )
    dog.poll()
    findings = dog.poll()
    assert calls == ["copilot"]
    assert findings.events[0].repaired is True
    assert findings.events[0].repair_detail == "started"


def test_only_crashes_are_auto_repaired(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager_config.save(ManagerConfig(preferences=Preferences(auto_restart_crashed=True)), config_file)
    calls: list[str] = []
    dog = make([snapshot(state(app_pids=(1, 2)))], config_file)
    monkeypatch.setattr(dog, "repair", lambda event: calls.append(event.type) or {"ok": True})
    dog.poll()
    assert calls == []  # a duplicate needs a human decision, never an automatic kill


# --------------------------------------------------------------------------- #
# Repair
# --------------------------------------------------------------------------- #


def test_kill_duplicates_keeps_the_oldest(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.terminated = False

        def create_time(self) -> float:
            return {1000: 10.0, 2000: 20.0, 3000: 30.0}[self.pid]

        def exe(self) -> str:
            return r"D:\Tools\UsageMonitorForCopilot.exe"

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

    processes = {pid: FakeProcess(pid) for pid in (1000, 2000, 3000)}
    from usage_monitor_manager import process_control

    monkeypatch.setattr(process_control, "open_process", lambda pid: processes[pid])

    dog = make([snapshot(state(app_pids=(3000, 1000, 2000)))], config_file)
    findings = dog.poll()
    outcome = dog.repair(findings.events[0])

    assert outcome["ok"] is True
    assert outcome["kept"] == 1000  # the oldest survives
    assert sorted(outcome["killed"]) == [2000, 3000]
    assert processes[1000].terminated is False
    assert processes[2000].terminated is True and processes[3000].terminated is True


def test_a_foreign_process_is_never_terminated(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pid reused by something else must not be killed on the watchdog's word."""

    class Foreign:
        def create_time(self) -> float:
            return 1.0

        def exe(self) -> str:
            return r"C:\Windows\notepad.exe"

        def terminate(self) -> None:  # pragma: no cover - must not be reached
            raise AssertionError("a foreign process was terminated")

        def wait(self, timeout: float | None = None) -> int:  # pragma: no cover
            return 0

    from usage_monitor_manager import process_control

    monkeypatch.setattr(process_control, "open_process", lambda pid: Foreign())
    dog = make([snapshot(state(app_pids=(1000, 2000)))], config_file)
    findings = dog.poll()
    outcome = dog.repair(findings.events[0])
    assert outcome["ok"] is False
    assert "not a monitor process" in outcome["detail"]


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


def test_the_loop_polls_at_the_configured_interval(config_file: Path) -> None:
    waits: list[float] = []
    rounds = {"left": 3}

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        if rounds["left"] <= 0:
            return True
        rounds["left"] -= 1
        return False

    dog = Watchdog(
        snapshot_provider=lambda: snapshot(state()),
        config_path=config_file,
        wait=wait,
        interval=30.0,
    )
    dog.run_loop()
    assert waits == [30.0, 30.0, 30.0, 30.0]


def test_a_failing_provider_does_not_kill_the_loop(config_file: Path) -> None:
    def provider() -> Snapshot:
        raise RuntimeError("psutil exploded")

    dog = Watchdog(snapshot_provider=provider, config_path=config_file)
    assert dog.poll().events == ()  # reported as "nothing new", never raised
