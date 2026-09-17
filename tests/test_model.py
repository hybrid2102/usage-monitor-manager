"""Tests for the domain dataclasses and their JSON projection."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan

STARTED = datetime(2026, 9, 17, 8, 30, tzinfo=UTC)


def _instance() -> Instance:
    return Instance(
        kind=get_kind("claude"),
        config_dir=Path(r"C:\Users\Marco\.claude-valeria"),
        instance_suffix="_578560bbc610",
        exe_path=Path(r"D:\Tools\UsageMonitorForClaude.exe"),
        exe_version="1.22.0",
        exe_mtime=STARTED,
        settings_path=Path(r"C:\Users\Marco\.claude-valeria\usage-monitor-settings.json"),
        settings_exists=True,
        launch_args=("--config-dir=C:\\Users\\Marco\\.claude-valeria",),
        launch_env={"CLAUDE_CONFIG_DIR": r"C:\Users\Marco\.claude-valeria"},
        label="Claude Valeria",
        sources=("process", "run_key"),
        processes=ProcessPair(bootloader_pid=82104, app_pid=93020, started_at=STARTED),
    )


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        tag="v0.3.2",
        version="0.3.2",
        published_at=STARTED,
        html_url="https://github.com/hybrid2102/usage-monitor-for-copilot/releases/tag/v0.3.2",
        body_markdown="- fixes",
        asset=ReleaseAsset(name="UsageMonitorForCopilot.exe", url="https://example/asset", size=123, digest=None),
        sidecar_sha_url="https://example/asset.sha256",
    )


@pytest.mark.parametrize(
    "cls",
    [Instance, ProcessPair, ReleaseAsset, ReleaseInfo, UpdatePlan],
)
def test_dataclasses_are_frozen(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen  # pyright: ignore[reportAttributeAccessIssue]


def test_instance_to_dict_is_json_serializable() -> None:
    data = _instance().to_dict()
    assert data["id"] == "claude_578560bbc610"
    assert data["kind"] == "claude"
    assert data["config_dir"] == r"C:\Users\Marco\.claude-valeria"
    assert data["exe_mtime"] == "2026-09-17T08:30:00+00:00"
    assert data["is_running"] is True
    assert data["processes"]["app_pid"] == 93020
    json.dumps(data)


def test_instance_without_processes() -> None:
    instance = dataclasses.replace(_instance(), processes=None)
    data = instance.to_dict()
    assert instance.is_running is False
    assert data["processes"] is None


def test_release_and_plan_to_dict() -> None:
    plan = UpdatePlan(
        exe_path=Path(r"D:\Tools\UsageMonitorForCopilot.exe"),
        kind=get_kind("copilot"),
        current_version="0.3.1",
        target=_release(),
        affected_instances=(_instance(),),
    )
    data = plan.to_dict()
    assert data["kind"] == "copilot"
    assert data["target"]["asset"]["name"] == "UsageMonitorForCopilot.exe"
    assert data["target"]["published_at"] == "2026-09-17T08:30:00+00:00"
    assert len(data["affected_instances"]) == 1
    json.dumps(data)


def test_kind_to_dict() -> None:
    data = get_kind("codex").to_dict()
    assert data["mutex_base"] == "UsageMonitorForCodex_SingleInstance"
    assert data["run_key_base"] == "UsageMonitorForCodex"
    json.dumps(data)
