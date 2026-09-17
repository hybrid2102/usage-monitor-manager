"""End-to-end tests against a real process holding a real named mutex.

Design note (why a frozen dummy and not a test hook in production code)
-----------------------------------------------------------------------
``stop`` and ``start`` are only meaningful against something that behaves like
a monitor: an executable that takes ``--config-dir``, owns a single-instance
mutex, and shows up in the process table as a PyInstaller bootloader/app pair.

Running the dummy as ``python.exe tests/dummy_monitor.py`` cannot work:
``start`` builds its command line as ``[exe, --config-dir=...]`` by design, and
an interpreter given only that flag exits. Teaching ``discovery`` a test-only
"exe matcher" would have meant carrying a seam in production code purely for
the suite. So instead the dummy is frozen with PyInstaller *inside the test*
(one ``--onefile`` build per session, skipped when PyInstaller is missing) and
registered as a temporary kind by monkeypatching ``kinds.KINDS``. Production
code learns nothing about the tests, and the exercise is the real one: real
CreateProcess, real bootloader/app pair, real mutex, real termination.

The temporary registry also isolates the suite: while these tests run, the kind
registry contains *only* the dummy, so discovery cannot see - let alone act on
- the developer's own monitor instances.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from usage_monitor_manager import discovery, kinds, platform, process_control
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.model import Instance, MonitorKind, ProcessPair

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the process layer is Windows-only in v1")

BUILD_TIMEOUT = 600
STARTUP_TIMEOUT = 30.0
"""A one-file bootloader unpacks itself before the app runs; be generous."""


# --------------------------------------------------------------------------- #
# The frozen dummy monitor
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def dummy_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Freeze ``dummy_monitor.py`` into a one-file executable, once per session."""
    pytest.importorskip("PyInstaller", reason="the integration tests need PyInstaller to build the dummy monitor")
    root = tmp_path_factory.mktemp("dummy-monitor-build")
    script = Path(__file__).with_name("dummy_monitor.py")
    completed = subprocess.run(  # noqa: S603 - fixed argv built from sys.executable
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconsole",
            "--noconfirm",
            "--clean",
            "--name",
            "UmmDummyMonitor",
            "--distpath",
            str(root / "dist"),
            "--workpath",
            str(root / "build"),
            "--specpath",
            str(root / "spec"),
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )
    exe = root / "dist" / "UmmDummyMonitor.exe"
    if completed.returncode != 0 or not exe.is_file():
        tail = (completed.stdout or "")[-1500:] + (completed.stderr or "")[-1500:]
        pytest.skip(f"could not build the dummy monitor with PyInstaller:\n{tail}")
    return exe


@pytest.fixture
def dummy_kind(
    dummy_exe: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MonitorKind]:
    """Register the dummy as the only kind in the registry for one test."""
    default_dir = tmp_path / "default-home"
    default_dir.mkdir(parents=True, exist_ok=True)
    base = f"UmmDummy{os.getpid()}_SingleInstance"

    monkeypatch.setenv("UMM_DUMMY_MUTEX_BASE", base)
    monkeypatch.setenv("UMM_DUMMY_DEFAULT_DIR", str(default_dir))
    monkeypatch.delenv("UMM_DUMMY_CONFIG_DIR", raising=False)

    kind = MonitorKind(
        key="dummy",
        display_name="Dummy",
        exe_name=dummy_exe.name,
        pe_product_name="Usage Monitor for Dummy",
        env_var="UMM_DUMMY_CONFIG_DIR",
        default_config_dir_factory=lambda: default_dir,
        repo="example/dummy",
        asset_pattern=dummy_exe.name,
        sha_sidecar=False,
        settings_schema_id="dummy",
        mutex_base=base,
        run_key_base="UmmDummyMonitor",
    )
    monkeypatch.setattr(kinds, "KINDS", {"dummy": kind})
    yield kind
    _kill_leftovers(dummy_exe)


def _kill_leftovers(exe: Path) -> None:
    """Never leave a dummy behind, whatever the test did or failed to do."""
    wanted = os.path.normcase(str(exe))
    for proc in psutil.process_iter(["exe"]):
        try:
            found = proc.info["exe"]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(found, str) and os.path.normcase(found) == wanted:
                proc.kill()
        except (psutil.Error, OSError):
            continue


def _instance(
    kind: MonitorKind,
    exe: Path,
    config_dir: Path,
    processes: ProcessPair | None = None,
) -> Instance:
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=config_dir_suffix(config_dir, kind.default_config_dir),
        exe_path=exe,
        processes=processes,
    )


