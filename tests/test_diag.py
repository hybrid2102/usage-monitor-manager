"""Tests for diagnostic mode and the manager log (PLAN.md section 9.7).

``process_control.stop`` and ``process_control.start`` are replaced in every
test: nothing here stops, starts or restarts anything. What is asserted is
exactly what diagnostic mode is supposed to guarantee:

* the instance is restarted with ``--verbose`` and with a log path under the
  manager's own ``logs`` directory - never inside a config dir;
* ``stop`` brings it back to normal mode, i.e. starts it again with no extra
  argument and forgets the recorded session;
* a failed stop launches nothing at all;
* the session survives a manager restart, because it lives in ``diag.json``;
* ``manager.log`` is a rotating 1 MB x 3 file and a logging setup that cannot
  write does not take the manager down with it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest

from usage_monitor_manager import diag, process_control
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair

CLAUDE_EXE = Path(r"D:\Tools\UsageMonitorForClaude.exe")


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``diag.json`` and ``logs/`` live in tmp_path, never in the real one."""
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


@pytest.fixture(autouse=True)
def quiet_logging() -> Any:
    """Every test starts and ends with the ``umm`` logger unconfigured."""
    diag.reset_logging()
    yield
    diag.reset_logging()


def _instance(*, running: bool = True, suffix: str = "") -> Instance:
    return Instance(
        kind=get_kind("claude"),
        config_dir=Path(r"C:\Users\Marco\.claude-valeria"),
        instance_suffix=suffix,
        exe_path=CLAUDE_EXE,
        exe_version="1.22.0.0",
        processes=ProcessPair(bootloader_pid=100, app_pid=101) if running else None,
    )


class FakeControl:
    """Records every stop/start and answers with whatever the test wants."""

    def __init__(self, *, stop_ok: bool = True, start_ok: bool = True) -> None:
        self.stop_ok = stop_ok
        self.start_ok = start_ok
        self.stops: list[str] = []
        self.starts: list[tuple[str, tuple[str, ...], Path | None]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeControl:
        monkeypatch.setattr(diag.process_control, "stop", self.stop)
        monkeypatch.setattr(diag.process_control, "start", self.start)
        return self

    def stop(self, instance: Instance, **_: object) -> process_control.StopResult:
        self.stops.append(instance.instance_id)
        return process_control.StopResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.stop_ok,
            detail="stopped" if self.stop_ok else "the mutex is still held",
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
        self.starts.append((instance.instance_id, tuple(extra_args), log_path))
        return process_control.StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.start_ok,
            detail="started" if self.start_ok else "it exited immediately",
            argv=tuple(process_control.build_argv(instance, extra_args)),
            app_pid=4242 if self.start_ok else None,
        )


@pytest.fixture
def control(monkeypatch: pytest.MonkeyPatch) -> FakeControl:
    return FakeControl().install(monkeypatch)


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def test_log_paths_live_under_the_manager_directory(local_appdata: Path) -> None:
    root = local_appdata / "UsageMonitorManager"
    assert diag.log_dir() == root / "logs"
    assert diag.manager_log_path() == root / "logs" / "manager.log"
    assert diag.state_path() == root / "diag.json"
    assert diag.instance_log_dir("claude_578560bbc610") == root / "logs" / "claude_578560bbc610"


def test_a_hostile_instance_id_cannot_escape_the_log_directory() -> None:
    escaped = diag.instance_log_dir(r"..\..\Windows\System32")
    assert escaped.parent == diag.log_dir()
    assert "\\" not in escaped.name and "/" not in escaped.name
    assert diag.instance_log_dir("").name == "instance"


def test_the_log_file_name_is_a_local_timestamp() -> None:
    moment = datetime(2026, 9, 18, 12, 30, 5, tzinfo=UTC)
    path = diag.log_path_for("claude", now=moment)
    assert path.parent == diag.instance_log_dir("claude")
    assert path.suffix == ".log"
    assert path.stem == moment.astimezone().strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------- #
# The argv of diagnostic mode
# --------------------------------------------------------------------------- #


