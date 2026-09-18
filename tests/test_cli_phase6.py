"""Tests for the Phase 6 CLI: token, self-update, archive, skip, status.

The safety rules of ``test_cli.py`` and ``test_cli_updates.py`` hold here too:
``discover``, the update engine, the network and the credential store are all
replaced, ``LOCALAPPDATA`` points at ``tmp_path`` so ``manager-config.json`` is
a temporary file, and no test starts, stops or updates anything real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import archive, cli, manager_config, secrets, self_update
from usage_monitor_manager.cli import EXIT_FAILED, EXIT_NETWORK, EXIT_UNVERIFIED, EXIT_UPDATES_AVAILABLE, main
from usage_monitor_manager.github import GitHubError
from usage_monitor_manager.integrity import IntegrityResult
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan
from usage_monitor_manager.self_update import MANAGER_REPO, SelfUpdateCheck
from usage_monitor_manager.updater import UpdateResult
from usage_monitor_manager.versions import VersionStatus

pytestmark = pytest.mark.usefixtures("english_cli")
"""Every test in this module asserts the English wording of the CLI."""

COPILOT_EXE = r"D:\Tools\UsageMonitorForCopilot.exe"


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every manager-config.json written by these tests lives in tmp_path."""
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


def _instance(exe_path: str = COPILOT_EXE) -> Instance:
    return Instance(
        kind=get_kind("copilot"),
        config_dir=Path(r"C:\Users\Marco\.copilot"),
        instance_suffix="",
        exe_path=Path(exe_path),
        exe_version="0.3.1.0",
        processes=ProcessPair(bootloader_pid=32440, app_pid=17540),
    )


def _plan(*, tag: str = "v0.3.2", version: str = "0.3.2") -> UpdatePlan:
    return UpdatePlan(
        exe_path=Path(COPILOT_EXE),
        kind=get_kind("copilot"),
        current_version="0.3.1.0",
        target=ReleaseInfo(
            tag=tag,
            version=version,
            published_at=datetime(2026, 9, 17, tzinfo=UTC),
            html_url="https://example.invalid/release",
            body_markdown="- fix",
            asset=ReleaseAsset(name="UsageMonitorForCopilot.exe", url="https://e/m.exe", size=10, digest="a" * 64),
        ),
        affected_instances=(_instance(),),
    )


@pytest.fixture
def plans(monkeypatch: pytest.MonkeyPatch) -> list[UpdatePlan]:
    built = [_plan()]
    monkeypatch.setattr(cli, "build_plans", lambda kinds, *, prerelease: list(built))
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: [_instance()])
    return built


# --------------------------------------------------------------------------- #
# umm token
# --------------------------------------------------------------------------- #


