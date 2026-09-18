"""Tests for instance discovery with a fully faked machine.

Nothing here touches the real process table, registry or file system outside
``tmp_path``: the whole scenario of PLAN.md section 2 (two Claude instances -
one identified only by its environment variable -, Copilot, Codex, plus noise)
is rebuilt from fixtures, so the suite passes on a bare CI runner.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import psutil
import pytest

from usage_monitor_manager import discovery, manager_config
from usage_monitor_manager.discovery import ProcessSnapshot
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.manager_config import DeclaredInstance
from usage_monitor_manager.model import Instance

SETTINGS = discovery.SETTINGS_FILENAME
PRODUCTS = {
    "UsageMonitorForClaude.exe": "Usage Monitor for Claude",
    "UsageMonitorForCopilot.exe": "Usage Monitor for Copilot",
    "UsageMonitorForCodex.exe": "Usage Monitor for Codex",
}


class Machine:
    """A fake Windows box: home dir, an exe folder, processes, Run entries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.tools = root / "Tools"
        self.other = root / "Other"
        for directory in (self.home, self.tools, self.other):
            directory.mkdir(parents=True, exist_ok=True)
        self.processes: list[ProcessSnapshot] = []
        self.run_entries: dict[str, str] = {}
        self.shortcuts: dict[Path, tuple[Path | None, list[str]]] = {}
        self.products: dict[str, str] = {}

    # -- construction helpers ------------------------------------------------
    def exe(self, name: str, *, folder: Path | None = None, product: str | None = None) -> Path:
        path = (folder or self.tools) / name
        path.write_bytes(b"MZ fake")
        self.products[str(path).casefold()] = product if product is not None else PRODUCTS.get(name, "")
        return path

    def config_dir(self, name: str, *, with_settings: bool = True) -> Path:
        directory = self.home / name
        directory.mkdir(parents=True, exist_ok=True)
        if with_settings:
            (directory / SETTINGS).write_text("{}", encoding="utf-8")
        return directory

    def pair(
        self,
        exe: Path,
        *,
        bootloader_pid: int,
        app_pid: int,
        args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        denied: bool = False,
    ) -> None:
        def read_environ() -> Mapping[str, str]:
            if denied:
                raise psutil.AccessDenied(app_pid)
            return dict(env or {})

        self.processes.append(
            ProcessSnapshot(pid=bootloader_pid, name=exe.name, ppid=4, exe=exe, cmdline=(str(exe),), create_time=1.0)
        )
        self.processes.append(
            ProcessSnapshot(
                pid=app_pid,
                name=exe.name,
                ppid=bootloader_pid,
                exe=exe,
                cmdline=(str(exe), *args),
                create_time=2.0,
                read_environ=read_environ,
            )
        )

    def orphan(self, exe: Path, *, pid: int, args: tuple[str, ...] = ()) -> None:
        self.processes.append(
            ProcessSnapshot(pid=pid, name=exe.name, ppid=999999, exe=exe, cmdline=(str(exe), *args), create_time=3.0)
        )

    # -- monkeypatch wiring --------------------------------------------------
    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self.home))
        monkeypatch.setenv("LOCALAPPDATA", str(self.root / "LocalAppData"))
        monkeypatch.setenv("APPDATA", str(self.root / "AppData"))
        monkeypatch.setattr(discovery, "scan_processes", lambda: list(self.processes))
        monkeypatch.setattr(discovery.platform, "read_run_entries", lambda: dict(self.run_entries))
        monkeypatch.setattr(discovery.platform, "file_product_name", self._product_name)
        monkeypatch.setattr(discovery.platform, "read_shortcuts", self._read_shortcuts)
        monkeypatch.setattr(discovery.versions, "exe_version", lambda path: None if path is None else "1.22.0.0")

    def _product_name(self, path: Path | str) -> str | None:
        return self.products.get(str(path).casefold()) or None

    def _read_shortcuts(self, paths: object) -> dict[Path, tuple[Path | None, list[str]]]:
        wanted = {str(p).casefold() for p in paths}  # pyright: ignore[reportGeneralTypeIssues]
        return {link: value for link, value in self.shortcuts.items() if str(link).casefold() in wanted}


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Machine]:
    box = Machine(tmp_path)
    box.install(monkeypatch)
    yield box


