"""Tests for autostart: the manager's own Run value and the per-instance ones.

Safety: the real ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` is
never read for a decision and **never written**. Every test either injects a
fake reader/writer, or points the injectable registry root at the scratch key
``HKCU\\Software\\UsageMonitorManagerTest\\Run``, which the fixture deletes
again afterwards.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only autostart layer")

import winreg  # noqa: E402 - after the platform skip

from usage_monitor_manager import process_control  # noqa: E402
from usage_monitor_manager.identity import config_dir_suffix  # noqa: E402
from usage_monitor_manager.kinds import get_kind  # noqa: E402
from usage_monitor_manager.model import Instance  # noqa: E402
from usage_monitor_manager.platform import win32  # noqa: E402

TEST_KEY_ROOT = r"Software\UsageMonitorManagerTest"
TEST_RUN_PATH = rf"{TEST_KEY_ROOT}\Run"
TEST_ROOT: win32.RegistryRoot = (winreg.HKEY_CURRENT_USER, TEST_RUN_PATH)


def _delete_tree(path: str) -> None:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    with key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(rf"{path}\{child}")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


@pytest.fixture
def scratch_run_key() -> Iterator[win32.RegistryRoot]:
    """A throwaway Run key under ``HKCU\\Software\\UsageMonitorManagerTest``."""
    _delete_tree(TEST_KEY_ROOT)
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_RUN_PATH).Close()
    try:
        yield TEST_ROOT
    finally:
        _delete_tree(TEST_KEY_ROOT)


def instance(kind_key: str, config_dir: Path, *, exe: Path | None = None) -> Instance:
    kind = get_kind(kind_key)
    exe_path = exe if exe is not None else Path(rf"C:\Tools\{kind.exe_name}")
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=config_dir_suffix(config_dir, kind.default_config_dir),
        exe_path=exe_path,
    )


# --------------------------------------------------------------------------- #
# Pure command construction (no registry at all)
# --------------------------------------------------------------------------- #


def test_instance_autostart_command_for_the_default_dir() -> None:
    kind = get_kind("claude")
    target = instance("claude", kind.default_config_dir)
    assert process_control.autostart_command(target) == r'"C:\Tools\UsageMonitorForClaude.exe"'
    assert process_control.run_key_name(target) == "UsageMonitorForClaude"


def test_instance_autostart_command_for_a_secondary_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / ".claude-valeria"
    config_dir.mkdir()
    target = instance("claude", config_dir)
    command = process_control.autostart_command(target)
    assert command == f'"C:\\Tools\\UsageMonitorForClaude.exe" --config-dir="{config_dir}"'
    assert process_control.run_key_name(target) == f"UsageMonitorForClaude{target.instance_suffix}"
    assert target.instance_suffix.startswith("_")


def test_instance_without_an_executable_cannot_be_autostarted() -> None:
    kind = get_kind("codex")
    target = Instance(kind=kind, config_dir=kind.default_config_dir, instance_suffix="", exe_path=None)
    with pytest.raises(ValueError, match="no executable path"):
        process_control.autostart_command(target)


def test_manager_autostart_command_uses_the_tray_subcommand() -> None:
    command = win32.manager_autostart_command()
    assert command.startswith('"')
    assert command.endswith("tray")
    assert win32.manager_autostart_command(r'"X:\a.exe" tray') == r'"X:\a.exe" tray'


# --------------------------------------------------------------------------- #
# Injected fake registry
# --------------------------------------------------------------------------- #


class FakeRegistry:
    """An in-memory stand-in for the Run key, injected over the win32 calls."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.roots: list[win32.RegistryRoot | None] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(win32, "read_run_entries", self.read_entries)
        monkeypatch.setattr(win32, "read_run_key", self.read_key)
        monkeypatch.setattr(win32, "write_run_key", self.write)
        monkeypatch.setattr(win32, "delete_run_key", self.delete)
        monkeypatch.setattr(process_control.platform, "read_run_key", self.read_key)
        monkeypatch.setattr(process_control.platform, "write_run_key", self.write)
        monkeypatch.setattr(process_control.platform, "delete_run_key", self.delete)

    def read_entries(self, root: win32.RegistryRoot | None = None) -> dict[str, str]:
        self.roots.append(root)
        return dict(self.values)

    def read_key(self, name: str, root: win32.RegistryRoot | None = None) -> str | None:
        self.roots.append(root)
        return self.values.get(name)

    def write(self, name: str, command: str, root: win32.RegistryRoot | None = None) -> None:
        self.roots.append(root)
        self.values[name] = command

    def delete(self, name: str, root: win32.RegistryRoot | None = None) -> None:
        self.roots.append(root)
        self.values.pop(name, None)


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    registry = FakeRegistry()
    registry.install(monkeypatch)
    return registry


