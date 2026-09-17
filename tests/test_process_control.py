"""Unit tests for start / stop / restart with fake psutil processes.

Nothing here touches the real process table, the real registry or the real
mutex namespace: ``open_process``, ``platform.mutex_is_held``,
``discovery.discover``, the ``Popen`` wrapper and the sleeps are all replaced.
The integration counterpart, with a real process and a real named mutex, lives
in ``test_process_control_integration.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import pytest

from usage_monitor_manager import process_control
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair


class FakeProcess:
    """The slice of ``psutil.Process`` that :mod:`process_control` uses."""

    def __init__(
        self,
        pid: int,
        exe: Path | None,
        *,
        survives_terminate: bool = False,
        terminate_error: BaseException | None = None,
        children: Sequence[FakeProcess] = (),
    ) -> None:
        self.pid = pid
        self._exe = exe
        self._children = list(children)
        self._survives_terminate = survives_terminate
        self._terminate_error = terminate_error
        self.alive = True
        self.calls: list[str] = []

    def exe(self) -> str:
        if self._exe is None:
            raise psutil.AccessDenied(self.pid)
        return str(self._exe)

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self._terminate_error is not None:
            raise self._terminate_error
        if not self._survives_terminate:
            self.alive = False

    def kill(self) -> None:
        self.calls.append("kill")
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait({timeout is not None})")
        if self.alive:
            raise psutil.TimeoutExpired(timeout or 0.0, pid=self.pid)
        return 0

    def parents(self) -> list[FakeProcess]:
        return []

    def children(self, recursive: bool = False) -> list[FakeProcess]:
        return list(self._children)


@dataclass
class Control:
    """Test harness: a process registry plus a scripted mutex."""

    processes: dict[int, FakeProcess] = field(default_factory=lambda: {})
    mutex_held: bool = False
    mutex_frees_after: int = 0
    mutex_probes: int = 0
    slept: list[float] = field(default_factory=lambda: [])
    spawned: list[tuple[tuple[str, ...], Mapping[str, str], Path]] = field(default_factory=lambda: [])
    spawn_pid: int = 4242
    spawn_error: BaseException | None = None
    discovered: list[Instance] = field(default_factory=lambda: [])
    events: list[process_control.ProcessEvent] = field(default_factory=lambda: [])

    def add(self, proc: FakeProcess) -> FakeProcess:
        self.processes[proc.pid] = proc
        return proc

    # -- the injected implementations ---------------------------------------
    def open_process(self, pid: int) -> psutil.Process:
        proc = self.processes.get(pid)
        if proc is None:
            raise psutil.NoSuchProcess(pid)
        return proc  # pyright: ignore[reportReturnType] - duck typed on purpose

    def is_held(self, name: str) -> bool:
        self.mutex_probes += 1
        if not self.mutex_held:
            return False
        return self.mutex_probes <= self.mutex_frees_after

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def spawn(self, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> int:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append((tuple(argv), dict(env), cwd))
        return self.spawn_pid

    def discover(self, include_dormant: bool = True) -> list[Instance]:
        assert include_dormant is False, "start() must not pay for dormant discovery"
        return list(self.discovered)

    def on_event(self, event: process_control.ProcessEvent) -> None:
        self.events.append(event)

    def phases(self) -> list[str]:
        return [event.phase for event in self.events]


@pytest.fixture
def control(monkeypatch: pytest.MonkeyPatch) -> Iterator[Control]:
    harness = Control()
    monkeypatch.setattr(process_control, "open_process", harness.open_process)
    monkeypatch.setattr(process_control, "_sleep", harness.sleep)
    monkeypatch.setattr(process_control, "_spawn", harness.spawn)
    monkeypatch.setattr(process_control.platform, "mutex_is_held", harness.is_held)
    monkeypatch.setattr(process_control.discovery, "discover", harness.discover)
    yield harness


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    path = tmp_path / "Tools" / "UsageMonitorForClaude.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ fake")
    return path


def make_instance(
    exe_path: Path | None,
    *,
    config_dir: Path | None = None,
    app_pid: int | None = None,
    bootloader_pid: int | None = None,
    kind_key: str = "claude",
) -> Instance:
    kind = get_kind(kind_key)
    directory = config_dir if config_dir is not None else kind.default_config_dir
    suffix = "" if config_dir is None else "_abcdef123456"
    pair = None if app_pid is None else ProcessPair(bootloader_pid=bootloader_pid, app_pid=app_pid)
    return Instance(
        kind=kind,
        config_dir=directory,
        instance_suffix=suffix,
        exe_path=exe_path,
        processes=pair,
    )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_mutex_name_matches_the_monitors_own_naming(exe: Path) -> None:
    default = make_instance(exe)
    assert process_control.mutex_name(default) == "UsageMonitorForClaude_SingleInstance"
    custom = make_instance(exe, config_dir=Path(r"C:\Users\Marco\.claude-valeria"))
    assert process_control.mutex_name(custom) == "UsageMonitorForClaude_SingleInstance_abcdef123456"


def test_argv_and_env_for_the_default_config_dir(exe: Path) -> None:
    instance = make_instance(exe)
    assert process_control.build_argv(instance) == [str(exe)]
    assert process_control.build_env_overrides(instance) == {}


def test_argv_and_env_for_a_custom_config_dir(exe: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".claude-valeria"
    instance = make_instance(exe, config_dir=directory)
    assert process_control.build_argv(instance) == [str(exe), f"--config-dir={directory}"]
    assert process_control.build_env_overrides(instance) == {"CLAUDE_CONFIG_DIR": str(directory)}
    assert process_control.build_env(instance)["CLAUDE_CONFIG_DIR"] == str(directory)


def test_default_instance_drops_an_inherited_config_dir_variable(
    exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: running the manager from a shell that exports the variable
    # used to start a second copy of *that* instance instead of the default one.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-valeria"))
    instance = make_instance(exe)
    assert process_control.strips_env_var(instance) is True
    assert "CLAUDE_CONFIG_DIR" not in process_control.build_env(instance)


def test_non_default_instance_sets_the_config_dir_variable(
    exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-somewhere-else"))
    directory = tmp_path / ".claude-valeria"
    instance = make_instance(exe, config_dir=directory)
    assert process_control.strips_env_var(instance) is False
    assert process_control.build_env(instance)["CLAUDE_CONFIG_DIR"] == str(directory)


@pytest.mark.parametrize("variable", ["_MEIPASS2", "_PYI_ARCHIVE_FILE", "_PYI_APPLICATION_HOME_DIR"])
def test_pyinstaller_variables_never_reach_the_child(
    variable: str, exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-file child inheriting these dies with "Failed to load Python DLL"."""
    monkeypatch.setenv(variable, str(tmp_path / "_MEI123456"))
    for instance in (make_instance(exe), make_instance(exe, config_dir=tmp_path / ".claude-valeria")):
        env = process_control.build_env(instance)
        assert variable not in env
        assert not any(key.startswith(("_PYI_", "_MEI")) for key in env)