def test_token_status_reports_absence(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    for name in secrets.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert main(["token", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["present"] is False
    assert payload["source"] == "none"
    assert payload["target"] == secrets.DEFAULT_TARGET


def test_token_set_reads_stdin_and_never_echoes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_read_token_from_stdin", lambda: "ghp_from_stdin\n")
    assert main(["token", "set"]) == 0
    out = capsys.readouterr().out
    assert "ghp_from_stdin" not in out  # the value never reaches the terminal
    assert secrets.DEFAULT_TARGET in out
    assert secrets.get_token() == "ghp_from_stdin"


def test_token_set_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "ghp_from_env")
    assert main(["token", "set", "--from-env", "MY_TOKEN"]) == 0
    assert secrets.get_token() == "ghp_from_env"


def test_token_set_from_a_missing_env_var_fails(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MY_TOKEN", raising=False)
    assert main(["token", "set", "--from-env", "MY_TOKEN"]) == EXIT_FAILED
    assert "MY_TOKEN is empty" in capsys.readouterr().err
    assert secrets.has_token() is False


def test_token_set_with_empty_stdin_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_read_token_from_stdin", lambda: "\n")
    assert main(["token", "set"]) == EXIT_FAILED
    assert secrets.has_token() is False


def test_token_clear(capsys: pytest.CaptureFixture[str]) -> None:
    secrets.set_token("ghp_x")
    assert main(["token", "clear"]) == 0
    assert "rimosso" in capsys.readouterr().out or secrets.has_token() is False
    assert secrets.has_token() is False


def test_the_token_never_travels_through_argv() -> None:
    """``umm token set`` takes no positional argument that could hold a secret."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["token", "set", "ghp_leaked"])


# --------------------------------------------------------------------------- #
# umm skip / unskip / check
# --------------------------------------------------------------------------- #


def test_skip_records_the_tag_and_check_reports_it(plans: list[UpdatePlan], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == EXIT_UPDATES_AVAILABLE
    capsys.readouterr()

    assert main(["skip", "--kind", "copilot"]) == 0
    stored = manager_config.load().skipped_versions
    assert stored[get_kind("copilot").repo] == "v0.3.2"

    assert main(["check"]) == 0  # skipped: no exit 10 any more
    assert "skipped" in capsys.readouterr().out

    assert main(["check", "--include-skipped"]) == EXIT_UPDATES_AVAILABLE
    assert "update_available" in capsys.readouterr().out

    assert main(["unskip", "--kind", "copilot"]) == 0
    assert manager_config.load().skipped_versions == {}
    assert main(["check"]) == EXIT_UPDATES_AVAILABLE


def test_check_json_marks_the_skipped_plan(plans: list[UpdatePlan], capsys: pytest.CaptureFixture[str]) -> None:
    main(["skip", "--kind", "copilot"])
    capsys.readouterr()
    assert main(["check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "skipped"
    assert payload[0]["skipped"] is True


def test_skip_manager_uses_the_self_update_check(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        self_update,
        "check_self",
        lambda **kwargs: SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version="0.7.0",
            detail="disponibile",
            release=ReleaseInfo(
                tag="v0.8.0",
                version="0.8.0",
                published_at=None,
                html_url="",
                body_markdown="",
            ),
        ),
    )
    assert main(["skip", "--kind", "manager"]) == 0
    assert manager_config.load().skipped_versions[MANAGER_REPO] == "v0.8.0"


def test_skip_reports_a_network_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def explode(kinds: object, *, prerelease: bool) -> list[UpdatePlan]:
        raise GitHubError("cannot reach GitHub")

    monkeypatch.setattr(cli, "build_plans", explode)
    assert main(["skip", "--kind", "copilot"]) == EXIT_NETWORK
    assert "cannot reach GitHub" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# umm update, unattended
# --------------------------------------------------------------------------- #


def _result(*, ok: bool, unverifiable: bool = False, stopped: tuple[str, ...] = ()) -> UpdateResult:
    return UpdateResult(
        exe_path=Path(COPILOT_EXE),
        kind="copilot",
        ok=ok,
        detail="done" if ok else "aborted",
        from_version="0.3.1",
        to_version="0.3.2",
        integrity=IntegrityResult(hash_verified=False, source="none") if unverifiable else None,
        stopped=stopped,
    )


def test_unattended_update_returns_4_for_an_unverifiable_asset(
    plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "usage_monitor_manager.updater.apply", lambda plan, **kwargs: _result(ok=False, unverifiable=True)
    )
    assert main(["update", "--all", "--yes"]) == EXIT_UNVERIFIED


def test_unattended_update_returns_1_for_a_real_failure(
    plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "usage_monitor_manager.updater.apply",
        lambda plan, **kwargs: _result(ok=False, stopped=("copilot",)),
    )
    assert main(["update", "--all", "--yes"]) == EXIT_FAILED


def test_unattended_update_returns_3_on_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(kinds: object, *, prerelease: bool) -> list[UpdatePlan]:
        raise GitHubError("cannot reach GitHub")

    monkeypatch.setattr(cli, "build_plans", explode)
    assert main(["update", "--all", "--yes"]) == EXIT_NETWORK


def test_unattended_update_writes_one_json_object_per_step(
    plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from usage_monitor_manager.updater import UpdateEvent

    def apply(plan: UpdatePlan, **kwargs: object) -> UpdateResult:
        on_event = kwargs.get("on_event")
        if callable(on_event):
            on_event(UpdateEvent(step="download", message="downloading", progress=0.5))
            on_event(UpdateEvent(step="swap", message="swapped"))
        return _result(ok=True)

    monkeypatch.setattr("usage_monitor_manager.updater.apply", apply)
    log_file = tmp_path / "logs" / "umm.jsonl"
    assert main(["update", "--all", "--yes", "--log", str(log_file)]) == 0

    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert [entry["step"] for entry in lines] == ["download", "swap", "result"]
    assert all(entry["ts"] and entry["kind"] == "copilot" for entry in lines)
    assert lines[0]["progress"] == 0.5
    assert lines[-1]["ok"] is True


def test_unattended_update_never_prompts(plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_confirm", lambda question: pytest.fail("--yes must never prompt"))
    monkeypatch.setattr("usage_monitor_manager.updater.apply", lambda plan, **kwargs: _result(ok=True))
    assert main(["update", "--all", "--yes"]) == 0


def test_a_skipped_release_is_not_updated_unless_forced(
    plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    applied: list[str] = []
    monkeypatch.setattr(
        "usage_monitor_manager.updater.apply",
        lambda plan, **kwargs: applied.append(plan.kind.key) or _result(ok=True),
    )
    main(["skip", "--kind", "copilot"])
    capsys.readouterr()

    assert main(["update", "--all", "--yes"]) == 0
    assert applied == []
    assert "skipped by the user" in capsys.readouterr().out

    assert main(["update", "--all", "--yes", "--include-skipped"]) == 0
    assert applied == ["copilot"]


# --------------------------------------------------------------------------- #
# umm archive / rollback --version
# --------------------------------------------------------------------------- #


@pytest.fixture
def archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = tmp_path / "UsageMonitorForCopilot.exe"
    exe.write_text("current", encoding="utf-8")
    for version in ("0.3.0", "0.3.1"):
        exe.with_name(f"{exe.name}.bak").write_text(f"build {version}", encoding="utf-8")
        archive.archive_exe(exe, version)
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: [_instance(str(exe))])
    return exe


def test_archive_list(archived: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["archive", "list", "--kind", "copilot"]) == 0
    out = capsys.readouterr().out
    assert "0.3.1" in out and "0.3.0" in out
    assert "VERSION" in out


def test_archive_list_json(archived: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["archive", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["version"] for row in rows] == ["0.3.1", "0.3.0"]
    assert rows[0]["kind"] == "copilot"


def test_archive_prune(archived: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["archive", "prune", "--keep", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["version"] for entry in payload["removed"]] == ["0.3.0"]
    assert [item.version for item in archive.list_archived(archived)] == ["0.3.1"]


def test_rollback_with_a_version_uses_the_archive(archived: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def restore(exe_path: Path, version: str | None = None, **kwargs: object) -> UpdateResult:
        calls.append((str(exe_path), version))
        return _result(ok=True)

    monkeypatch.setattr(archive, "restore", restore)
    monkeypatch.setattr(
        "usage_monitor_manager.updater.rollback",
        lambda *a, **k: pytest.fail("--version must go through the archive"),
    )
    assert main(["rollback", "--kind", "copilot", "--version", "0.3.0"]) == 0
    assert calls == [(str(archived), "0.3.0")]


def test_rollback_without_a_version_falls_back_to_the_archive(archived: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``.bak`` left, but the archive has the previous build."""
    calls: list[str | None] = []
    monkeypatch.setattr(
        archive,
        "restore",
        lambda exe_path, version=None, **kwargs: calls.append(version) or _result(ok=True),
    )
    assert main(["rollback", "--kind", "copilot"]) == 0
    assert calls == [None]  # "the previous one"


# --------------------------------------------------------------------------- #
# umm self-update / umm status / --post-update
# --------------------------------------------------------------------------- #


def test_self_update_check_only_without_a_release(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        self_update,
        "check_self",
        lambda **kwargs: SelfUpdateCheck(
            status=VersionStatus.UNKNOWN,
            current_version="0.7.0",
            detail=f"nessuna release pubblicata per {MANAGER_REPO}",
        ),
    )
    assert main(["self-update", "--check-only"]) == 0
    assert "nessuna release pubblicata" in capsys.readouterr().out


def test_self_update_check_only_with_a_release(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        self_update,
        "check_self",
        lambda **kwargs: SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version="0.7.0",
            detail="0.8.0 disponibile",
            release=ReleaseInfo(tag="v0.8.0", version="0.8.0", published_at=None, html_url="", body_markdown=""),
        ),
    )
    assert main(["self-update", "--check-only", "--json"]) == EXIT_UPDATES_AVAILABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is True
    assert payload["target_version"] == "0.8.0"


def test_self_update_refuses_from_source(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        self_update,
        "check_self",
        lambda **kwargs: SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version="0.7.0",
            detail="0.8.0 disponibile",
            release=ReleaseInfo(tag="v0.8.0", version="0.8.0", published_at=None, html_url="", body_markdown=""),
        ),
    )
    monkeypatch.setattr(self_update, "is_frozen", lambda: False)
    monkeypatch.setattr(
        self_update,
        "apply_self",
        lambda *a, **k: pytest.fail("a source checkout must never reach apply_self"),
    )
    assert main(["self-update", "--yes"]) == EXIT_FAILED
    assert "da sorgente" in capsys.readouterr().err


def test_status_json_is_offline_by_default(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: [_instance()])
    monkeypatch.setattr(
        cli,
        "build_plans",
        lambda kinds, *, prerelease: pytest.fail("umm status must not check GitHub without --check"),
    )
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] == 1
    assert payload["instances"][0]["id"] == "copilot"
    assert payload["updates"]["checked"] is False
    assert payload["watchdog"]["count"] == 0
    assert payload["self_update"] is None


