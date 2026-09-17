"""Smoke tests for the CLI surface of Phase 0."""

from __future__ import annotations

import pytest

from usage_monitor_manager import __main__, __version__
from usage_monitor_manager.cli import EXIT_NOT_IMPLEMENTED, build_parser, main


def test_version_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    "argv",
    [
        ["list"],
        ["list", "--json"],
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
    assert __main__.main(["list"]) == EXIT_NOT_IMPLEMENTED