def test_scrubbing_keeps_the_rest_of_the_environment(exe: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMM_KEEP_ME", "yes")
    monkeypatch.setenv("_MEIPASS2", "no")
    env = process_control.build_env(make_instance(exe))
    assert env["UMM_KEEP_ME"] == "yes"
    assert "_MEIPASS2" not in env


def test_build_argv_without_an_executable_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="no executable path"):
        process_control.build_argv(make_instance(None))


# --------------------------------------------------------------------------- #
# stop
# --------------------------------------------------------------------------- #


def test_stop_terminates_the_app_before_the_bootloader(control: Control, exe: Path) -> None:
    app = control.add(FakeProcess(200, exe))
    boot = control.add(FakeProcess(100, exe))
    instance = make_instance(exe, app_pid=200, bootloader_pid=100)

    result = process_control.stop(instance, timeout=5.0, on_event=control.on_event)

    assert result.ok is True
    assert result.terminated_pids == (200, 100), "the child must be signalled first"
    assert result.killed_pids == ()
    assert result.mutex_released is True
    assert result.mutex_name == "UsageMonitorForClaude_SingleInstance"
    assert "terminate" in app.calls and "terminate" in boot.calls
    assert "kill" not in app.calls and "kill" not in boot.calls
    assert "stop_end" in control.phases()
    assert result.to_dict()["terminated_pids"] == [200, 100]


def test_stop_also_stops_a_same_executable_child(control: Control, exe: Path) -> None:
    """Discovery may have caught only the bootloader; the app holds the mutex."""
    app = FakeProcess(201, exe)
    boot = control.add(FakeProcess(200, exe, children=[app]))
    control.add(app)
    instance = make_instance(exe, app_pid=200)

    result = process_control.stop(instance, timeout=5.0)

    assert result.terminated_pids == (201, 200), "the child goes before its parent"
    assert "terminate" in app.calls and "terminate" in boot.calls