def test_status_with_check_asks_github(
    capsys: pytest.CaptureFixture[str], plans: list[UpdatePlan], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(
        self_update,
        "check_self",
        lambda **kwargs: SelfUpdateCheck(status=VersionStatus.UNKNOWN, current_version="0.7.0", detail="nessuna"),
    )
    assert main(["status", "--json", "--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["updates"]["checked"] is True
    assert payload["updates"]["available"] == 1
    assert payload["updates"]["plans"][0]["status"] == "update_available"
    assert payload["self_update"]["status"] == "unknown"


def test_status_text_output(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: [_instance()])
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1/1 running" in out
    assert "watchdog: nothing anomalous" in out


def test_post_update_waits_then_becomes_the_tray(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(
        self_update,
        "finish_post_update",
        lambda pid, **kwargs: (
            order.append(f"wait:{pid}")
            or self_update.PostUpdateResult(ok=True, detail="ok", old_pid=pid, message="Manager aggiornato alla v0.7.0")
        ),
    )

    def run_tray(config_path: object = None, **kwargs: object) -> int:
        order.append(f"tray:{kwargs.get('messages')}")
        assert kwargs["wait_for_mutex"] == self_update.DEFAULT_WAIT_TIMEOUT
        return 0

    monkeypatch.setattr("usage_monitor_manager.ui.tray.run_tray", run_tray)
    assert main(["--post-update", "4321"]) == 0
    assert order == ["wait:4321", "tray:['Manager aggiornato alla v0.7.0']"]
