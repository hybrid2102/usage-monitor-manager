"""Tests for the update engine.

Safety rules for this file, in the same spirit as ``test_process_control.py``:

* ``process_control.stop`` / ``start`` are always replaced by a recorder, so no
  PID of a real monitor is ever signalled and no executable is ever launched;
* ``github.download`` / ``fetch_text`` / ``fetch_latest_release`` are always
  replaced, so nothing reaches the network;
* ``versions.exe_version`` is replaced by a reader of a ``VERSION=`` marker
  inside the fake executable, which makes the swap observable: the file really
  changes version when the update succeeds;
* every path lives under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import github, process_control, updater, versions
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, MonitorKind, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan
from usage_monitor_manager.updater import FileSwapper, SwapError, UpdateEvent
from usage_monitor_manager.versions import VersionStatus

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def fake_exe_version(path: Path | str | None) -> str | None:
    """Read a ``VERSION=x.y.z`` marker: our stand-in for a PE ProductVersion."""
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"VERSION=([0-9.]+)", text)
    return f"{match.group(1)}.0" if match else None


class ProcessRecorder:
    """Stands in for ``process_control``: records, never touches a process."""

    def __init__(self, *, stop_ok: bool = True, start_ok: bool = True) -> None:
        self.calls: list[str] = []
        self.stop_ok = stop_ok
        self.start_ok = start_ok

    def stop(self, instance: Instance, *args: object, **kwargs: object) -> process_control.StopResult:
        self.calls.append(f"stop:{instance.instance_id}")
        return process_control.StopResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.stop_ok,
            detail="stopped" if self.stop_ok else "still holding the mutex",
        )

    def start(self, instance: Instance, *args: object, **kwargs: object) -> process_control.StartResult:
        self.calls.append(f"start:{instance.instance_id}")
        return process_control.StartResult(
            instance_id=instance.instance_id,
            kind=instance.kind.key,
            config_dir=instance.config_dir,
            ok=self.start_ok,
            detail="started" if self.start_ok else "the mutex is still held",
        )


def payload_for(version: str) -> bytes:
    return f"MZ fake executable VERSION={version} padding".encode()


def release(
    tag: str,
    *,
    asset_name: str = "UsageMonitorForCopilot.exe",
    size: int = 0,
    digest: str | None = None,
    sidecar: str | None = None,
    body: str = "### Fixed\n\n- a thing",
) -> ReleaseInfo:
    return ReleaseInfo(
        tag=tag,
        version=versions.format_version(tag),
        published_at=datetime(2026, 9, 17, 10, 15, tzinfo=UTC),
        html_url=f"https://github.com/hybrid2102/usage-monitor-for-copilot/releases/tag/{tag}",
        body_markdown=body,
        asset=ReleaseAsset(name=asset_name, url=f"https://e/{asset_name}", size=size, digest=digest),
        sidecar_sha_url=sidecar,
    )


def instance(
    kind: MonitorKind,
    config_dir: Path,
    exe_path: Path,
    *,
    suffix: str = "",
    running: bool = True,
    pid: int = 4242,
) -> Instance:
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=suffix,
        exe_path=exe_path,
        exe_version=fake_exe_version(exe_path),
        processes=ProcessPair(bootloader_pid=pid, app_pid=pid + 1) if running else None,
    )


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in these tests may actually sleep."""
    monkeypatch.setattr(updater, "_sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def fake_pe_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "exe_version", fake_exe_version)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> ProcessRecorder:
    rec = ProcessRecorder()
    monkeypatch.setattr(process_control, "stop", rec.stop)
    monkeypatch.setattr(process_control, "start", rec.start)
    return rec


def install_download(monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, payload: bytes) -> None:
    def download(
        asset: ReleaseAsset,
        dest_path: Path,
        on_progress: github.ProgressCallback | None = None,
        **kwargs: object,
    ) -> Path:
        recorder.calls.append(f"download:{dest_path.name}")
        dest_path.write_bytes(payload)
        if on_progress is not None:
            on_progress(0, len(payload))
            on_progress(len(payload), len(payload))
        return dest_path

    monkeypatch.setattr(github, "download", download)


def install_discovery(monkeypatch: pytest.MonkeyPatch, instances: Sequence[Instance]) -> None:
    from usage_monitor_manager import discovery

    monkeypatch.setattr(discovery, "discover", lambda include_dormant=True: list(instances))


# --------------------------------------------------------------------------- #
# plan_updates
# --------------------------------------------------------------------------- #


def test_two_instances_sharing_an_exe_produce_one_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    claude_exe = tmp_path / "Claude" / "UsageMonitorForClaude.exe"
    copilot_exe = tmp_path / "Copilot" / "UsageMonitorForCopilot.exe"
    for exe, version in ((claude_exe, "1.22.0"), (copilot_exe, "0.3.1")):
        exe.parent.mkdir(parents=True)
        exe.write_bytes(payload_for(version))

    instances = [
        instance(get_kind("claude"), tmp_path / ".claude", claude_exe),
        instance(get_kind("claude"), tmp_path / ".claude-valeria", claude_exe, suffix="_578560bbc610"),
        instance(get_kind("copilot"), tmp_path / ".copilot", copilot_exe),
    ]

    fetched: list[str] = []

    def fetch(repo: str, **kwargs: object) -> ReleaseInfo:
        fetched.append(repo)
        return release("v1.22.0" if "claude" in repo else "v0.3.2")

    monkeypatch.setattr(github, "fetch_latest_release", fetch)

    plans = updater.plan_updates(instances)

    assert [len(plan.affected_instances) for plan in plans] == [2, 1]
    assert plans[0].exe_path == claude_exe
    assert [i.instance_id for i in plans[0].affected_instances] == ["claude", "claude_578560bbc610"]
    assert plans[0].current_version == "1.22.0.0"
    assert updater.plan_status(plans[0]) is VersionStatus.UP_TO_DATE
    assert updater.plan_status(plans[1]) is VersionStatus.UPDATE_AVAILABLE
    assert fetched == ["jens-duttke/usage-monitor-for-claude", "hybrid2102/usage-monitor-for-copilot"]


def test_plan_updates_queries_each_repo_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exes = []
    for index in range(2):  # the same kind installed twice, in two folders
        exe = tmp_path / f"copy{index}" / "UsageMonitorForCopilot.exe"
        exe.parent.mkdir()
        exe.write_bytes(payload_for("0.3.1"))
        exes.append(exe)
    instances = [instance(get_kind("copilot"), tmp_path / f".copilot{i}", exe) for i, exe in enumerate(exes)]

    calls: list[str] = []
    monkeypatch.setattr(
        github, "fetch_latest_release", lambda repo, **kwargs: (calls.append(repo), release("v0.3.2"))[1]
    )

    plans = updater.plan_updates(instances)
    assert len(plans) == 2
    assert calls == ["hybrid2102/usage-monitor-for-copilot"]


def test_plan_updates_filters_by_kind_and_skips_unknown_exes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    copilot_exe = tmp_path / "UsageMonitorForCopilot.exe"
    copilot_exe.write_bytes(payload_for("0.3.1"))
    instances = [
        instance(get_kind("copilot"), tmp_path / ".copilot", copilot_exe),
        Instance(kind=get_kind("codex"), config_dir=tmp_path / ".codex", instance_suffix=""),  # no exe_path
    ]
    monkeypatch.setattr(github, "fetch_latest_release", lambda repo, **kwargs: release("v0.3.2"))

    assert [p.kind.key for p in updater.plan_updates(instances)] == ["copilot"]
    assert updater.plan_updates(instances, kinds=["codex"]) == []


def test_plan_updates_lets_a_github_error_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "UsageMonitorForCopilot.exe"
    exe.write_bytes(payload_for("0.3.1"))

    def boom(repo: str, **kwargs: object) -> ReleaseInfo:
        raise github.GitHubError("cannot reach GitHub")

    monkeypatch.setattr(github, "fetch_latest_release", boom)
    with pytest.raises(github.GitHubError):
        updater.plan_updates([instance(get_kind("copilot"), tmp_path / ".copilot", exe)])


# --------------------------------------------------------------------------- #
# FileSwapper
# --------------------------------------------------------------------------- #


def test_swapper_retries_until_the_lock_goes_away(tmp_path: Path) -> None:
    target = tmp_path / "UsageMonitorForCopilot.exe"
    target.write_bytes(payload_for("0.3.1"))
    handle = target.open("rb")  # a real Windows lock: renaming fails while it is open
    attempts: list[float] = []

    def sleep(seconds: float) -> None:
        attempts.append(seconds)
        if len(attempts) == 2:  # the antivirus lets go after a couple of seconds
            handle.close()

    messages: list[str] = []
    swapper = FileSwapper(attempts=5, delay=2.0, sleep=sleep, on_retry=messages.append)
    try:
        swapper.move(target, tmp_path / "UsageMonitorForCopilot.exe.bak")
    finally:
        handle.close()

    assert (tmp_path / "UsageMonitorForCopilot.exe.bak").exists()
    if os.name == "nt":
        assert attempts == [2.0, 2.0]
        assert "retry 2/5" in messages[0]


def test_swapper_gives_up_with_a_swap_error(tmp_path: Path) -> None:
    def always_fails(src: Path, dst: Path) -> None:
        raise PermissionError(13, "The process cannot access the file")

    swapper = FileSwapper(attempts=3, delay=0.0, replace=always_fails, sleep=lambda s: None)
    with pytest.raises(SwapError, match="after 3 attempts"):
        swapper.move(tmp_path / "a", tmp_path / "b")


def test_swapper_backup_replaces_a_stale_bak(tmp_path: Path) -> None:
    exe = tmp_path / "m.exe"
    exe.write_bytes(payload_for("0.3.2"))
    stale = tmp_path / "m.exe.bak"
    stale.write_bytes(b"ancient")

    backup = FileSwapper(sleep=lambda s: None).backup(exe)

    assert backup == stale
    assert backup.read_bytes() == payload_for("0.3.2")
    assert not exe.exists()


def test_swapper_discard_never_raises(tmp_path: Path) -> None:
    assert FileSwapper(sleep=lambda s: None).discard(tmp_path / "never-existed") is True


# --------------------------------------------------------------------------- #
# apply: the happy path
# --------------------------------------------------------------------------- #


@pytest.fixture
def scenario(tmp_path: Path) -> tuple[Path, list[Instance]]:
    """A Copilot 0.3.1 executable with one running instance."""
    exe = tmp_path / "Copilot" / "UsageMonitorForCopilot.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(payload_for("0.3.1"))
    return exe, [instance(get_kind("copilot"), tmp_path / ".copilot", exe)]


def plan_for(exe: Path, instances: Sequence[Instance], target: ReleaseInfo) -> UpdatePlan:
    return UpdatePlan(
        exe_path=exe,
        kind=get_kind("copilot"),
        current_version=fake_exe_version(exe),
        target=target,
        affected_instances=tuple(instances),
    )


def test_apply_runs_the_steps_in_the_documented_order(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest=f"sha256:{hashlib.sha256(payload).hexdigest()}"))

    events: list[UpdateEvent] = []
    result = updater.apply(plan, on_event=events.append, post_check_delay=0.0)

    assert result.ok, result.detail
    assert recorder.calls == [
        "download:UsageMonitorForCopilot.exe.download",
        "stop:copilot",
        "start:copilot",
    ]
    steps = [event.step for event in events]
    assert steps.index("download") < steps.index("verify") < steps.index("stop")
    assert steps.index("stop") < steps.index("backup") < steps.index("swap") < steps.index("start")
    assert steps.index("start") < steps.index("post_check") < steps.index("cleanup")

    assert exe.read_bytes() == payload  # the swap really happened
    assert not (exe.parent / "UsageMonitorForCopilot.exe.bak").exists()  # deleted after the check
    assert not (exe.parent / "UsageMonitorForCopilot.exe.download").exists()
    assert result.integrity is not None
    assert (result.integrity.source, result.integrity.hash_verified) == ("digest", True)
    assert result.restarted == ("copilot",)
    assert result.stopped == ("copilot",)
    assert result.rollback_available is False
    assert result.to_dict()["to_version"] == "0.3.2"
    assert any(event.progress == 1.0 for event in events)


