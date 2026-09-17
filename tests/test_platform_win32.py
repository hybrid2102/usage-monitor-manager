"""Tests for the Windows platform layer.

The file-version tests run against real system files, which exist on every
Windows machine and on the ``windows-latest`` CI runner: no fixture binary has
to be committed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only platform layer")

from usage_monitor_manager.platform import win32  # noqa: E402 - after the platform skip

KERNEL32 = Path(r"C:\Windows\System32\kernel32.dll")


def test_file_version_of_kernel32() -> None:
    version = win32.file_version(KERNEL32)
    assert version is not None
    assert version[0].isdigit()
    assert version.count(".") >= 2


def test_file_product_name_of_kernel32() -> None:
    product = win32.file_product_name(KERNEL32)
    assert product is not None
    assert "Windows" in product


def test_file_version_without_resource(tmp_path: Path) -> None:
    target = tmp_path / "empty.exe"
    target.write_bytes(b"\x00" * 16)
    assert win32.file_version(target) is None
    assert win32.file_product_name(target) is None


def test_file_version_of_missing_file(tmp_path: Path) -> None:
    assert win32.file_version(tmp_path / "nope.exe") is None


@pytest.mark.parametrize(
    ("command", "exe", "args"),
    [
        (
            r'"C:\Tools\UsageMonitorForClaude.exe" --config-dir="C:\Users\Marco\.claude-valeria"',
            r"C:\Tools\UsageMonitorForClaude.exe",
            [r"--config-dir=C:\Users\Marco\.claude-valeria"],
        ),
        (
            r'"C:\Program Files\App\Mon.exe"',
            r"C:\Program Files\App\Mon.exe",
            [],
        ),
        (
            r"C:\Tools\UsageMonitorForCodex.exe --verbose",
            r"C:\Tools\UsageMonitorForCodex.exe",
            ["--verbose"],
        ),
        (
            r"C:\Program Files\My Tools\Mon.exe --config-dir C:\cfg",
            r"C:\Program Files\My Tools\Mon.exe",
            ["--config-dir", r"C:\cfg"],
        ),
        (
            r'"C:\Tools\Mon.exe',
            r"C:\Tools\Mon.exe",
            [],
        ),
        ("notepad", "notepad", []),
    ],
)
def test_parse_run_command(command: str, exe: str, args: list[str]) -> None:
    parsed_exe, parsed_args = win32.parse_run_command(command)
    assert parsed_exe == Path(exe)
    assert parsed_args == args


def test_parse_run_command_empty() -> None:
    assert win32.parse_run_command("   ") == (None, [])


def test_read_run_entries_returns_strings() -> None:
    entries = win32.read_run_entries()
    assert all(isinstance(name, str) and isinstance(value, str) for name, value in entries.items())


def test_no_window_popen_kwargs() -> None:
    kwargs = win32.no_window_popen_kwargs()
    assert kwargs["creationflags"] == win32.CREATE_NO_WINDOW
    assert kwargs["startupinfo"] is not None


def test_read_shortcuts_with_no_paths() -> None:
    assert win32.read_shortcuts([]) == {}