def test_stop_leaves_a_foreign_child_alone(control: Control, exe: Path, tmp_path: Path) -> None:
    webview = FakeProcess(201, tmp_path / "msedgewebview2.exe")
    control.add(FakeProcess(200, exe, children=[webview]))
    control.add(webview)

    result = process_control.stop(make_instance(exe, app_pid=200), timeout=5.0)

    assert webview.calls == []
    assert result.terminated_pids == (200,)
    assert result.skipped_pids == ()


def test_stop_refuses_a_pid_running_another_executable(control: Control, exe: Path, tmp_path: Path) -> None:
    impostor = control.add(FakeProcess(200, tmp_path / "Other" / "UsageMonitorForClaude.exe"))
    instance = make_instance(exe, app_pid=200)

    result = process_control.stop(instance, timeout=1.0, on_event=control.on_event)

    assert impostor.calls == [], "a same-named executable elsewhere is not ours"
    assert result.skipped_pids == (200,)
    assert result.ok is False
    assert "nothing we were allowed to stop" in result.detail


def test_stop_refuses_a_pid_whose_executable_cannot_be_read(control: Control, exe: Path) -> None:
    denied = control.add(FakeProcess(200, None))
    result = process_control.stop(make_instance(exe, app_pid=200), timeout=1.0)
    assert denied.calls == []
    assert result.skipped_pids == (200,)


def test_stop_never_touches_this_process_or_its_ancestors(control: Control, exe: Path) -> None:
    mine = control.add(FakeProcess(os.getpid(), exe))
    instance = make_instance(exe, app_pid=os.getpid())

    result = process_control.stop(instance, timeout=1.0)

    assert mine.calls == []
    assert result.skipped_pids == (os.getpid(),)


def test_stop_falls_back_to_kill_when_terminate_is_ignored(control: Control, exe: Path) -> None:
    stubborn = control.add(FakeProcess(200, exe, survives_terminate=True))
    result = process_control.stop(make_instance(exe, app_pid=200), timeout=0.0, on_event=control.on_event)

    assert stubborn.calls[:1] == ["terminate"]
    assert "kill" in stubborn.calls
    assert result.killed_pids == (200,)
    assert result.ok is True
    assert "kill" in control.phases()