def test_the_diagnostic_argv_is_just_verbose() -> None:
    assert diag.DIAG_ARGS == ("--verbose",)
    argv = process_control.build_argv(_instance(), diag.DIAG_ARGS)
    assert argv[0] == str(CLAUDE_EXE)
    assert argv[-1] == "--verbose"


def test_describe_start_touches_nothing(local_appdata: Path) -> None:
    lines = diag.describe_start(_instance(), now=datetime(2026, 9, 18, tzinfo=UTC))
    assert any("would stop" in line for line in lines)
    assert any("--verbose" in line for line in lines)
    assert any("would append stdout and stderr to" in line for line in lines)
    assert diag.read_state() is None
    assert not (local_appdata / "UsageMonitorManager" / "logs").exists()


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #


def test_start_stops_first_then_starts_verbose_on_a_log_file(control: FakeControl) -> None:
    moment = datetime(2026, 9, 18, 9, 0, 0, tzinfo=UTC)
    result = diag.start_diagnostics(_instance(), now=moment)

    assert result.ok is True
    assert control.stops == ["claude"]
    assert len(control.starts) == 1
    instance_id, extra_args, log_path = control.starts[0]
    assert instance_id == "claude"
    assert extra_args == ("--verbose",)
    assert log_path == diag.log_path_for("claude", now=moment)
    assert log_path is not None
    assert log_path.parent == diag.instance_log_dir("claude")
    assert result.log_path == log_path
    assert "--verbose" in result.argv


def test_start_on_a_stopped_instance_does_not_stop_anything(control: FakeControl) -> None:
    result = diag.start_diagnostics(_instance(running=False))
    assert result.ok is True
    assert control.stops == []
    assert [args for _, args, _ in control.starts] == [("--verbose",)]


def test_start_records_the_session(control: FakeControl) -> None:
    moment = datetime(2026, 9, 18, 9, 0, 0, tzinfo=UTC)
    diag.start_diagnostics(_instance(), now=moment)

    session = diag.current_session()
    assert session is not None
    assert session.instance_id == "claude"
    assert session.log_path == diag.log_path_for("claude", now=moment)
    assert session.started_at == moment
    assert "--verbose" in session.argv
    # and it is a plain JSON file a restarted manager can read back
    assert diag.state_path().is_file()
    assert diag.read_state() == session


def test_a_failed_stop_launches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    control = FakeControl(stop_ok=False).install(monkeypatch)
    result = diag.start_diagnostics(_instance())

    assert result.ok is False
    assert "not started in diagnostic mode" in result.detail
    assert control.starts == []
    assert diag.read_state() is None, "a session must not be recorded when nothing was started"


def test_a_failed_start_records_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeControl(start_ok=False).install(monkeypatch)
    result = diag.start_diagnostics(_instance())
    assert result.ok is False
    assert diag.read_state() is None


# --------------------------------------------------------------------------- #
# stop
# --------------------------------------------------------------------------- #


def test_stop_restores_normal_mode(control: FakeControl) -> None:
    diag.start_diagnostics(_instance())
    control.starts.clear()
    control.stops.clear()

    result = diag.stop_diagnostics(_instance())

    assert result.ok is True
    assert result.detail == "back to normal mode"
    assert control.stops == ["claude"]
    assert [args for _, args, _ in control.starts] == [()], "restarted without --verbose"
    assert [path for _, _, path in control.starts] == [None], "and with its output no longer captured"
    assert diag.read_state() is None
    assert result.log_path is not None, "the result still points at the log that was written"


def test_stop_without_restart_only_stops(control: FakeControl) -> None:
    diag.start_diagnostics(_instance())
    control.starts.clear()
    result = diag.stop_diagnostics(_instance(), restart=False)
    assert result.ok is True
    assert control.starts == []
    assert diag.read_state() is None


def test_a_failed_stop_keeps_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = FakeControl().install(monkeypatch)
    diag.start_diagnostics(_instance())
    ok.stop_ok = False

    result = diag.stop_diagnostics(_instance())
    assert result.ok is False
    assert "still in diagnostic mode" in result.detail
    assert diag.read_state() is not None, "the instance is still verbose: keep saying so"