def test_apply_verifies_against_the_sidecar_when_there_is_no_api_digest(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(github, "fetch_text", lambda url, **kwargs: f"{digest}  UsageMonitorForCopilot.exe\n")
    plan = plan_for(exe, instances, release("v0.3.2", sidecar="https://e/m.exe.sha256"))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok, result.detail
    assert result.integrity is not None and result.integrity.source == "sidecar"


def test_apply_without_restart_leaves_the_instances_stopped(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest()))

    result = updater.apply(plan, restart=False, post_check_delay=0.0)

    assert result.ok, result.detail
    assert result.restarted == ()
    assert "start:copilot" not in recorder.calls


def test_apply_updates_both_instances_of_a_shared_exe(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, tmp_path: Path
) -> None:
    exe = tmp_path / "Claude" / "UsageMonitorForClaude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(payload_for("1.21.0"))
    instances = [
        instance(get_kind("claude"), tmp_path / ".claude", exe, pid=100),
        instance(get_kind("claude"), tmp_path / ".claude-valeria", exe, suffix="_578560bbc610", pid=200),
    ]
    payload = payload_for("1.22.0")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = UpdatePlan(
        exe_path=exe,
        kind=get_kind("claude"),
        current_version="1.21.0.0",
        target=release("v1.22.0", asset_name="UsageMonitorForClaude.exe", digest=hashlib.sha256(payload).hexdigest()),
        affected_instances=tuple(instances),
    )

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok, result.detail
    assert recorder.calls == [
        "download:UsageMonitorForClaude.exe.download",
        "stop:claude",
        "stop:claude_578560bbc610",
        "start:claude",
        "start:claude_578560bbc610",
    ]
    assert result.restarted == ("claude", "claude_578560bbc610")


