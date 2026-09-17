"""Tests for version parsing, comparison and update status."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from usage_monitor_manager.versions import (
    VersionStatus,
    compare,
    display_version,
    exe_mtime,
    exe_version,
    format_version,
    parse_version,
    status_for,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.22.0", (1, 22, 0)),
        ("v1.22.0", (1, 22, 0)),
        ("1.22.0.0", (1, 22, 0)),  # the PE form of the monitors
        ("V0.3.2", (0, 3, 2)),
        ("1.22", (1, 22, 0)),
        ("2", (2, 0, 0)),
        ("0.3.2-beta.1", (0, 3, 2)),
        ("  v10.0.26200.1  ", (10, 0, 26200)),
        ("", (0, 0, 0)),
        ("nightly", (0, 0, 0)),
        (None, (0, 0, 0)),
    ],
)
def test_parse_version(text: str | None, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


def test_format_version_normalizes() -> None:
    assert format_version("v1.22.0.0") == "1.22.0"
    assert format_version(None) == "0.0.0"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("1.22.0", "1.22.0.0", 0),
        ("v0.3.1", "0.3.2", -1),
        ("0.3.2", "v0.3.1", 1),
        ("1.9.0", "1.10.0", -1),
        ("2.0.0", "1.99.99", 1),
    ],
)
def test_compare(a: str, b: str, expected: int) -> None:
    assert compare(a, b) == expected


@pytest.mark.parametrize(
    ("local", "remote", "expected"),
    [
        ("1.22.0.0", "v1.22.0", VersionStatus.UP_TO_DATE),
        ("0.3.1", "v0.3.2", VersionStatus.UPDATE_AVAILABLE),
        ("0.4.0", "v0.3.2", VersionStatus.NEWER_LOCAL),
        (None, "v0.3.2", VersionStatus.UNKNOWN),
        ("0.3.2", None, VersionStatus.UNKNOWN),
        ("", "", VersionStatus.UNKNOWN),
    ],
)
def test_status_for(local: str | None, remote: str | None, expected: VersionStatus) -> None:
    assert status_for(local, remote) is expected
    assert str(expected) == expected.value


def test_exe_version_of_a_non_pe_file_is_none(tmp_path: Path) -> None:
    target = tmp_path / "plain.exe"
    target.write_bytes(b"not a PE file")
    assert exe_version(target) is None
    assert exe_version(None) is None


@pytest.mark.skipif(sys.platform != "win32", reason="PE version info is a Windows concept")
def test_exe_version_of_a_real_system_dll() -> None:
    version = exe_version(Path(r"C:\Windows\System32\kernel32.dll"))
    assert version is not None
    assert parse_version(version)[0] >= 6


def test_exe_mtime(tmp_path: Path) -> None:
    target = tmp_path / "file.exe"
    target.write_bytes(b"x")
    stamp = exe_mtime(target)
    assert stamp is not None
    assert stamp.tzinfo is not None
    assert exe_mtime(tmp_path / "missing.exe") is None
    assert exe_mtime(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.22.0.0", "1.22.0"),
        ("0.3.1.0", "0.3.1"),
        ("v0.3.2", "0.3.2"),
        ("1.22", "1.22.0"),
        (None, None),
        ("", None),
    ],
)
def test_display_version_shows_three_components(raw: str | None, expected: str | None) -> None:
    assert display_version(raw) == expected
