"""Tests for the CLI surface.

Safety rule for this file: no test may reach the real ``process_control``.
Every test of ``restart`` / ``stop`` / ``start`` either runs with ``--dry-run``
or replaces the ``process_control`` entry points, and ``discover`` is always
monkeypatched - so running the suite can never touch a real monitor instance.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import __main__, __version__, cli, process_control
from usage_monitor_manager.cli import (
    EXIT_FAILED,
    EXIT_NO_MATCH,
    EXIT_NOT_IMPLEMENTED,
    build_parser,
    format_instance_table,
    main,
    select_instances,
)
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair

CLAUDE_EXE = r"D:\Tools\UsageMonitorForClaude.exe"
CODEX_EXE = r"D:\Tools\UsageMonitorForCodex.exe"


def _instances() -> list[Instance]:
    return [
        Instance(
            kind=get_kind("claude"),
            config_dir=Path(r"C:\Users\Marco\.claude"),
            instance_suffix="",
            exe_path=Path(CLAUDE_EXE),
            exe_version="1.22.0.0",
            exe_mtime=datetime(2026, 9, 1, tzinfo=UTC),
            settings_path=Path(r"C:\Users\Marco\.claude\usage-monitor-settings.json"),
            settings_exists=True,
            autostart_entry=True,
            autostart_command=f'"{CLAUDE_EXE}"',
            sources=("process", "run_key"),
            config_dir_source="default",
            processes=ProcessPair(bootloader_pid=32472, app_pid=32520),
        ),
        Instance(
            kind=get_kind("codex"),
            config_dir=Path(r"C:\Users\Marco\.codex"),
            instance_suffix="",
            exe_path=Path(CODEX_EXE),
            sources=("settings_file",),
            config_dir_source="unknown",
        ),
    ]


@pytest.fixture
def instances(monkeypatch: pytest.MonkeyPatch) -> list[Instance]:
    """Freeze discovery and turn any real process call into a loud failure."""
    found = _instances()
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda include_dormant=True: found)
    for name in ("stop", "start", "restart", "start_missing", "stop_all", "restart_all"):
        monkeypatch.setattr(
            process_control,
            name,
            _forbidden,
        )
    return found


def _forbidden(*args: object, **kwargs: object) -> None:
    pytest.fail("the CLI tests must never call process_control for real")


def _discovery(monkeypatch: pytest.MonkeyPatch, found: list[Instance]) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda include_dormant=True: found)


# --------------------------------------------------------------------------- #
# Surface
# --------------------------------------------------------------------------- #


def test_version_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    "argv",
    [
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


def test_repeatable_selectors_accumulate() -> None:
    args = build_parser().parse_args(
        ["restart", "--kind", "claude", "--kind", "codex", "--config-dir", "A", "--config-dir", "B"]
    )
    assert args.kind == ["claude", "codex"]
    assert args.config_dir == ["A", "B"]
    assert args.dry_run is False


def test_module_entry_without_args_is_tray_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    assert __main__.main([]) == 0
    assert capsys.readouterr().out.strip() == "tray not implemented yet"


def test_module_entry_with_args_dispatches_to_cli() -> None:
    assert __main__.main(["self-update", "--check-only"]) == EXIT_NOT_IMPLEMENTED


# --------------------------------------------------------------------------- #
# umm list
# --------------------------------------------------------------------------- #


def test_list_table(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _discovery(monkeypatch, _instances())
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "KIND" in out
    assert "32472/32520" in out
    assert "\u25cf" in out and "\u25cb" in out
    assert "(?)" in out  # the codex row could not determine its config dir


def test_list_json(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _discovery(monkeypatch, _instances())
    assert main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["kind"] for row in payload] == ["claude", "codex"]
    assert payload[0]["processes"]["app_pid"] == 32520
    assert payload[1]["config_dir_source"] == "unknown"


def test_format_instance_table_when_empty() -> None:
    assert "no usage-monitor instance" in format_instance_table([])
    assert cli.EXIT_UPDATES_AVAILABLE == 10


# --------------------------------------------------------------------------- #
# Output encoding: regression for the UnicodeEncodeError on a cp1252 console
# --------------------------------------------------------------------------- #


def test_table_falls_back_to_ascii_on_a_cp1252_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    text = format_instance_table(_instances())

    stream.write(text)  # this raised UnicodeEncodeError before the fix
    stream.flush()
    assert "*" in text and "-" in text
    assert "\u25cf" not in text and "\u25cb" not in text


def test_table_keeps_the_circles_on_a_utf8_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stream)
    text = format_instance_table(_instances())
    assert "\u25cf" in text and "\u25cb" in text


def test_explicit_markers_win_over_the_stream() -> None:
    text = format_instance_table(_instances(), markers=("Y", "N"))
    assert "\u25cf" not in text


def test_markers_are_chosen_per_encoding() -> None:
    assert cli._markers("utf-8") == cli.RUN_MARKERS  # noqa: SLF001
    assert cli._markers("cp1252") == cli.ASCII_MARKERS  # noqa: SLF001
    assert cli._markers("no-such-codec") == cli.ASCII_MARKERS  # noqa: SLF001
    assert cli._markers(None) == cli.ASCII_MARKERS  # noqa: SLF001


def test_reconfigure_stdout_switches_a_cp1252_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    cli.reconfigure_stdout()
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_reconfigure_stdout_leaves_a_foreign_stream_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = io.StringIO()
    monkeypatch.setattr(sys, "stdout", sentinel)
    cli.reconfigure_stdout()  # must not raise
    assert sys.stdout is sentinel


def test_list_is_printable_on_a_cp1252_console(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", line_buffering=True)
    _discovery(monkeypatch, _instances())
    monkeypatch.setattr(sys, "stdout", stream)
    assert main(["list"]) == 0  # end to end: this used to abort with a traceback


# --------------------------------------------------------------------------- #
# Selectors
# --------------------------------------------------------------------------- #


def test_select_all() -> None:
    found = _instances()
    assert select_instances(found, every=True) == found


def test_select_by_kind_is_repeatable() -> None:
    found = _instances()
    assert [i.kind.key for i in select_instances(found, kind_keys=["codex"])] == ["codex"]
    assert len(select_instances(found, kind_keys=["claude", "codex"])) == 2


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\Marco\.codex",
        '"C:\\Users\\Marco\\.codex\\"',
        r"c:\users\marco\.codex",
    ],
)
def test_select_by_config_dir_normalizes_the_value(raw: str) -> None:
    assert [i.kind.key for i in select_instances(_instances(), config_dirs=[raw])] == ["codex"]


def test_select_by_id_accepts_both_spellings() -> None:
    found = _instances()
    assert [i.kind.key for i in select_instances(found, instance_id="codex")] == ["codex"]
    assert [i.kind.key for i in select_instances(found, instance_id="CODEX:")] == ["codex"]


def test_select_without_a_selector_matches_nothing() -> None:
    assert select_instances(_instances()) == []


def test_selectors_are_a_union_without_duplicates() -> None:
    found = _instances()
    selected = select_instances(found, kind_keys=["claude"], config_dirs=[str(found[0].config_dir)])
    assert len(selected) == 1


# --------------------------------------------------------------------------- #
# umm restart / stop / start
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["restart", "stop", "start"])
def test_process_commands_require_a_selector(command: str, instances: list[Instance]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


@pytest.mark.parametrize("command", ["restart", "stop", "start"])
def test_no_match_exits_three(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _discovery(monkeypatch, [])
    assert main([command, "--all"]) == EXIT_NO_MATCH
    assert "no instance matched" in capsys.readouterr().out


def test_stop_dry_run_lists_pids_and_mutex(instances: list[Instance], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stop", "--all", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would stop claude pids 32520, 32472" in out
    assert "UsageMonitorForClaude_SingleInstance" in out
    assert "would skip codex: not running" in out


def test_start_dry_run_shows_the_command_line(instances: list[Instance], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["start", "--kind", "codex", "--dry-run"]) == 0
    assert f"would start codex: {CODEX_EXE}" in capsys.readouterr().out


def test_start_dry_run_skips_a_running_instance(instances: list[Instance], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["start", "--kind", "claude", "--dry-run"]) == 0
    assert "would skip claude: already running" in capsys.readouterr().out


def test_restart_dry_run_plans_a_stop_and_a_start(
    instances: list[Instance], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["restart", "--id", "claude", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would stop claude" in out and "would start claude" in out


def test_dry_run_json(instances: list[Instance], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stop", "--all", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["action"] == "stop"
    assert len(payload["plan"]) == 2


def _stop_result(instance: Instance, ok: bool) -> process_control.StopResult:
    return process_control.StopResult(
        instance_id=instance.instance_id,
        kind=instance.kind.key,
        config_dir=instance.config_dir,
        ok=ok,
        detail="stopped" if ok else "mutex still held",
    )


def test_stop_prints_one_line_per_instance(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _discovery(monkeypatch, _instances())
    monkeypatch.setattr(process_control, "stop", lambda instance, **kwargs: _stop_result(instance, True))

    assert main(["stop", "--all"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ok   stop claude: stopped",
        "ok   stop codex: stopped",
    ]


def test_a_failed_stop_exits_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _discovery(monkeypatch, _instances())
    monkeypatch.setattr(
        process_control,
        "stop",
        lambda instance, **kwargs: _stop_result(instance, instance.kind.key != "claude"),
    )

    assert main(["stop", "--all"]) == EXIT_FAILED
    assert "FAIL stop claude: mutex still held" in capsys.readouterr().out


def test_stop_json_emits_the_result_dicts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _discovery(monkeypatch, _instances())
    monkeypatch.setattr(process_control, "stop", lambda instance, **kwargs: _stop_result(instance, True))

    assert main(["stop", "--kind", "claude", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["action"] == "stop" and payload[0]["ok"] is True


def test_start_leaves_running_instances_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _discovery(monkeypatch, _instances())
    seen: list[str] = []

    def fake_start_missing(targets: list[Instance], **kwargs: object) -> list[process_control.StartResult]:
        seen.extend(instance.instance_id for instance in targets)
        return [
            process_control.StartResult(
                instance_id=instance.instance_id,
                kind=instance.kind.key,
                config_dir=instance.config_dir,
                ok=True,
                detail="started (1/2)",
            )
            for instance in targets
        ]

    monkeypatch.setattr(process_control, "start_missing", fake_start_missing)

    assert main(["start", "--all"]) == 0
    assert seen == ["codex"], "the live claude instance must not be launched again"
    out = capsys.readouterr().out
    assert "ok   start claude: already running" in out
    assert "ok   start codex: started (1/2)" in out


def test_restart_reports_stops_then_starts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    found = _instances()
    _discovery(monkeypatch, found)
    target = found[0]
    outcome = process_control.RestartResult(
        stops=(_stop_result(target, True),),
        starts=(
            process_control.StartResult(
                instance_id=target.instance_id,
                kind=target.kind.key,
                config_dir=target.config_dir,
                ok=True,
                detail="started (10/11)",
            ),
        ),
    )
    monkeypatch.setattr(process_control, "restart", lambda targets, **kwargs: outcome)

    assert main(["restart", "--id", "claude"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ok   stop claude: stopped",
        "ok   start claude: started (10/11)",
    ]