def test_apply_leaves_a_dormant_instance_alone(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, tmp_path: Path
) -> None:
    exe = tmp_path / "UsageMonitorForCodex.exe"
    exe.write_bytes(payload_for("0.0.9"))
    instances = [instance(get_kind("codex"), tmp_path / ".codex", exe, running=False)]
    payload = payload_for("0.1.0")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = UpdatePlan(
        exe_path=exe,
        kind=get_kind("codex"),
        current_version="0.0.9.0",
        target=release("v0.1.0", asset_name="UsageMonitorForCodex.exe", digest=hashlib.sha256(payload).hexdigest()),
        affected_instances=tuple(instances),
    )

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok, result.detail
    assert recorder.calls == ["download:UsageMonitorForCodex.exe.download"]  # nothing stopped, nothing started


# --------------------------------------------------------------------------- #
# apply: refusals and failures
# --------------------------------------------------------------------------- #


def test_an_unverifiable_download_is_refused_by_default(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    install_download(monkeypatch, recorder, payload_for("0.3.2"))
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2"))  # no digest, no sidecar

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert "no sha256" in result.detail
    assert recorder.calls == ["download:UsageMonitorForCopilot.exe.download"]  # never stopped anything
    assert exe.read_bytes() == payload_for("0.3.1")  # untouched
    assert not (exe.parent / "UsageMonitorForCopilot.exe.download").exists()  # deleted
    assert result.integrity is not None and result.integrity.unverifiable


def test_an_unverifiable_download_can_be_confirmed(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    install_download(monkeypatch, recorder, payload_for("0.3.2"))
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2"))

    result = updater.apply(plan, confirm_unverified=lambda: True, post_check_delay=0.0)

    assert result.ok, result.detail
    assert exe.read_bytes() == payload_for("0.3.2")


def test_a_broken_sidecar_makes_the_download_unverifiable(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    install_download(monkeypatch, recorder, payload_for("0.3.2"))
    install_discovery(monkeypatch, instances)
    monkeypatch.setattr(github, "fetch_text", lambda url, **kwargs: "404: not found\n")
    plan = plan_for(exe, instances, release("v0.3.2", sidecar="https://e/m.exe.sha256"))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert exe.read_bytes() == payload_for("0.3.1")


def test_a_digest_mismatch_aborts_and_deletes_the_download(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    install_download(monkeypatch, recorder, payload_for("0.3.2"))
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest="sha256:" + "00" * 32))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert "mismatch" in result.detail
    assert recorder.calls == ["download:UsageMonitorForCopilot.exe.download"]
    assert exe.read_bytes() == payload_for("0.3.1")
    assert not (exe.parent / "UsageMonitorForCopilot.exe.download").exists()


def test_a_download_failure_is_reported_without_touching_anything(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario

    def failing_download(*args: object, **kwargs: object) -> Path:
        raise github.GitHubError("GitHub answered 502 Bad Gateway")

    monkeypatch.setattr(github, "download", failing_download)
    install_discovery(monkeypatch, instances)

    result = updater.apply(plan_for(exe, instances, release("v0.3.2")), post_check_delay=0.0)

    assert result.ok is False
    assert "502" in result.detail
    assert recorder.calls == []
    assert exe.read_bytes() == payload_for("0.3.1")


def test_a_release_without_an_asset_is_refused(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    target = ReleaseInfo(tag="v0.3.2", version="0.3.2", published_at=None, html_url="", body_markdown="", asset=None)
    result = updater.apply(plan_for(exe, instances, target), post_check_delay=0.0)
    assert result.ok is False
    assert "no .exe asset" in result.detail
    assert recorder.calls == []


def test_a_failed_stop_aborts_before_the_swap(
    monkeypatch: pytest.MonkeyPatch, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    rec = ProcessRecorder(stop_ok=False)
    monkeypatch.setattr(process_control, "stop", rec.stop)
    monkeypatch.setattr(process_control, "start", rec.start)
    payload = payload_for("0.3.2")
    install_download(monkeypatch, rec, payload)
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest()))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert "could not stop" in result.detail
    assert exe.read_bytes() == payload_for("0.3.1")
    assert not (exe.parent / "UsageMonitorForCopilot.exe.bak").exists()


def test_a_failed_swap_restores_the_previous_executable(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest()))

    real_replace = os.replace

    def replace(src: Path, dst: Path) -> None:
        if src.name.endswith(".download"):  # the promote step, and only that one
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    swapper = FileSwapper(attempts=2, delay=0.0, replace=replace, sleep=lambda s: None)
    result = updater.apply(plan, swapper=swapper, post_check_delay=0.0)

    assert result.ok is False
    assert "restored" in result.detail
    assert exe.read_bytes() == payload_for("0.3.1")  # the old executable is back
    assert not (exe.parent / "UsageMonitorForCopilot.exe.bak").exists()
    assert result.restarted == ("copilot",)  # and the instance runs again


def test_a_failed_post_check_keeps_the_backup(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.1")  # the "new" file reports the OLD version: the check must fail
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    plan = plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest()))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert "post-update check failed" in result.detail
    assert result.rollback_available is True
    backup = exe.parent / "UsageMonitorForCopilot.exe.bak"
    assert result.backup_path == backup
    assert backup.exists()


def test_a_restarted_instance_that_did_not_come_back_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, [instance(get_kind("copilot"), instances[0].config_dir, exe, running=False)])
    plan = plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest()))

    result = updater.apply(plan, post_check_delay=0.0)

    assert result.ok is False
    assert "not running" in result.detail
    assert result.rollback_available is True


