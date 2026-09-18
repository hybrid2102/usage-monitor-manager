"""Discovery, update planning and archiving for a ``github-exe`` kind.

The fake machine of ``test_discovery.py`` is reused as is; what differs is the
kind under test: ``AgentMonitorForClaude.exe`` has no config directory, no
environment variable and no settings file, so

* a running pair must be found from the process table alone, with its
  "directory" being the one the executable lives in, and **without** the
  discovery ever calling ``read_environ()`` - that call is the one PLAN.md
  section 8 documents as denied on a foreign or elevated process, and there is
  nothing in the environment of a portable exe worth the risk;
* a dormant copy must be found by the executable scan of the folders discovery
  already knows (never a disk scan);
* the update engine must plan for it like for any monitor, and the archive
  must keep its previous builds the same way.

Nothing here reaches the network: the release is a fixture and
``github.fetch_latest_release`` is replaced.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from test_discovery import Machine, machine  # noqa: F401 - the fixture is used by name
from usage_monitor_manager import archive, discovery, manager_config, updater
from usage_monitor_manager.discovery import ProcessSnapshot
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.manager_config import DeclaredInstance
from usage_monitor_manager.model import Instance, ReleaseAsset, ReleaseInfo

AGENT_EXE = "AgentMonitorForClaude.exe"
AGENT_PRODUCT = "Agent Monitor for Claude"


def _pair_without_environ(box: Machine, exe: Path, *, bootloader_pid: int, app_pid: int) -> list[str]:
    """A running PyInstaller pair whose ``read_environ`` records every call."""
    calls: list[str] = []

    def read_environ() -> Mapping[str, str]:
        calls.append("read_environ")
        return {}

    box.processes.append(
        ProcessSnapshot(pid=bootloader_pid, name=exe.name, ppid=4, exe=exe, cmdline=(str(exe),), create_time=1.0)
    )
    box.processes.append(
        ProcessSnapshot(
            pid=app_pid,
            name=exe.name,
            ppid=bootloader_pid,
            exe=exe,
            cmdline=(str(exe),),
            create_time=2.0,
            read_environ=read_environ,
        )
    )
    return calls


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_running_pair_is_discovered_without_reading_the_environment(machine: Machine) -> None:  # noqa: F811
    exe = machine.exe(AGENT_EXE, product=AGENT_PRODUCT)
    calls = _pair_without_environ(machine, exe, bootloader_pid=4100, app_pid=4101)

    instances = discovery.discover()
    assert len(instances) == 1
    instance = instances[0]
    assert instance.kind.key == "agent-monitor-claude"
    assert instance.kind.family == "github-exe"
    assert instance.instance_id == "agent-monitor-claude"  # one copy: no suffix
    assert instance.instance_suffix == ""
    assert instance.config_dir == machine.tools  # its own folder, not a config dir
    assert instance.config_dir_source == "default"
    assert instance.config_dir_unknown is False
    assert instance.is_running is True
    assert instance.processes is not None
    assert (instance.processes.bootloader_pid, instance.processes.app_pid) == (4100, 4101)
    assert instance.exe_path == exe
    assert instance.exe_version == "1.22.0.0"
    assert instance.settings_path is None
    assert instance.settings_exists is False
    assert instance.launch_env == {}
    # seen both as a process and as a file in a known folder: both are recorded
    assert instance.sources == ("process", "exe_scan")
    assert calls == [], "a github-exe kind must never have its environment read"


def test_a_dormant_copy_is_found_next_to_a_known_executable(machine: Machine) -> None:  # noqa: F811
    claude = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(claude, bootloader_pid=10, app_pid=11)
    agent = machine.exe(AGENT_EXE, product=AGENT_PRODUCT)

    by_id = {instance.instance_id: instance for instance in discovery.discover()}
    dormant = by_id["agent-monitor-claude"]
    assert dormant.is_running is False
    assert dormant.processes is None
    assert dormant.exe_path == agent
    assert dormant.sources == ("exe_scan",)
    assert dormant.config_dir == machine.tools


def test_a_dormant_copy_is_not_looked_for_when_dormant_is_off(machine: Machine) -> None:  # noqa: F811
    claude = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(claude, bootloader_pid=10, app_pid=11)
    machine.exe(AGENT_EXE, product=AGENT_PRODUCT)

    ids = [instance.instance_id for instance in discovery.discover(include_dormant=False)]
    assert ids == ["claude"]


def test_a_copy_in_an_unknown_folder_is_not_scanned_for(machine: Machine) -> None:  # noqa: F811
    claude = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(claude, bootloader_pid=10, app_pid=11)
    machine.exe(AGENT_EXE, folder=machine.other, product=AGENT_PRODUCT)

    ids = [instance.instance_id for instance in discovery.discover()]
    assert ids == ["claude"], "discovery must not walk the disk looking for portable exes"


def test_two_copies_of_the_same_kind_both_get_a_suffix(machine: Machine) -> None:  # noqa: F811
    """One copy is the plain id; two copies are told apart by their folder."""
    claude = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(claude, bootloader_pid=10, app_pid=11)
    running = machine.exe(AGENT_EXE, folder=machine.other, product=AGENT_PRODUCT)
    _pair_without_environ(machine, running, bootloader_pid=12, app_pid=13)
    machine.exe(AGENT_EXE, product=AGENT_PRODUCT)  # a second, dormant copy in Tools

    found = [instance for instance in discovery.discover() if instance.kind.key == "agent-monitor-claude"]
    ids = sorted(instance.instance_id for instance in found)
    assert len(ids) == 2
    assert all(candidate.startswith("agent-monitor-claude_") for candidate in ids), ids
    assert ids[0] != ids[1]
    assert sorted(str(instance.config_dir) for instance in found) == sorted([str(machine.other), str(machine.tools)])


def test_a_foreign_process_with_the_same_name_is_rejected_by_its_product_name(machine: Machine) -> None:  # noqa: F811
    """The name matches a known kind, the ProductName says it is not ours."""
    impostor = machine.exe(AGENT_EXE, folder=machine.other, product="Somebody Else's Tool")
    _pair_without_environ(machine, impostor, bootloader_pid=20, app_pid=21)

    assert discovery.discover() == []


def test_a_custom_kind_matches_on_the_file_name_alone(machine: Machine) -> None:  # noqa: F811
    """A user-defined entry declares no ProductName, so the name must suffice."""
    from usage_monitor_manager import kinds

    kinds.set_custom_kinds(
        [
            manager_config.CustomKind(
                key="statusline",
                display_name="Statusline Helper",
                exe_name="StatuslineHelper.exe",
                repo="hybrid2102/statusline-helper",
            )
        ]
    )
    exe = machine.exe("StatuslineHelper.exe", product="Something Unrelated")
    _pair_without_environ(machine, exe, bootloader_pid=50, app_pid=51)

    instances = discovery.discover()
    assert [instance.instance_id for instance in instances] == ["statusline"]
    assert instances[0].kind.source == "custom"


def test_a_declared_github_exe_instance_points_at_its_folder(machine: Machine) -> None:  # noqa: F811
    machine.exe(AGENT_EXE, folder=machine.other, product=AGENT_PRODUCT)
    instances = discovery.discover(
        declared=[
            DeclaredInstance(kind="agent-monitor-claude", config_dir=machine.other, label="Agent Monitor"),
        ]
    )
    assert len(instances) == 1
    assert instances[0].label == "Agent Monitor"
    assert instances[0].exe_path == machine.other / AGENT_EXE


# --------------------------------------------------------------------------- #
# Update planning and archiving
# --------------------------------------------------------------------------- #


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        tag="v0.9.0",
        version="0.9.0",
        published_at=datetime(2026, 9, 18, tzinfo=UTC),
        html_url="https://example.invalid/agent/v0.9.0",
        body_markdown="- nuovo",
        asset=ReleaseAsset(name=AGENT_EXE, url="https://example.invalid/a.exe", size=100, digest="b" * 64),
    )


def _agent_instance(exe: Path) -> Instance:
    return Instance(
        kind=get_kind("agent-monitor-claude"),
        config_dir=exe.parent,
        instance_suffix="",
        exe_path=exe,
        exe_version="0.8.0.0",
    )


def test_plan_updates_covers_a_github_exe_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / AGENT_EXE
    exe.write_bytes(b"MZ old")
    monkeypatch.setattr(updater.versions, "exe_version", lambda path: None if path is None else "0.8.0.0")
    asked: list[tuple[str, bool]] = []

    def fake_fetch(repo: str, *, include_prerelease: bool = False, **_: object) -> ReleaseInfo:
        asked.append((repo, include_prerelease))
        return _release()

    monkeypatch.setattr(updater.github, "fetch_latest_release", fake_fetch)

    plans = updater.plan_updates([_agent_instance(exe)])
    assert len(plans) == 1
    plan = plans[0]
    assert asked == [("jens-duttke/agent-monitor-for-claude", False)]
    assert plan.kind.key == "agent-monitor-claude"
    assert plan.current_version == "0.8.0.0"
    assert plan.target.version == "0.9.0"
    assert str(updater.plan_status(plan)) == "update_available"
    assert plan.affected_instances[0].instance_id == "agent-monitor-claude"
    steps = updater.describe(plan)
    assert any(AGENT_EXE in line for line in steps)


def test_the_archive_keeps_previous_builds_of_a_github_exe(tmp_path: Path) -> None:
    exe = tmp_path / AGENT_EXE
    exe.write_bytes(b"MZ 0.8.0")
    archive.record_current(exe, version="0.8.0.0", tag="v0.8.0")
    assert archive.current_version(exe) == "0.8.0.0"
    assert archive.current_release_tag(exe) == "v0.8.0"

    exe.with_name(f"{exe.name}.bak").write_bytes(b"MZ 0.8.0")
    kept = archive.archive_exe(exe, "0.8.0.0", source_release_tag="v0.8.0")
    assert kept is not None
    assert kept.source_release_tag == "v0.8.0"
    assert kept.path.parent == archive.archive_dir(exe)
    assert kept.path.exists()
    assert [item.version for item in archive.list_archived(exe)] == ["0.8.0"]
    assert archive.find(exe, "0.8.0") is not None