def _wait_for_mutex(name: str, held: bool, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if platform.mutex_is_held(name) is held:
            return True
        time.sleep(0.2)
    return platform.mutex_is_held(name) is held


def _launch(exe: Path, config_dir: Path) -> int:
    """Start a dummy the way a user would, independently of ``start()``."""
    kwargs = platform.no_window_popen_kwargs()
    creationflags = int(kwargs.pop("creationflags", 0)) | platform.DETACHED_PROCESS
    process = subprocess.Popen(  # noqa: S603 - fixed argv, paths under tmp_path
        [str(exe), f"--config-dir={config_dir}"],
        cwd=str(exe.parent),
        env=dict(os.environ),
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    return process.pid


def _discover_one(kind: MonitorKind, config_dir: Path) -> Instance:
    """Wait for the instance to be fully up (mutex taken), then discover it."""
    suffix = config_dir_suffix(config_dir, kind.default_config_dir)
    assert _wait_for_mutex(f"{kind.mutex_base}{suffix}", held=True), "the dummy never took its mutex"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    wanted = os.path.normcase(str(config_dir))
    while time.monotonic() < deadline:
        for found in discovery.discover(include_dormant=False):
            if found.kind.key == kind.key and os.path.normcase(str(found.config_dir)) == wanted and found.is_running:
                return found
        time.sleep(0.3)
    raise AssertionError(f"no running dummy instance for {config_dir}")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_dummy_monitor_is_discovered_and_holds_its_mutex(
    dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "instance-a"
    config_dir.mkdir()
    name = f"{dummy_kind.mutex_base}{config_dir_suffix(config_dir, dummy_kind.default_config_dir)}"
    assert platform.mutex_is_held(name) is False

    _launch(dummy_exe, config_dir)
    assert _wait_for_mutex(name, held=True), "the dummy never took its mutex"

    found = _discover_one(dummy_kind, config_dir)
    assert found.config_dir_source == "cmdline"
    assert (config_dir / "dummy-monitor.pid").is_file()
    assert found.processes is not None
    assert found.processes.app_pid > 0


def test_stop_terminates_the_instance_and_frees_the_mutex(
    dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path
) -> None:
    config_dir = tmp_path / "instance-a"
    config_dir.mkdir()
    _launch(dummy_exe, config_dir)
    running = _discover_one(dummy_kind, config_dir)
    assert running.processes is not None
    pids = [pid for pid in (running.processes.app_pid, running.processes.bootloader_pid) if pid]

    events: list[process_control.ProcessEvent] = []
    result = process_control.stop(running, timeout=STARTUP_TIMEOUT, on_event=events.append)

    assert result.ok is True, result.detail
    assert result.mutex_released is True
    assert result.terminated_pids, "the app pid must have been signalled"
    assert not any(psutil.pid_exists(pid) and _still_ours(pid, dummy_exe) for pid in pids)
    assert platform.mutex_is_held(result.mutex_name) is False
    assert [event.phase for event in events][-1] == "stop_end"


def _still_ours(pid: int, exe: Path) -> bool:
    try:
        return os.path.normcase(psutil.Process(pid).exe()) == os.path.normcase(str(exe))
    except (psutil.Error, OSError):
        return False


def test_stop_leaves_a_foreign_process_alone(dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path) -> None:
    """A stale PID that now belongs to something else must not be killed."""
    config_dir = tmp_path / "instance-a"
    config_dir.mkdir()
    victim = subprocess.Popen(  # noqa: S603 - our own interpreter, no shell
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        stale = _instance(
            dummy_kind,
            dummy_exe,
            config_dir,
            processes=ProcessPair(bootloader_pid=None, app_pid=victim.pid),
        )
        result = process_control.stop(stale, timeout=5.0)
        assert result.skipped_pids == (victim.pid,)
        assert victim.poll() is None, "the foreign process was killed"
    finally:
        victim.kill()
        victim.wait(timeout=10)


def test_start_launches_a_discoverable_instance(dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path) -> None:
    config_dir = tmp_path / "instance-b"
    config_dir.mkdir()
    dormant = _instance(dummy_kind, dummy_exe, config_dir)

    result = process_control.start(dormant, wait_seconds=8.0)

    assert result.ok is True, result.detail
    assert result.argv == (str(dummy_exe), f"--config-dir={config_dir}")
    assert result.env_overrides == {"UMM_DUMMY_CONFIG_DIR": str(config_dir)}
    assert result.app_pid is not None
    assert platform.mutex_is_held(process_control.mutex_name(dormant)) is True
    marker = config_dir / "dummy-monitor.pid"
    assert marker.is_file(), "the instance did not use the config dir we asked for"
    assert str(result.app_pid) in marker.read_text(encoding="utf-8")


def test_start_refuses_to_launch_a_second_copy(dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path) -> None:
    config_dir = tmp_path / "instance-b"
    config_dir.mkdir()
    _launch(dummy_exe, config_dir)
    dormant = _instance(dummy_kind, dummy_exe, config_dir)
    assert _wait_for_mutex(process_control.mutex_name(dormant), held=True)

    result = process_control.start(dormant, wait_seconds=1.0)

    assert result.ok is False
    assert result.already_running is True
    assert result.spawned_pid is None, "nothing may be launched while the mutex is held"


def test_restart_end_to_end(dummy_kind: MonitorKind, dummy_exe: Path, tmp_path: Path) -> None:
    config_dir = tmp_path / "instance-c"
    config_dir.mkdir()
    _launch(dummy_exe, config_dir)
    running = _discover_one(dummy_kind, config_dir)
    assert running.processes is not None
    before = running.processes.app_pid

    outcome = process_control.restart([running], gap_seconds=0.5, stop_timeout=STARTUP_TIMEOUT, start_wait=8.0)

    assert outcome.ok is True, (outcome.stops[0].detail, outcome.starts[0].detail)
    assert outcome.stops[0].mutex_released is True
    after = outcome.starts[0].app_pid
    assert after is not None and after != before, "restart must produce a new process"
    assert platform.mutex_is_held(process_control.mutex_name(running)) is True