# --------------------------------------------------------------------------- #
# rollback
# --------------------------------------------------------------------------- #


def test_rollback_restores_the_backup_and_restarts(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    exe.write_bytes(payload_for("0.3.2"))  # the new one is in place
    backup = exe.parent / "UsageMonitorForCopilot.exe.bak"
    backup.write_bytes(payload_for("0.3.1"))
    install_discovery(monkeypatch, instances)

    result = updater.rollback(exe, instances=instances)

    assert result.ok, result.detail
    assert exe.read_bytes() == payload_for("0.3.1")
    assert not backup.exists()
    assert recorder.calls == ["stop:copilot", "start:copilot"]
    assert result.from_version == "0.3.1"


def test_rollback_accepts_the_plan_it_came_from(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    (exe.parent / "UsageMonitorForCopilot.exe.bak").write_bytes(payload_for("0.3.1"))
    exe.write_bytes(payload_for("0.3.2"))
    install_discovery(monkeypatch, instances)

    result = updater.rollback(plan_for(exe, instances, release("v0.3.2")))

    assert result.ok, result.detail
    assert result.kind == "copilot"


def test_rollback_without_a_backup_says_so(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    install_discovery(monkeypatch, instances)

    result = updater.rollback(exe, instances=instances)

    assert result.ok is False
    assert "nothing to roll back" in result.detail
    assert recorder.calls == []


def test_rollback_aborts_when_an_instance_will_not_stop(
    monkeypatch: pytest.MonkeyPatch, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    (exe.parent / "UsageMonitorForCopilot.exe.bak").write_bytes(payload_for("0.3.1"))
    exe.write_bytes(payload_for("0.3.2"))
    rec = ProcessRecorder(stop_ok=False)
    monkeypatch.setattr(process_control, "stop", rec.stop)
    monkeypatch.setattr(process_control, "start", rec.start)
    install_discovery(monkeypatch, instances)

    result = updater.rollback(exe, instances=instances)

    assert result.ok is False
    assert result.rollback_available is True
    assert exe.read_bytes() == payload_for("0.3.2")  # nothing was moved


# --------------------------------------------------------------------------- #
# describe (dry run)
# --------------------------------------------------------------------------- #


def test_describe_lists_every_step_and_touches_nothing(
    recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    before = sorted(path.name for path in exe.parent.iterdir())
    plan = plan_for(exe, instances, release("v0.3.2", size=15_260_446, digest="sha256:" + "ab" * 32))

    lines = updater.describe(plan)
    text = "\n".join(lines)

    assert "would update Copilot 0.3.1 -> 0.3.2" in text
    assert "would download UsageMonitorForCopilot.exe (15260446 bytes)" in text
    assert "GitHub API digest" in text
    assert "would stop copilot" in text
    assert "UsageMonitorForCopilot.exe.bak" in text
    assert "would start copilot" in text
    assert recorder.calls == []
    assert sorted(path.name for path in exe.parent.iterdir()) == before


def test_describe_mentions_the_sidecar_or_the_missing_digest(scenario: tuple[Path, list[Instance]]) -> None:
    exe, instances = scenario
    sidecar_plan = plan_for(exe, instances, release("v0.3.2", sidecar="https://e/m.exe.sha256"))
    assert any("https://e/m.exe.sha256" in line for line in updater.describe(sidecar_plan))

    naked_plan = plan_for(exe, instances, release("v0.3.2"))
    assert any("explicit confirmation" in line for line in updater.describe(naked_plan))


def test_describe_honours_no_restart(scenario: tuple[Path, list[Instance]]) -> None:
    exe, instances = scenario
    lines = updater.describe(plan_for(exe, instances, release("v0.3.2")), restart=False)
    assert any("--no-restart" in line for line in lines)
    assert not any(line.startswith("would start") for line in lines)


def test_paths_are_siblings_of_the_executable(tmp_path: Path) -> None:
    exe = tmp_path / "Copilot" / "UsageMonitorForCopilot.exe"
    assert updater.download_path_for(exe).parent == exe.parent
    assert updater.backup_path_for(exe).name == "UsageMonitorForCopilot.exe.bak"


def test_event_callback_and_collected_events_agree(
    monkeypatch: pytest.MonkeyPatch, recorder: ProcessRecorder, scenario: tuple[Path, list[Instance]]
) -> None:
    exe, instances = scenario
    payload = payload_for("0.3.2")
    install_download(monkeypatch, recorder, payload)
    install_discovery(monkeypatch, instances)
    seen: list[UpdateEvent] = []
    result = updater.apply(
        plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest())),
        on_event=seen.append,
        post_check_delay=0.0,
    )
    assert list(result.events) == seen
    assert seen[-1].step == "done"


def test_the_update_engine_never_launches_anything_itself(
    monkeypatch: pytest.MonkeyPatch, scenario: tuple[Path, list[Instance]]
) -> None:
    """Belt and braces: a Popen inside ``apply`` would be a rule violation."""
    exe, instances = scenario
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("popen")
        raise AssertionError("updater must go through process_control")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    rec = ProcessRecorder()
    monkeypatch.setattr(process_control, "stop", rec.stop)
    monkeypatch.setattr(process_control, "start", rec.start)
    payload = payload_for("0.3.2")
    install_download(monkeypatch, rec, payload)
    install_discovery(monkeypatch, instances)
    result = updater.apply(
        plan_for(exe, instances, release("v0.3.2", digest=hashlib.sha256(payload).hexdigest())),
        post_check_delay=0.0,
    )
    assert result.ok, result.detail
    assert calls == []