def test_manager_autostart_toggle_on_a_fake_registry(fake_registry: FakeRegistry) -> None:
    assert win32.get_manager_autostart() is None
    command = win32.set_manager_autostart(True, r'"X:\UsageMonitorManager.exe" tray')
    assert command == r'"X:\UsageMonitorManager.exe" tray'
    assert fake_registry.values == {"UsageMonitorManager": r'"X:\UsageMonitorManager.exe" tray'}
    assert win32.get_manager_autostart() == command

    assert win32.set_manager_autostart(False) is None
    assert fake_registry.values == {}


def test_sync_does_nothing_when_autostart_is_off(fake_registry: FakeRegistry) -> None:
    assert win32.sync_manager_autostart_path(r'"X:\new.exe" tray') is None
    assert fake_registry.values == {}


def test_sync_rewrites_a_moved_executable(fake_registry: FakeRegistry) -> None:
    win32.set_manager_autostart(True, r'"D:\OneDrive\old\UsageMonitorManager.exe" tray')
    result = win32.sync_manager_autostart_path(r'"D:\OneDrive\new\UsageMonitorManager.exe" tray')
    assert result == r'"D:\OneDrive\new\UsageMonitorManager.exe" tray'
    assert fake_registry.values["UsageMonitorManager"] == result


def test_sync_leaves_an_identical_command_alone(fake_registry: FakeRegistry) -> None:
    command = r'"D:\OneDrive\UsageMonitorManager.exe" tray'
    win32.set_manager_autostart(True, command)
    writes_before = len(fake_registry.roots)
    assert win32.sync_manager_autostart_path(command.upper()) == command
    # only the read happened, no second write
    assert len(fake_registry.roots) == writes_before + 1


def test_instance_autostart_toggle_on_a_fake_registry(fake_registry: FakeRegistry, tmp_path: Path) -> None:
    config_dir = tmp_path / ".claude-valeria"
    config_dir.mkdir()
    target = instance("claude", config_dir)

    assert process_control.get_instance_autostart(target) is None
    command = process_control.set_instance_autostart(target, True)
    assert command is not None
    assert command.endswith(f'--config-dir="{config_dir}"')
    assert fake_registry.values[process_control.run_key_name(target)] == command
    assert process_control.get_instance_autostart(target) == command

    assert process_control.set_instance_autostart(target, False) is None
    assert fake_registry.values == {}


def test_the_injected_root_is_passed_through(fake_registry: FakeRegistry) -> None:
    win32.set_manager_autostart(True, r'"X:\a.exe" tray', TEST_ROOT)
    assert TEST_ROOT in fake_registry.roots


# --------------------------------------------------------------------------- #
# The one test that talks to the real registry - in a scratch key it deletes
# --------------------------------------------------------------------------- #


def test_manager_autostart_against_a_temporary_registry_subkey(scratch_run_key: win32.RegistryRoot) -> None:
    real_run = win32.read_run_entries()  # read-only: proves we are elsewhere
    assert win32.get_manager_autostart(scratch_run_key) is None

    command = win32.set_manager_autostart(True, r'"X:\UsageMonitorManager.exe" tray', scratch_run_key)
    assert win32.get_manager_autostart(scratch_run_key) == command
    assert win32.read_run_entries(scratch_run_key) == {"UsageMonitorManager": command}

    moved = win32.sync_manager_autostart_path(r'"Y:\UsageMonitorManager.exe" tray', scratch_run_key)
    assert moved == r'"Y:\UsageMonitorManager.exe" tray'
    assert win32.read_run_entries(scratch_run_key)["UsageMonitorManager"] == moved

    win32.set_manager_autostart(False, root=scratch_run_key)
    assert win32.read_run_entries(scratch_run_key) == {}
    win32.delete_run_key("UsageMonitorManager", scratch_run_key)  # idempotent

    # the real Run key was not modified by any of the above
    assert win32.read_run_entries() == real_run


def test_instance_autostart_against_a_temporary_registry_subkey(
    scratch_run_key: win32.RegistryRoot,
    tmp_path: Path,
) -> None:
    real_run = win32.read_run_entries()
    config_dir = tmp_path / ".codex-work"
    config_dir.mkdir()
    target = instance("codex", config_dir)
    name = process_control.run_key_name(target)

    command = process_control.set_instance_autostart(target, True, scratch_run_key)
    assert win32.read_run_entries(scratch_run_key) == {name: command}
    assert process_control.get_instance_autostart(target, scratch_run_key) == command

    process_control.set_instance_autostart(target, False, scratch_run_key)
    assert win32.read_run_entries(scratch_run_key) == {}
    assert win32.read_run_entries() == real_run