def test_stop_skips_the_kill_when_the_pid_was_recycled(
    control: Control, exe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exe guard is re-evaluated before the kill, not only before terminate."""
    proc = control.add(FakeProcess(200, exe, survives_terminate=True))
    seen: list[int] = []

    def flaky(target: psutil.Process, exe_path: Path) -> bool:
        seen.append(target.pid)
        return len(seen) == 1  # ours for the terminate, somebody else by the kill

    monkeypatch.setattr(process_control, "_is_ours", flaky)
    result = process_control.stop(make_instance(exe, app_pid=200), timeout=0.0)

    assert "kill" not in proc.calls
    assert result.killed_pids == ()


def test_stop_waits_for_the_mutex_and_then_succeeds(control: Control, exe: Path) -> None:
    control.add(FakeProcess(200, exe))
    control.mutex_held = True
    control.mutex_frees_after = 2  # released on the third probe

    result = process_control.stop(make_instance(exe, app_pid=200), timeout=30.0, on_event=control.on_event)

    assert result.ok is True
    assert result.mutex_released is True
    assert "mutex_wait" in control.phases()
    assert "mutex_free" in control.phases()
    assert control.slept, "the wait must actually poll"


def test_stop_fails_when_the_mutex_is_never_released(control: Control, exe: Path) -> None:
    control.add(FakeProcess(200, exe))
    control.mutex_held = True
    control.mutex_frees_after = 10_000

    result = process_control.stop(make_instance(exe, app_pid=200), timeout=0.0, on_event=control.on_event)

    assert result.ok is False
    assert result.mutex_released is False
    assert "mutex is still held" in result.detail
    assert "mutex_busy" in control.phases()


def test_stop_of_a_dormant_instance_is_a_no_op(control: Control, exe: Path) -> None:
    result = process_control.stop(make_instance(exe), timeout=1.0)
    assert (result.ok, result.already_stopped, result.detail) == (True, True, "already stopped")


def test_stop_of_a_dormant_instance_whose_mutex_is_held_is_a_failure(control: Control, exe: Path) -> None:
    control.mutex_held = True
    control.mutex_frees_after = 10_000
    result = process_control.stop(make_instance(exe), timeout=1.0)
    assert result.ok is False
    assert result.mutex_released is False


def test_stop_without_an_executable_path_refuses_to_signal_anything(control: Control) -> None:
    control.add(FakeProcess(200, Path("whatever.exe")))
    result = process_control.stop(make_instance(None, app_pid=200), timeout=1.0)
    assert result.ok is False
    assert "refusing to terminate" in result.detail
    assert control.processes[200].calls == []


def test_stop_ignores_a_pid_that_has_already_exited(control: Control, exe: Path) -> None:
    control.add(FakeProcess(200, exe))
    result = process_control.stop(make_instance(exe, app_pid=200, bootloader_pid=999), timeout=1.0)
    assert result.terminated_pids == (200,)
    assert result.ok is True


def test_stop_reports_a_terminate_that_is_refused(control: Control, exe: Path) -> None:
    blocked = control.add(FakeProcess(200, exe, terminate_error=psutil.AccessDenied(200)))
    result = process_control.stop(make_instance(exe, app_pid=200), timeout=1.0, on_event=control.on_event)
    assert blocked.calls == ["terminate"]
    assert result.skipped_pids == (200,)
    assert result.ok is False
    assert "error" in control.phases()


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #


def test_start_builds_the_expected_argv_env_and_cwd(control: Control, exe: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".claude-valeria"
    instance = make_instance(exe, config_dir=directory)
    control.discovered = [make_instance(exe, config_dir=directory, app_pid=777, bootloader_pid=776)]

    result = process_control.start(instance, wait_seconds=0.1, on_event=control.on_event)

    assert result.ok is True
    argv, env, cwd = control.spawned[0]
    assert argv == (str(exe), f"--config-dir={directory}")
    assert env["CLAUDE_CONFIG_DIR"] == str(directory)
    assert cwd == exe.parent
    assert (result.bootloader_pid, result.app_pid) == (776, 777)
    assert result.spawned_pid == control.spawn_pid
    assert result.argv == (str(exe), f"--config-dir={directory}")
    assert result.to_dict()["env_overrides"] == {"CLAUDE_CONFIG_DIR": str(directory)}
    assert control.slept == [0.1]


def test_start_of_the_default_instance_passes_no_flag(control: Control, exe: Path) -> None:
    instance = make_instance(exe)
    control.discovered = [make_instance(exe, app_pid=5)]
    result = process_control.start(instance, wait_seconds=0.0)
    assert control.spawned[0][0] == (str(exe),)
    assert result.ok is True


def test_start_refuses_while_the_mutex_is_held(control: Control, exe: Path) -> None:
    control.mutex_held = True
    control.mutex_frees_after = 10_000

    result = process_control.start(make_instance(exe), wait_seconds=0.0, on_event=control.on_event)

    assert result.ok is False
    assert result.already_running is True
    assert "already running" in result.detail
    assert control.spawned == [], "nothing may be launched next to a live instance"
    assert "mutex_busy" in control.phases()


def test_start_fails_when_the_instance_does_not_come_up(control: Control, exe: Path) -> None:
    control.discovered = []
    result = process_control.start(make_instance(exe), wait_seconds=0.0)
    assert result.ok is False
    assert "no running instance was found" in result.detail
    assert result.spawned_pid == control.spawn_pid


def test_start_ignores_a_running_instance_of_another_config_dir(control: Control, exe: Path, tmp_path: Path) -> None:
    control.discovered = [make_instance(exe, config_dir=tmp_path / "somewhere-else", app_pid=9)]
    result = process_control.start(make_instance(exe), wait_seconds=0.0)
    assert result.ok is False


def test_start_reports_a_missing_executable(control: Control, tmp_path: Path) -> None:
    result = process_control.start(make_instance(tmp_path / "gone.exe"), wait_seconds=0.0)
    assert result.ok is False
    assert "executable not found" in result.detail
    assert control.spawned == []


def test_start_reports_a_launch_failure(control: Control, exe: Path) -> None:
    control.spawn_error = OSError("access is denied")
    result = process_control.start(make_instance(exe), wait_seconds=0.0, on_event=control.on_event)
    assert result.ok is False
    assert "launch failed" in result.detail
    assert "error" in control.phases()


def test_start_without_an_executable_path(control: Control) -> None:
    result = process_control.start(make_instance(None), wait_seconds=0.0)
    assert result.ok is False
    assert "executable path unknown" in result.detail


# --------------------------------------------------------------------------- #
# Batch operations
# --------------------------------------------------------------------------- #


def test_restart_stops_everything_before_starting_anything(
    control: Control, exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_dir, second_dir = tmp_path / ".claude-a", tmp_path / ".claude-b"
    control.add(FakeProcess(11, exe))
    control.add(FakeProcess(22, exe))
    first = make_instance(exe, config_dir=first_dir, app_pid=11)
    second = make_instance(exe, config_dir=second_dir, app_pid=22)
    control.discovered = [
        make_instance(exe, config_dir=first_dir, app_pid=111),
        make_instance(exe, config_dir=second_dir, app_pid=222),
    ]

    order: list[str] = []

    def tracking_spawn(argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> int:
        order.append(f"start:{argv[-1]}")
        return control.spawn(argv, env, cwd)

    original_stop = process_control.stop

    def tracking_stop(instance: Instance, timeout: float = 15.0, on_event: object = None) -> process_control.StopResult:
        order.append(f"stop:{instance.instance_id}")
        return original_stop(instance, timeout=timeout)

    monkeypatch.setattr(process_control, "_spawn", tracking_spawn)
    monkeypatch.setattr(process_control, "stop", tracking_stop)
    outcome = process_control.restart([first, second], gap_seconds=2.0, start_wait=0.0)

    assert outcome.ok is True
    assert order[:2] == [f"stop:{first.instance_id}", f"stop:{second.instance_id}"], "both stops come first"
    assert [step for step in order if step.startswith("start:")] == order[2:]
    assert len(outcome.stops) == 2 and len(outcome.starts) == 2
    assert 2.0 in control.slept, "the second start waits for the gap"
    assert outcome.to_dict()["ok"] is True


def test_restart_does_not_start_an_instance_whose_stop_failed(control: Control, exe: Path) -> None:
    control.add(FakeProcess(11, exe))
    control.mutex_held = True
    control.mutex_frees_after = 10_000
    instance = make_instance(exe, app_pid=11)

    outcome = process_control.restart([instance], gap_seconds=0.0, stop_timeout=0.0, start_wait=0.0)

    assert outcome.ok is False
    assert outcome.starts[0].ok is False
    assert "the stop did not complete" in outcome.starts[0].detail
    assert control.spawned == []


def test_stop_all_and_restart_all_only_consider_running_instances(control: Control, exe: Path) -> None:
    control.add(FakeProcess(11, exe))
    running = make_instance(exe, app_pid=11)
    dormant = make_instance(exe, config_dir=Path(r"C:\dormant"))

    results = process_control.stop_all([running, dormant], timeout=1.0)

    assert [result.instance_id for result in results] == [running.instance_id]


def test_start_missing_only_starts_the_dormant_ones(control: Control, exe: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".claude-valeria"
    running = make_instance(exe, app_pid=11)
    dormant = make_instance(exe, config_dir=directory)
    control.discovered = [make_instance(exe, config_dir=directory, app_pid=99)]

    results = process_control.start_missing([running, dormant], wait_seconds=0.0)

    assert [result.instance_id for result in results] == [dormant.instance_id]
    assert results[0].ok is True


def test_batch_helpers_discover_when_given_nothing(
    control: Control, exe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_control.discovery, "discover", lambda include_dormant=True: [])
    assert process_control.stop_all() == []
    assert process_control.start_missing() == []
    assert process_control.restart_all().ok is True


# --------------------------------------------------------------------------- #
# Dry-run descriptions
# --------------------------------------------------------------------------- #


def test_describe_stop_and_start(exe: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".claude-valeria"
    running = make_instance(exe, config_dir=directory, app_pid=93020, bootloader_pid=82104)
    assert process_control.describe_stop(running) == (
        "would stop claude_abcdef123456 pids 93020, 82104 then wait for the "
        "UsageMonitorForClaude_SingleInstance_abcdef123456 mutex"
    )
    assert process_control.describe_start(running) == (
        f"would start claude_abcdef123456: {exe} --config-dir={directory} CLAUDE_CONFIG_DIR={directory}"
    )
    assert "not running" in process_control.describe_stop(make_instance(exe))
    assert "executable path unknown" in process_control.describe_start(make_instance(None))


def test_describe_start_mentions_the_stripped_variable(
    exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-valeria"))
    assert "(without CLAUDE_CONFIG_DIR)" in process_control.describe_start(make_instance(exe))


def test_process_event_is_serializable() -> None:
    event = process_control.ProcessEvent("claude", "stop", "terminate", "bye", pid=7)
    assert event.to_dict() == {
        "instance_id": "claude",
        "action": "stop",
        "phase": "terminate",
        "message": "bye",
        "pid": 7,
    }
