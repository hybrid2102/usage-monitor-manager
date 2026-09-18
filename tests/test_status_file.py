"""Tests for the health snapshot and ``status.json`` (PLAN.md section 9.11).

Two separate promises are checked, because two different consumers depend on
them:

* the **schema** of the document (``docs/status-file.md``): a statusline script
  reads ``generated_at``, ``running``, ``instances[]`` and
  ``updates.available`` and must not have to guess whether a key exists. The
  offline build must also stay offline - the autouse ``no_network`` fixture of
  ``conftest.py`` fails the test if it is not.
* the **write**, which has to be atomic: the reader runs every few seconds and
  would happily parse a half-written file.

``discover`` and the update engine are faked throughout; nothing is started,
stopped or updated.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import __version__, cli, manager_config, status_file
from usage_monitor_manager.github import GitHubError
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan
from usage_monitor_manager.self_update import SelfUpdateCheck
from usage_monitor_manager.versions import VersionStatus

COPILOT_EXE = Path(r"D:\Tools\UsageMonitorForCopilot.exe")
AGENT_EXE = Path(r"D:\Tools\AgentMonitorForClaude.exe")


def _self_check() -> SelfUpdateCheck:
    """A self-update check that says "nothing new", without asking GitHub."""
    return SelfUpdateCheck(
        status=VersionStatus.UP_TO_DATE,
        current_version=__version__,
        detail="the manager is up to date",
    )


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


def _copilot(*, running: bool = True) -> Instance:
    return Instance(
        kind=get_kind("copilot"),
        config_dir=Path(r"C:\Users\Marco\.copilot"),
        instance_suffix="",
        exe_path=COPILOT_EXE,
        exe_version="0.3.1.0",
        processes=ProcessPair(bootloader_pid=32440, app_pid=17540) if running else None,
    )


def _agent() -> Instance:
    return Instance(
        kind=get_kind("agent-monitor-claude"),
        config_dir=AGENT_EXE.parent,
        instance_suffix="",
        exe_path=AGENT_EXE,
        exe_version="0.8.0.0",
    )


def _plan() -> UpdatePlan:
    return UpdatePlan(
        exe_path=COPILOT_EXE,
        kind=get_kind("copilot"),
        current_version="0.3.1.0",
        target=ReleaseInfo(
            tag="v0.3.2",
            version="0.3.2",
            published_at=datetime(2026, 9, 17, tzinfo=UTC),
            html_url="https://example.invalid/release",
            body_markdown="- fix",
            asset=ReleaseAsset(name="UsageMonitorForCopilot.exe", url="https://e/m.exe", size=10, digest="a" * 64),
        ),
        affected_instances=(_copilot(),),
    )


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> list[Instance]:
    """Two instances - one monitor running, one portable exe dormant."""
    instances = [_copilot(), _agent()]
    monkeypatch.setattr("usage_monitor_manager.discovery.discover", lambda *a, **k: list(instances))
    return instances


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def test_the_default_path_is_in_the_manager_directory(local_appdata: Path) -> None:
    assert status_file.DEFAULT_FILENAME == "status.json"
    assert status_file.default_status_path() == local_appdata / "UsageMonitorManager" / "status.json"
    assert status_file.resolve_path(None) == status_file.default_status_path()
    assert status_file.resolve_path("   ") == status_file.default_status_path()


def test_a_directory_gets_the_default_file_name_appended(tmp_path: Path) -> None:
    assert status_file.resolve_path(str(tmp_path)) == tmp_path / "status.json"
    assert status_file.resolve_path(f"{tmp_path}\\") == tmp_path / "status.json"
    assert status_file.resolve_path(str(tmp_path / "custom.json")) == tmp_path / "custom.json"


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #


def test_the_offline_document_has_the_documented_schema(machine: list[Instance]) -> None:
    payload = status_file.build_status()

    assert set(payload) == {
        "version",
        "generated_at",
        "instances",
        "running",
        "updates",
        "self_update",
        "watchdog",
    }
    assert payload["version"] == __version__
    generated_at = datetime.fromisoformat(str(payload["generated_at"]))
    assert generated_at.tzinfo is not None, "generated_at is always an aware ISO timestamp"
    assert payload["running"] == 1
    assert payload["self_update"] is None, "the offline document asks GitHub nothing"

    rows = payload["instances"]
    assert isinstance(rows, list)
    assert len(rows) == len(machine)
    for row in rows:
        assert set(row) == {
            "id",
            "kind",
            "family",
            "label",
            "config_dir",
            "exe_path",
            "local_version",
            "is_running",
            "pid",
        }
    monitor, agent = rows
    assert monitor["id"] == "copilot"
    assert monitor["family"] == "usage-monitor"
    assert monitor["config_dir"] == str(Path(r"C:\Users\Marco\.copilot"))
    assert monitor["local_version"] == "0.3.1"
    assert monitor["is_running"] is True
    assert monitor["pid"] == 17540
    assert agent["family"] == "github-exe"
    assert agent["config_dir"] is None, "a portable exe has no config dir to report"
    assert agent["is_running"] is False
    assert agent["pid"] is None

    updates = payload["updates"]
    assert set(updates) == {"checked", "last_check", "skipped_versions", "plans", "available", "error"}
    assert updates["checked"] is False
    assert updates["plans"] == []
    assert updates["available"] == 0
    assert updates["error"] is None

    assert set(payload["watchdog"]) >= {"count", "events"}


def test_the_document_is_json_serializable(machine: list[Instance]) -> None:
    text = json.dumps(status_file.build_status(), indent=2, ensure_ascii=False)
    assert json.loads(text)["version"] == __version__


def test_labels_come_from_the_manager_config(machine: list[Instance]) -> None:
    config = manager_config.load()
    manager_config.save(
        manager_config.ManagerConfig(
            instances=(
                manager_config.DeclaredInstance(
                    kind="copilot",
                    config_dir=Path(r"C:\Users\Marco\.copilot"),
                    label="Copilot Lavoro",
                ),
            ),
            preferences=config.preferences,
        )
    )
    payload = status_file.build_status()
    assert payload["instances"][0]["label"] == "Copilot Lavoro"


def test_with_check_the_plans_and_the_self_update_state_are_included(
    machine: list[Instance], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "build_plans", lambda kinds, *, prerelease: [_plan()])
    monkeypatch.setattr(
        "usage_monitor_manager.self_update.check_self",
        lambda **_: _self_check(),
    )

    updates = status_file.build_status(check=True)["updates"]
    assert updates["checked"] is True
    assert updates["available"] == 1
    assert updates["error"] is None
    plan_row = updates["plans"][0]
    assert set(plan_row) == {"kind", "exe_path", "current_version", "target_version", "tag", "status"}
    assert plan_row["kind"] == "copilot"
    assert plan_row["status"] == "update_available"
    assert plan_row["target_version"] == "0.3.2"
    assert status_file.build_status(check=True)["self_update"] is not None


def test_a_skipped_version_is_reported_as_skipped_and_not_counted(
    machine: list[Instance], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "build_plans", lambda kinds, *, prerelease: [_plan()])
    monkeypatch.setattr(
        "usage_monitor_manager.self_update.check_self",
        lambda **_: _self_check(),
    )
    manager_config.skip_version("hybrid2102/usage-monitor-for-copilot", "v0.3.2")

    updates = status_file.build_status(check=True)["updates"]
    assert updates["plans"][0]["status"] == "skipped"
    assert updates["available"] == 0
    assert updates["skipped_versions"]["hybrid2102/usage-monitor-for-copilot"] == "v0.3.2"


def test_a_github_failure_lands_in_the_error_field(machine: list[Instance], monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: object, **__: object) -> list[UpdatePlan]:
        raise GitHubError("rate limit exceeded")

    monkeypatch.setattr(cli, "build_plans", explode)
    monkeypatch.setattr(
        "usage_monitor_manager.self_update.check_self",
        lambda **_: _self_check(),
    )

    payload = status_file.build_status(check=True)
    assert payload["updates"]["error"] == "rate limit exceeded"
    assert payload["updates"]["plans"] == []
    assert payload["updates"]["available"] == 0


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def test_write_creates_the_directory_and_the_file(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "status.json"
    written = status_file.write_status_file({"version": "0.8.0", "running": 1}, target)
    assert written == target
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == "0.8.0"
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_write_stamps_generated_at_when_the_payload_has_none(tmp_path: Path) -> None:
    written = status_file.write_status_file({"running": 0}, tmp_path / "status.json")
    stamp = json.loads(written.read_text(encoding="utf-8"))["generated_at"]
    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_write_keeps_a_generated_at_that_is_already_there(tmp_path: Path) -> None:
    given = "2026-09-18T09:00:00+00:00"
    written = status_file.write_status_file({"generated_at": given}, tmp_path / "status.json")
    assert json.loads(written.read_text(encoding="utf-8"))["generated_at"] == given


def test_write_does_not_mutate_the_payload_it_was_given(tmp_path: Path) -> None:
    payload: dict[str, object] = {"running": 0}
    status_file.write_status_file(payload, tmp_path / "status.json")
    assert payload == {"running": 0}


def test_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    status_file.write_status_file({"n": 1}, target)
    status_file.write_status_file({"n": 2}, target)
    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 2
    assert [path.name for path in tmp_path.iterdir()] == ["status.json"]


def test_a_failed_write_leaves_the_previous_document_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "status.json"
    status_file.write_status_file({"n": 1}, target)

    def refuse(src: object, dst: object) -> None:
        raise OSError("held by another process")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="held by another process"):
        status_file.write_status_file({"n": 2}, target)

    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 1
    assert [path.name for path in tmp_path.iterdir()] == ["status.json"], "the temp file is cleaned up"


def test_write_defaults_to_the_configured_default_path(local_appdata: Path) -> None:
    written = status_file.write_status_file({"running": 0})
    assert written == local_appdata / "UsageMonitorManager" / "status.json"
    assert written.is_file()


def test_the_built_document_round_trips_through_the_file(machine: list[Instance], tmp_path: Path) -> None:
    payload = status_file.build_status()
    written = status_file.write_status_file(payload, tmp_path / "status.json")
    assert json.loads(written.read_text(encoding="utf-8")) == payload