def _by_id(instances: list[Instance]) -> dict[str, Instance]:
    return {instance.instance_id: instance for instance in instances}


def test_pair_with_config_dir_on_the_command_line(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    valeria = machine.config_dir(".claude-valeria")
    machine.pair(exe, bootloader_pid=82104, app_pid=93020, args=(f"--config-dir={valeria}",))

    instances = discovery.discover()
    assert len(instances) == 1
    instance = instances[0]
    assert instance.kind.key == "claude"
    assert instance.config_dir == valeria
    assert instance.config_dir_source == "cmdline"
    assert instance.config_dir_unknown is False
    assert instance.instance_suffix == config_dir_suffix(valeria, machine.home / ".claude")
    assert instance.is_running is True
    assert instance.processes is not None
    assert (instance.processes.bootloader_pid, instance.processes.app_pid) == (82104, 93020)
    assert instance.processes.started_at is not None
    assert instance.exe_path == exe
    assert instance.exe_version == "1.22.0.0"
    assert instance.exe_mtime is not None
    # the same dir is also seen as a stray settings file: both sources are kept
    assert sorted(instance.sources) == ["process", "settings_file"]
    assert instance.settings_path == valeria / SETTINGS
    assert instance.autostart_entry is False


def test_pair_identified_only_by_its_environment_variable(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForCopilot.exe")
    work = machine.config_dir(".copilot-work")
    machine.pair(exe, bootloader_pid=100, app_pid=101, env={"COPILOT_HOME": str(work)})

    instance = discovery.discover()[0]
    assert instance.config_dir == work
    assert instance.config_dir_source == "env"
    assert instance.launch_env == {"COPILOT_HOME": str(work)}


def test_orphan_app_has_no_bootloader(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForCodex.exe")
    machine.config_dir(".codex")
    machine.orphan(exe, pid=18204)

    instance = discovery.discover()[0]
    assert instance.processes is not None
    assert instance.processes.bootloader_pid is None
    assert instance.processes.app_pid == 18204
    assert instance.config_dir == machine.home / ".codex"
    assert instance.config_dir_source == "default"
    assert instance.instance_suffix == ""


def test_same_exe_name_in_another_folder_is_not_ours(machine: Machine) -> None:
    ours = machine.exe("UsageMonitorForClaude.exe")
    impostor = machine.exe("UsageMonitorForClaude.exe", folder=machine.other, product="Some Other Tool")
    machine.pair(ours, bootloader_pid=10, app_pid=11)
    machine.pair(impostor, bootloader_pid=20, app_pid=21)

    instances = discovery.discover()
    assert [str(instance.exe_path) for instance in instances] == [str(ours)]


def test_access_denied_environ_keeps_going(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(exe, bootloader_pid=30, app_pid=31, denied=True)

    instance = discovery.discover()[0]
    assert instance.config_dir_source == "unknown"
    assert instance.config_dir_unknown is True
    assert instance.config_dir == machine.home / ".claude"
    assert instance.is_running is True


def test_settings_search_order_reports_shadowed_files(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    valeria = machine.config_dir(".claude-valeria")
    machine.config_dir(".claude")
    (machine.tools / SETTINGS).write_text("{}", encoding="utf-8")
    machine.pair(exe, bootloader_pid=1, app_pid=2, args=(f"--config-dir={valeria}",))

    instance = next(i for i in discovery.discover() if i.config_dir == valeria)
    assert instance.settings_path == valeria / SETTINGS
    assert instance.settings_exists is True
    assert list(instance.shadow_settings_paths) == [machine.tools / SETTINGS, machine.home / ".claude" / SETTINGS]


def test_default_config_dir_skips_its_own_candidate(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForCodex.exe")
    default = machine.config_dir(".codex")
    machine.orphan(exe, pid=7)

    candidates = discovery.settings_candidates(get_kind("codex"), default, exe)
    assert candidates == [machine.tools / SETTINGS, default / SETTINGS]


def test_no_settings_file_anywhere(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForCodex.exe")
    machine.orphan(exe, pid=7)

    instance = discovery.discover()[0]
    assert instance.settings_path is None
    assert instance.settings_exists is False
    assert list(instance.shadow_settings_paths) == []


def test_autostart_entry_is_matched_by_suffix(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    valeria = machine.config_dir(".claude-valeria")
    suffix = config_dir_suffix(valeria, machine.home / ".claude")
    command = f'"{exe}" --config-dir="{valeria}"'
    machine.run_entries = {f"UsageMonitorForClaude{suffix}": command, "Unrelated": "notepad.exe"}
    machine.pair(exe, bootloader_pid=1, app_pid=2, args=(f"--config-dir={valeria}",))

    instance = next(i for i in discovery.discover() if i.config_dir == valeria)
    assert instance.autostart_entry is True
    assert instance.autostart_command == command
    assert sorted(instance.sources) == ["process", "run_key", "settings_file"]


def test_dormant_instance_from_a_run_entry(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    machine.config_dir(".claude")
    machine.run_entries = {"UsageMonitorForClaude": f'"{exe}"'}

    instances = discovery.discover()
    assert len(instances) == 1
    instance = instances[0]
    assert instance.is_running is False
    assert sorted(instance.sources) == ["run_key", "settings_file"]
    assert instance.autostart_entry is True
    assert instance.exe_path == exe

    assert discovery.discover(include_dormant=False) == []


def test_dormant_instance_from_a_legacy_shortcut(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    valeria = machine.config_dir(".claude-valeria")
    machine.pair(exe, bootloader_pid=1, app_pid=2)  # default instance, running
    link = machine.tools / "UsageMonitorForClaude.exe - valeria.lnk"
    link.write_bytes(b"L\x00")
    machine.shortcuts = {link: (exe, [f"--config-dir={valeria}"])}

    instances = _by_id(discovery.discover())
    dormant = instances[f"claude{config_dir_suffix(valeria, machine.home / '.claude')}"]
    assert dormant.is_running is False
    assert sorted(dormant.sources) == ["settings_file", "shortcut"]
    assert dormant.config_dir == valeria
    assert list(dormant.launch_args) == [f"--config-dir={valeria}"]


def test_dormant_instance_from_a_stray_settings_file(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(exe, bootloader_pid=1, app_pid=2)
    dormant_dir = machine.config_dir(".claude-archive")
    machine.config_dir(".unrelated-tool")

    instances = discovery.discover()
    dormant = [instance for instance in instances if instance.config_dir == dormant_dir]
    assert len(dormant) == 1
    assert dormant[0].sources == ("settings_file",)
    assert dormant[0].exe_path == exe  # reuses the exe known for that kind
    assert [instance.config_dir for instance in instances if instance.config_dir.name == ".unrelated-tool"] == []


def test_running_instance_wins_over_every_dormant_source(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    valeria = machine.config_dir(".claude-valeria")
    suffix = config_dir_suffix(valeria, machine.home / ".claude")
    machine.pair(exe, bootloader_pid=82104, app_pid=93020, env={"CLAUDE_CONFIG_DIR": str(valeria)})
    machine.run_entries = {f"UsageMonitorForClaude{suffix}": f'"{exe}" --config-dir="{valeria}"'}
    link = machine.tools / "UsageMonitorForClaude.exe - valeria.lnk"
    link.write_bytes(b"L\x00")
    machine.shortcuts = {link: (exe, [f"--config-dir={valeria}"])}

    instances = discovery.discover()
    assert len(instances) == 1
    instance = instances[0]
    assert instance.processes is not None
    assert instance.processes.app_pid == 93020
    assert instance.config_dir_source == "env"
    assert sorted(instance.sources) == ["process", "run_key", "settings_file", "shortcut"]


def test_full_machine_is_sorted_and_serializable(machine: Machine) -> None:
    claude = machine.exe("UsageMonitorForClaude.exe")
    copilot = machine.exe("UsageMonitorForCopilot.exe")
    codex = machine.exe("UsageMonitorForCodex.exe")
    machine.config_dir(".claude")
    valeria = machine.config_dir(".claude-valeria")
    machine.config_dir(".copilot")
    machine.config_dir(".codex")
    machine.pair(claude, bootloader_pid=32472, app_pid=32520)
    machine.pair(claude, bootloader_pid=82104, app_pid=93020, env={"CLAUDE_CONFIG_DIR": str(valeria)})
    machine.pair(copilot, bootloader_pid=32440, app_pid=17540)
    machine.orphan(codex, pid=18204)

    instances = discovery.discover()
    assert [instance.kind.key for instance in instances] == ["claude", "claude", "copilot", "codex"]
    assert [instance.config_dir.name for instance in instances] == [
        ".claude",
        ".claude-valeria",
        ".copilot",
        ".codex",
    ]
    assert all(instance.is_running for instance in instances)
    payload = json.dumps([instance.to_dict() for instance in instances])
    assert '"config_dir_source": "env"' in payload


def test_scan_processes_on_the_real_machine() -> None:
    snapshots = discovery.scan_processes()
    assert snapshots
    assert any(snapshot.exe is not None for snapshot in snapshots)
    assert all(isinstance(snapshot.pid, int) for snapshot in snapshots)


# --------------------------------------------------------------------------- #
# Source (d): instances declared in manager-config.json
# --------------------------------------------------------------------------- #


def test_declared_instance_is_a_dormant_source(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(exe, bootloader_pid=32472, app_pid=32520)
    valeria = machine.config_dir(".claude-valeria", with_settings=False)

    instances = discovery.discover(
        declared=[DeclaredInstance(kind="claude", config_dir=valeria, label="Claude Valeria")]
    )
    by_id = _by_id(instances)
    declared = by_id[f"claude{config_dir_suffix(valeria, machine.home / '.claude')}"]
    assert declared.sources == (discovery.SOURCE_DECLARED,)
    assert declared.label == "Claude Valeria"
    assert declared.is_running is False
    assert declared.exe_path == exe  # the kind's known executable is reused


def test_declared_label_reaches_a_running_instance(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    default = machine.config_dir(".claude")
    machine.pair(exe, bootloader_pid=32472, app_pid=32520)

    instances = discovery.discover(declared=[DeclaredInstance(kind="claude", config_dir=default, label="Principale")])
    assert len(instances) == 1
    assert instances[0].label == "Principale"
    assert instances[0].is_running is True
    assert discovery.SOURCE_PROCESS in instances[0].sources
    assert discovery.SOURCE_DECLARED in instances[0].sources


def test_disabled_declared_instance_is_ignored(machine: Machine) -> None:
    machine.exe("UsageMonitorForCodex.exe")
    hidden = machine.config_dir(".codex-hidden", with_settings=False)
    instances = discovery.discover(declared=[DeclaredInstance(kind="codex", config_dir=hidden, enabled=False)])
    assert instances == []


def test_declared_instance_with_an_unknown_kind_is_ignored(machine: Machine) -> None:
    instances = discovery.discover(
        declared=[DeclaredInstance(kind="gemini", config_dir=machine.home / ".gemini", label="Future")]
    )
    assert instances == []


def test_declared_instances_are_not_added_without_dormant(machine: Machine) -> None:
    exe = machine.exe("UsageMonitorForClaude.exe")
    machine.pair(exe, bootloader_pid=1, app_pid=2)
    valeria = machine.config_dir(".claude-valeria", with_settings=False)
    instances = discovery.discover(
        include_dormant=False,
        declared=[DeclaredInstance(kind="claude", config_dir=valeria, label="Claude Valeria")],
    )
    assert [i.config_dir for i in instances] == [machine.home / ".claude"]


def test_declared_defaults_to_the_manager_config_file(
    machine: Machine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``declared=None`` reads the real file - which the fixture has relocated."""
    config_path = tmp_path / "LocalAppData" / "UsageMonitorManager" / "manager-config.json"
    machine.exe("UsageMonitorForCopilot.exe")
    work = machine.config_dir(".copilot-work", with_settings=False)
    manager_config.save(
        manager_config.ManagerConfig(
            instances=(DeclaredInstance(kind="copilot", config_dir=work, label="Copilot Lavoro"),)
        ),
        config_path,
    )
    assert manager_config.config_path() == config_path

    instances = discovery.discover()
    assert [i.label for i in instances] == ["Copilot Lavoro"]