def test_clear_session_is_idempotent(control: FakeControl) -> None:
    diag.clear_session()
    diag.start_diagnostics(_instance())
    diag.clear_session()
    diag.clear_session()
    assert diag.read_state() is None


def test_a_corrupt_state_file_reads_as_no_session() -> None:
    path = diag.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert diag.read_state() is None
    path.write_text('{"instance_id": 7}', encoding="utf-8")
    assert diag.read_state() is None
    path.write_text('{"instance_id": "claude", "log_path": "x.log", "started_at": "nope"}', encoding="utf-8")
    session = diag.read_state()
    assert session is not None
    assert session.started_at is None


# --------------------------------------------------------------------------- #
# Reading logs
# --------------------------------------------------------------------------- #


def test_tail_returns_the_last_lines_and_survives_bad_bytes(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"\r\n".join(f"line {number}".encode() for number in range(1, 11)) + b"\r\nbad \xff\r\n")
    assert diag.tail(path, 3)[-2:] == ["bad \ufffd", ""]
    assert "line 10" in diag.tail(path, 3)[0]
    assert diag.tail(path, 0) == []
    assert diag.tail(tmp_path / "missing.log") == []


def test_list_logs_is_sorted_and_empty_when_absent() -> None:
    assert diag.list_logs("claude") == []
    directory = diag.instance_log_dir("claude")
    directory.mkdir(parents=True)
    for stamp in ("20260918-090000", "20260917-090000"):
        (directory / f"{stamp}.log").write_text("x", encoding="utf-8")
    assert [path.stem for path in diag.list_logs("claude")] == ["20260917-090000", "20260918-090000"]


def test_collect_report_carries_versions_and_no_secrets() -> None:
    payload = {"version": "0.8.0", "generated_at": "2026-09-18T09:00:00+00:00", "watchdog": {"count": 0}}
    report = diag.collect_report(
        payload,
        instance=_instance(),
        log_lines=["boot", "ready"],
        manager_version="0.8.0",
    )
    assert "Diagnostica Usage Monitor Manager" in report
    assert "- manager: 0.8.0" in report
    assert "Istanza claude" in report
    assert "UsageMonitorForClaude.exe" in report
    assert "boot" in report and "ready" in report
    assert "watchdog" not in report, "the watchdog findings are trimmed out of the paste block"
    assert "token" not in report.casefold()


# --------------------------------------------------------------------------- #
# The manager log
# --------------------------------------------------------------------------- #


def test_the_manager_log_is_a_rotating_file() -> None:
    logger = diag.setup_manager_logging()
    handlers = [handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert diag.LOG_MAX_BYTES == 1024 * 1024
    assert diag.LOG_BACKUP_COUNT == 3
    assert handler.maxBytes == diag.LOG_MAX_BYTES
    assert handler.backupCount == diag.LOG_BACKUP_COUNT
    assert Path(handler.baseFilename) == diag.manager_log_path()
    assert logger.propagate is False


def test_the_setup_runs_once_per_process() -> None:
    first = diag.setup_manager_logging()
    diag.setup_manager_logging()
    diag.setup_manager_logging()
    assert len([h for h in first.handlers if isinstance(h, RotatingFileHandler)]) == 1


def test_what_is_logged_ends_up_in_the_file() -> None:
    diag.setup_manager_logging()
    diag.get_logger("tray").info("hello %s", "world")
    for handler in logging.getLogger(diag.LOGGER_NAME).handlers:
        handler.flush()
    text = diag.manager_log_path().read_text(encoding="utf-8")
    assert "umm.tray" in text
    assert "hello world" in text
    assert any(line.endswith("hello world") for line in diag.tail_manager_log(5))


def test_a_directory_that_cannot_be_created_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", refuse)
    logger = diag.setup_manager_logging()
    assert [h for h in logger.handlers if isinstance(h, RotatingFileHandler)] == []
    logger.info("this must not raise")


def test_get_logger_namespaces_under_umm() -> None:
    assert diag.get_logger().name == "umm"
    assert diag.get_logger("scheduler").name == "umm.scheduler"
