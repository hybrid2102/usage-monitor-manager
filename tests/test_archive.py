"""Tests for the executable archive and the version rollback (PLAN.md section 9.4).

Everything happens inside ``tmp_path``: the "executables" are small text files
(the archive only ever renames them and hashes their bytes), and the only piece
that would touch a process - :func:`archive.restore` - gets its instances and
its ``process_control`` replaced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from usage_monitor_manager import archive, process_control
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair

EXE_NAME = "UsageMonitorForCopilot.exe"


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    path = tmp_path / EXE_NAME
    path.write_text("build 0.3.2", encoding="utf-8")
    return path


def _bak(exe_path: Path, content: str) -> Path:
    backup = exe_path.with_name(f"{exe_path.name}.bak")
    backup.write_text(content, encoding="utf-8")
    return backup


def _manifest(exe_path: Path) -> dict[str, object]:
    return json.loads((archive.archive_dir(exe_path) / archive.MANIFEST_NAME).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Archiving
# --------------------------------------------------------------------------- #


def test_archive_moves_the_backup_and_writes_the_manifest(exe: Path) -> None:
    _bak(exe, "build 0.3.1")
    item = archive.archive_exe(exe, "0.3.1.0", source_release_tag="v0.3.1")

    assert item is not None
    assert item.path.name == "UsageMonitorForCopilot-0.3.1.exe"
    assert item.path.read_text(encoding="utf-8") == "build 0.3.1"
    assert not exe.with_name(f"{exe.name}.bak").exists()  # moved, not copied
    assert item.version == "0.3.1"  # the PE padding is dropped
    assert item.sha256 and len(item.sha256) == 64
    assert item.source_release_tag == "v0.3.1"

    entries = _manifest(exe)["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["file"] == item.path.name
    assert entries[0]["archived_at"]


def test_nothing_to_archive_is_not_an_error(exe: Path) -> None:
    assert archive.archive_exe(exe, "0.3.1") is None
    assert not archive.archive_dir(exe).exists()


def test_record_current_supplies_the_tag_of_the_next_archived_build(exe: Path) -> None:
    """The tag travels: installing v0.3.2 is remembered, archiving it uses it."""
    archive.record_current(exe, version="0.3.2", tag="v0.3.2")
    assert archive.current_release_tag(exe) == "v0.3.2"
    _bak(exe, "build 0.3.2")
    item = archive.archive_exe(exe, "0.3.2")
    assert item is not None
    assert item.source_release_tag == "v0.3.2"


def test_keep_n_evicts_the_oldest(exe: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stamps = iter(datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=index) for index in range(10))

    class Clock(datetime):
        """A ``datetime`` whose ``now`` walks one day forward per call."""

        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return next(stamps)

    monkeypatch.setattr(archive, "datetime", Clock)
    for version in ("0.1.0", "0.2.0", "0.3.0", "0.4.0"):
        _bak(exe, f"build {version}")
        archive.archive_exe(exe, version, keep=3)

    kept = archive.list_archived(exe)
    assert [item.version for item in kept] == ["0.4.0", "0.3.0", "0.2.0"]  # newest first
    assert not (archive.archive_dir(exe) / "UsageMonitorForCopilot-0.1.0.exe").exists()
    assert len(_manifest(exe)["entries"]) == 3  # the manifest lost the evicted entry too


def test_keep_is_per_executable(tmp_path: Path) -> None:
    """A shared Tools\\AI directory must not let one kind evict another."""
    copilot = tmp_path / "UsageMonitorForCopilot.exe"
    codex = tmp_path / "UsageMonitorForCodex.exe"
    for exe_path, versions in ((copilot, ("0.3.0", "0.3.1")), (codex, ("0.1.0", "0.1.1"))):
        exe_path.write_text("current", encoding="utf-8")
        for version in versions:
            _bak(exe_path, f"{exe_path.stem} {version}")
            archive.archive_exe(exe_path, version, keep=2)

    assert [item.version for item in archive.list_archived(copilot)] == ["0.3.1", "0.3.0"]
    assert [item.version for item in archive.list_archived(codex)] == ["0.1.1", "0.1.0"]


def test_prune_drops_entries_whose_file_disappeared(exe: Path) -> None:
    for version in ("0.1.0", "0.2.0"):
        _bak(exe, f"build {version}")
        archive.archive_exe(exe, version)
    (archive.archive_dir(exe) / "UsageMonitorForCopilot-0.1.0.exe").unlink()

    assert [item.version for item in archive.list_archived(exe)] == ["0.2.0"]
    archive.prune(exe, keep=5)
    assert len(_manifest(exe)["entries"]) == 1


def test_find_picks_the_newest_or_the_named_version(exe: Path) -> None:
    for version in ("0.1.0", "0.2.0"):
        _bak(exe, f"build {version}")
        archive.archive_exe(exe, version)
    newest = archive.find(exe)
    assert newest is not None and newest.version == "0.2.0"
    named = archive.find(exe, "0.1.0")
    assert named is not None and named.version == "0.1.0"
    assert archive.find(exe, "9.9.9") is None


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #


def _instance(exe_path: Path, *, running: bool) -> Instance:
    return Instance(
        kind=get_kind("copilot"),
        config_dir=Path.home() / ".copilot",
        instance_suffix="",
        exe_path=exe_path,
        processes=ProcessPair(bootloader_pid=100, app_pid=101) if running else None,
    )


def _stop_result(instance: Instance, *, ok: bool, detail: str) -> process_control.StopResult:
    return process_control.StopResult(
        instance_id=instance.instance_id,
        kind=instance.kind.key,
        config_dir=instance.config_dir,
        ok=ok,
        detail=detail,
    )


@pytest.fixture
def fake_process_control(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def stop(instance: Instance, **kwargs: object) -> process_control.StopResult:
        calls.append(f"stop:{instance.instance_id}")
        return _stop_result(instance, ok=True, detail="stopped")

    def start(instance: Instance, **kwargs: object) -> process_control.StartResult:
        calls.append(f"start:{instance.instance_id}")
        return process_control.StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=True,
            detail="started",
        )

    monkeypatch.setattr(process_control, "stop", stop)
    monkeypatch.setattr(process_control, "start", start)
    monkeypatch.setattr(archive, "_sleep", lambda seconds: None)
    return calls


def test_restore_swaps_and_archives_the_current_build(exe: Path, fake_process_control: list[str]) -> None:
    archive.record_current(exe, version="0.3.2", tag="v0.3.2")  # what updater.apply writes
    _bak(exe, "build 0.3.1")
    archive.archive_exe(exe, "0.3.1")

    result = archive.restore(exe, "0.3.1", instances=[_instance(exe, running=True)])

    assert result.ok, result.detail
    assert exe.read_text(encoding="utf-8") == "build 0.3.1"  # the archived build is in place
    assert fake_process_control == ["stop:copilot", "start:copilot"]  # stop before, start after
    # the build that was in place is now itself archivable again
    assert [item.version for item in archive.list_archived(exe)] == ["0.3.2"]
    assert (archive.archive_dir(exe) / "UsageMonitorForCopilot-0.3.2.exe").read_text(encoding="utf-8") == "build 0.3.2"
    assert not (archive.archive_dir(exe) / "UsageMonitorForCopilot-0.3.1.exe").exists()  # promoted, not duplicated


def test_restore_without_a_version_takes_the_newest(exe: Path, fake_process_control: list[str]) -> None:
    for version, body in (("0.3.0", "build 0.3.0"), ("0.3.1", "build 0.3.1")):
        _bak(exe, body)
        archive.archive_exe(exe, version)
    result = archive.restore(exe, instances=[_instance(exe, running=False)])
    assert result.ok
    assert exe.read_text(encoding="utf-8") == "build 0.3.1"
    assert fake_process_control == []  # nothing was running, nothing was touched


def test_restore_without_an_archive_fails_cleanly(exe: Path, fake_process_control: list[str]) -> None:
    result = archive.restore(exe, "1.0.0", instances=[_instance(exe, running=True)])
    assert result.ok is False
    assert "no archived build" in result.detail
    assert exe.read_text(encoding="utf-8") == "build 0.3.2"  # untouched
    assert fake_process_control == []  # and nothing was stopped


def test_restore_aborts_when_an_instance_will_not_stop(
    exe: Path, monkeypatch: pytest.MonkeyPatch, fake_process_control: list[str]
) -> None:
    _bak(exe, "build 0.3.1")
    archive.archive_exe(exe, "0.3.1")
    monkeypatch.setattr(
        process_control,
        "stop",
        lambda instance, **kwargs: _stop_result(instance, ok=False, detail="still running"),
    )
    result = archive.restore(exe, "0.3.1", instances=[_instance(exe, running=True)])
    assert result.ok is False
    assert "could not stop" in result.detail
    assert exe.read_text(encoding="utf-8") == "build 0.3.2"  # the executable was not touched


def test_no_restart_leaves_the_instances_stopped(exe: Path, fake_process_control: list[str]) -> None:
    _bak(exe, "build 0.3.1")
    archive.archive_exe(exe, "0.3.1")
    result = archive.restore(exe, "0.3.1", instances=[_instance(exe, running=True)], restart=False)
    assert result.ok
    assert fake_process_control == ["stop:copilot"]
