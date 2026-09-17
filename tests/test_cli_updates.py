"""Tests for ``umm check`` / ``umm update`` / ``umm rollback`` (Phase 3).

Safety rule, as in ``test_cli.py``: nothing here may reach the network, a
process or a file. ``discover``, ``updater.plan_updates``, ``updater.apply``
and ``updater.rollback`` are monkeypatched in every test; the ones that must
not be called at all are replaced by a recorder that fails the test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import cli
from usage_monitor_manager.cli import EXIT_FAILED, EXIT_NO_MATCH, build_parser, main
from usage_monitor_manager.github import GitHubError, RateLimitedError
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan
from usage_monitor_manager.updater import UpdateEvent, UpdateResult

COPILOT_EXE = r"D:\Tools\UsageMonitorForCopilot.exe"

CHANGELOG = """### Fixed

- The usage stopped updating, see [#12](https://github.com/x/y/issues/12)
- ![screenshot](https://example.com/shot.png) the popup keeps its place

```powershell
Update-UsageMonitor.ps1 -Kind copilot
```
"""


def _forbidden(*args: object, **kwargs: object) -> None:
    pytest.fail("this test must not reach the real update engine")


def _release(tag: str, version: str, *, body: str = CHANGELOG, digest: str | None = None) -> ReleaseInfo:
    return ReleaseInfo(
        tag=tag,
        version=version,
        published_at=datetime(2026, 9, 17, 10, 15, tzinfo=UTC),
        html_url=f"https://github.com/hybrid2102/usage-monitor-for-copilot/releases/tag/{tag}",
        body_markdown=body,
        asset=ReleaseAsset(name="UsageMonitorForCopilot.exe", url="https://e/m.exe", size=15_260_446, digest=digest),
    )


def _copilot_instance() -> Instance:
    return Instance(
        kind=get_kind("copilot"),
        config_dir=Path(r"C:\Users\Marco\.copilot"),
        instance_suffix="",
        exe_path=Path(COPILOT_EXE),
        exe_version="0.3.1.0",
        processes=ProcessPair(bootloader_pid=32440, app_pid=17540),
    )


def _plans(*, outdated: bool = True) -> list[UpdatePlan]:
    return [
        UpdatePlan(
            exe_path=Path(COPILOT_EXE),
            kind=get_kind("copilot"),
            current_version="0.3.1.0" if outdated else "0.3.2.0",
            target=_release("v0.3.2", "0.3.2"),
            affected_instances=(_copilot_instance(),),
        )
    ]


def _freeze(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plans: list[UpdatePlan] | None = None,
    error: Exception | None = None,
    instances: list[Instance] | None = None,
) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda include_dormant=True: list(instances or []))

    def plan_updates(found: object, **kwargs: object) -> list[UpdatePlan]:
        if error is not None:
            raise error
        return list(plans or [])

    monkeypatch.setattr("usage_monitor_manager.updater.plan_updates", plan_updates)
    monkeypatch.setattr("usage_monitor_manager.updater.apply", _forbidden)
    monkeypatch.setattr("usage_monitor_manager.updater.rollback", _forbidden)


# --------------------------------------------------------------------------- #
# umm check
# --------------------------------------------------------------------------- #


def test_check_table_and_exit_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans())
    assert main(["check"]) == cli.EXIT_UPDATES_AVAILABLE
    out = capsys.readouterr().out
    assert "KIND" in out and "LATEST" in out and "PUBLISHED" in out
    assert "0.3.1  " in out and "0.3.2" in out
    assert "0.3.1.0" not in out  # display_version drops the PE padding
    assert "update_available" in out
    assert "2026-09-17" in out


def test_check_exits_zero_when_up_to_date(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans(outdated=False))
    assert main(["check"]) == 0
    assert "up_to_date" in capsys.readouterr().out


def test_check_with_nothing_to_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=[])
    assert main(["check"]) == 0
    assert "no usage-monitor executable" in capsys.readouterr().out


def test_check_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans())
    assert main(["check", "--json"]) == cli.EXIT_UPDATES_AVAILABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "update_available"
    assert payload[0]["target"]["tag"] == "v0.3.2"
    assert payload[0]["affected_instances"][0]["kind"] == "copilot"


def test_check_changelog_renders_plain_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, plans=_plans())
    main(["check", "--changelog"])
    out = capsys.readouterr().out
    assert "What's new in Copilot v0.3.2" in out
    assert "#12 (https://github.com/x/y/issues/12)" in out
    assert "screenshot the popup keeps its place" in out
    assert "https://example.com/shot.png" not in out  # image URL dropped, alt text kept
    assert "```" not in out
    assert "Update-UsageMonitor.ps1" in out  # the code block content survives


def test_check_maps_a_network_failure_to_exit_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, error=GitHubError("cannot reach GitHub (api.github.com): getaddrinfo failed"))
    assert main(["check"]) == cli.EXIT_NETWORK
    assert "cannot reach GitHub" in capsys.readouterr().err


def test_check_maps_a_rate_limit_to_exit_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, error=RateLimitedError("GitHub API rate limit exhausted; it resets at 12:40:00", None))
    assert main(["check"]) == cli.EXIT_NETWORK
    assert "rate limit" in capsys.readouterr().err


def test_check_accepts_repeated_kinds() -> None:
    args = build_parser().parse_args(["check", "--kind", "copilot", "--kind", "codex", "--changelog"])
    assert args.kind == ["copilot", "codex"]
    assert args.changelog is True


# --------------------------------------------------------------------------- #
# umm update
# --------------------------------------------------------------------------- #


def test_update_requires_a_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, plans=_plans())
    with pytest.raises(SystemExit) as exc:
        main(["update"])
    assert exc.value.code == 2


def test_update_dry_run_prints_every_step(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans())
    assert main(["update", "--kind", "copilot", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "What's new in Copilot v0.3.2" in out
    assert "would update Copilot 0.3.1 -> 0.3.2" in out
    assert "would download UsageMonitorForCopilot.exe" in out
    assert "would stop copilot" in out
    assert "would start copilot" in out
    assert "explicit confirmation" in out  # this release has neither digest nor sidecar


def test_update_dry_run_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans())
    assert main(["update", "--all", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["plans"][0]["kind"] == "copilot"
    assert any("would download" in step for step in payload["plans"][0]["steps"])


def test_update_says_nothing_to_do(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _freeze(monkeypatch, plans=_plans(outdated=False))
    assert main(["update", "--all"]) == 0
    assert "nothing to update" in capsys.readouterr().out


def test_update_network_failure_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, error=GitHubError("timed out after 20s waiting for GitHub"))
    assert main(["update", "--all"]) == cli.EXIT_NETWORK
    assert "timed out" in capsys.readouterr().err


def test_update_applies_the_plan(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: list[dict[str, object]] = []

    def fake_apply(plan: UpdatePlan, **kwargs: object) -> UpdateResult:
        captured.append(kwargs)
        return UpdateResult(
            exe_path=plan.exe_path,
            kind=plan.kind.key,
            ok=True,
            detail="updated to 0.3.2: version verified and every restarted instance is running",
            to_version="0.3.2",
            restarted=("copilot",),
        )

    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("usage_monitor_manager.updater.apply", fake_apply)

    assert main(["update", "--kind", "copilot", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "What's new in Copilot v0.3.2" in out  # changelog before the download
    assert out.index("What's new") < out.index("updated to 0.3.2")
    assert "ok   copilot" in out
    assert captured[0]["restart"] is True
    confirm = captured[0]["confirm_unverified"]
    assert callable(confirm) and confirm() is True  # --yes also accepts an unverifiable asset


def test_update_json_reports_the_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_apply(plan: UpdatePlan, **kwargs: object) -> UpdateResult:
        return UpdateResult(exe_path=plan.exe_path, kind=plan.kind.key, ok=True, detail="done", to_version="0.3.2")

    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("usage_monitor_manager.updater.apply", fake_apply)

    assert main(["update", "--all", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["ok"] is True
    assert payload[0]["to_version"] == "0.3.2"


def test_update_honours_no_restart_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[dict[str, object]] = []

    def fake_apply(plan: UpdatePlan, **kwargs: object) -> UpdateResult:
        captured.append(kwargs)
        return UpdateResult(
            exe_path=plan.exe_path,
            kind=plan.kind.key,
            ok=False,
            detail="the post-update check failed: restarted but not running after 5s: copilot",
            rollback_available=True,
        )

    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("usage_monitor_manager.updater.apply", fake_apply)

    assert main(["update", "--all", "--yes", "--no-restart"]) == EXIT_FAILED
    assert captured[0]["restart"] is False
    assert "FAIL copilot" in capsys.readouterr().out


def test_update_skips_when_the_user_says_no(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert main(["update", "--kind", "copilot"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_update_proceeds_when_the_user_says_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_apply(plan: UpdatePlan, **kwargs: object) -> UpdateResult:
        return UpdateResult(exe_path=plan.exe_path, kind=plan.kind.key, ok=True, detail="done")

    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("usage_monitor_manager.updater.apply", fake_apply)
    monkeypatch.setattr("builtins.input", lambda prompt="": "Y")
    assert main(["update", "--kind", "copilot"]) == 0


def test_a_non_interactive_stdin_counts_as_no(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(prompt: str = "") -> str:
        raise EOFError

    _freeze(monkeypatch, plans=_plans())
    monkeypatch.setattr("builtins.input", eof)
    assert main(["update", "--kind", "copilot"]) == 0  # skipped, and apply is _forbidden


# --------------------------------------------------------------------------- #
# Progress rendering
# --------------------------------------------------------------------------- #


def test_progress_is_one_redrawn_line_on_a_tty(capsys: pytest.CaptureFixture[str]) -> None:
    printer = cli._ProgressPrinter(tty=True)
    printer(UpdateEvent(step="download", message="m.exe", progress=0.5))
    printer(UpdateEvent(step="verify", message="sha256 matches"))
    out = capsys.readouterr().out
    assert out.startswith("\r  downloading  50%")
    assert "sha256 matches" in out


def test_progress_is_plain_throttled_lines_without_a_tty(capsys: pytest.CaptureFixture[str]) -> None:
    printer = cli._ProgressPrinter(tty=False)
    for fraction in (0.0, 0.05, 0.1, 0.3, 0.9, 1.0):
        printer(UpdateEvent(step="download", message="m.exe", progress=fraction))
    out = capsys.readouterr().out
    assert "\r" not in out
    assert 0 < out.count("downloading") < 6  # throttled, not one line per chunk
    assert "downloading 100%" in out


# --------------------------------------------------------------------------- #
# umm rollback
# --------------------------------------------------------------------------- #


def test_rollback_requires_a_kind() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["rollback"])
    assert exc.value.code == 2


def test_rollback_calls_the_updater_once_per_exe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Path] = []

    def fake_rollback(target: object, **kwargs: object) -> UpdateResult:
        seen.append(Path(str(target)))
        return UpdateResult(exe_path=Path(str(target)), kind="copilot", ok=True, detail="rolled back to 0.3.1")

    _freeze(monkeypatch, instances=[_copilot_instance(), _copilot_instance()])  # same exe twice
    monkeypatch.setattr("usage_monitor_manager.updater.rollback", fake_rollback)

    assert main(["rollback", "--kind", "copilot"]) == 0
    assert seen == [Path(COPILOT_EXE)]  # grouped by executable
    assert "rolled back to 0.3.1" in capsys.readouterr().out


def test_rollback_reports_a_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_rollback(target: object, **kwargs: object) -> UpdateResult:
        return UpdateResult(
            exe_path=Path(str(target)), kind="copilot", ok=False, detail="nothing to roll back: no .bak"
        )

    _freeze(monkeypatch, instances=[_copilot_instance()])
    monkeypatch.setattr("usage_monitor_manager.updater.rollback", fake_rollback)

    assert main(["rollback", "--kind", "copilot"]) == EXIT_FAILED
    assert "nothing to roll back" in capsys.readouterr().out


def test_rollback_without_a_matching_instance_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _freeze(monkeypatch, instances=[])
    assert main(["rollback", "--kind", "codex"]) == EXIT_NO_MATCH
    assert "no Codex executable" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #


def test_markdown_link_conversion() -> None:
    assert cli.convert_markdown_links("see [docs](https://x/y)") == "see docs (https://x/y)"
    assert cli.convert_markdown_links("![alt](https://x/i.png) tail") == "alt tail"
    assert cli.convert_markdown_links("no links here") == "no links here"
    assert cli.convert_markdown_links("[bare]()") == "bare"


def test_render_changelog_of_an_empty_body() -> None:
    assert "no release notes" in "\n".join(cli.render_changelog("   "))


def test_render_changelog_structure() -> None:
    lines = cli.render_changelog("# Title\r\n\r\n- one\n* two\nplain\n")
    assert "  Title" in lines
    assert "  - one" in lines and "  - two" in lines
    assert "  plain" in lines
