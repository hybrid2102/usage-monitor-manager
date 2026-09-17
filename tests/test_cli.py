"""Smoke tests for the CLI surface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import __main__, __version__, cli
from usage_monitor_manager.cli import EXIT_NOT_IMPLEMENTED, build_parser, format_instance_table, main
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair


def test_version_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    "argv",
    [
        ["restart", "--all"],
        ["stop", "--kind", "claude"],
        ["start", "--id", "claude_578560bbc610"],
        ["check"],
        ["update", "--kind", "copilot", "--yes"],
        ["config", "show", "--instance", "claude"],
        ["self-update", "--check-only"],
        ["status", "--json"],
    ],
)
def test_subcommands_report_not_implemented(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == EXIT_NOT_IMPLEMENTED
    assert "not implemented" in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_NOT_IMPLEMENTED
    assert "usage: umm" in capsys.readouterr().out


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_module_entry_without_args_is_tray_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    assert __main__.main([]) == 0
    assert capsys.readouterr().out.strip() == "tray not implemented yet"


def test_module_entry_with_args_dispatches_to_cli() -> None:
    assert __main__.main(["check"]) == EXIT_NOT_IMPLEMENTED


def _instances() -> list[Instance]:
    return [
        Instance(
            kind=get_kind("claude"),
            config_dir=Path(r"C:\Users\Marco\.claude"),
            instance_suffix="",
            exe_path=Path(r"D:\Tools\UsageMonitorForClaude.exe"),
            exe_version="1.22.0.0",
            exe_mtime=datetime(2026, 9, 1, tzinfo=UTC),
            settings_path=Path(r"C:\Users\Marco\.claude\usage-monitor-settings.json"),
            settings_exists=True,
            autostart_entry=True,
            autostart_command=r'"D:\Tools\UsageMonitorForClaude.exe"',
            sources=("process", "run_key"),
            config_dir_source="default",
            processes=ProcessPair(bootloader_pid=32472, app_pid=32520),
        ),
        Instance(
            kind=get_kind("codex"),
            config_dir=Path(r"C:\Users\Marco\.codex"),
            instance_suffix="",
            sources=("settings_file",),
            config_dir_source="unknown",
        ),
    ]


def test_list_table(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda include_dormant=True: _instances())
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "KIND" in out
    assert "32472/32520" in out
    assert "\u25cf" in out and "\u25cb" in out
    assert "(?)" in out  # the codex row could not determine its config dir


def test_list_json(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda include_dormant=True: _instances())
    assert main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["kind"] for row in payload] == ["claude", "codex"]
    assert payload[0]["processes"]["app_pid"] == 32520
    assert payload[1]["config_dir_source"] == "unknown"


def test_format_instance_table_when_empty() -> None:
    assert "no usage-monitor instance" in format_instance_table([])
    assert cli.EXIT_UPDATES_AVAILABLE == 10
