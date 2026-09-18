"""``umm config``: show, validate, set/unset, create, export/import, history.

``discovery.discover`` is faked and every path is under ``tmp_path``; no real
settings file and no real monitor is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from usage_monitor_manager import discovery, history
from usage_monitor_manager.cli import EXIT_FAILED, EXIT_NO_MATCH, EXIT_OK, main, parse_setting_value
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance
from usage_monitor_manager.settings import store


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Instance:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    settings = home / ".claude" / "usage-monitor-settings.json"
    settings.write_text('{"bar_fg": "#4a9eff", "mia_chiave": 1}', encoding="utf-8")
    kind = get_kind("claude")
    built = Instance(
        kind=kind,
        config_dir=home / ".claude",
        instance_suffix=config_dir_suffix(home / ".claude", kind.default_config_dir),
        exe_path=tmp_path / "UsageMonitorForClaude.exe",
        settings_path=settings,
        settings_exists=True,
    )
    monkeypatch.setattr(discovery, "discover", lambda *a, **k: [built])
    return built


def test_show_prints_the_file(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "show", "--instance", "claude"]) == EXIT_OK
    out = capsys.readouterr().out
    assert '"bar_fg": "#4a9eff"' in out


def test_show_json(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "show", "--instance", "claude", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["mia_chiave"] == 1
    assert payload["exists"] is True


def test_validate_reports_unknown_keys(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "validate", "--instance", "claude"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "valido" in out
    assert "mia_chiave" in out


def test_validate_fails_on_a_broken_value(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.settings_path is not None
    instance.settings_path.write_text('{"poll_interval": "x"}', encoding="utf-8")
    assert main(["config", "validate", "--instance", "claude", "--json"]) == EXIT_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["message"] == "expected an integer, got str"


def test_unknown_instance_exits_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "show", "--instance", "nope"]) == EXIT_NO_MATCH
    assert "nessuna istanza" in capsys.readouterr().out


def test_set_parses_json_and_falls_back_to_a_string(instance: Instance) -> None:
    assert instance.settings_path is not None
    assert main(["config", "set", "poll_interval", "300", "--instance", "claude"]) == EXIT_OK
    assert main(["config", "set", "bar_fg", "#00cc66", "--instance", "claude"]) == EXIT_OK
    assert main(["config", "set", "tooltip_fields", '["five_hour"]', "--instance", "claude"]) == EXIT_OK
    data = store.load(instance.settings_path).data
    assert data["poll_interval"] == 300
    assert data["bar_fg"] == "#00cc66"
    assert data["tooltip_fields"] == ["five_hour"]
    assert data["mia_chiave"] == 1


def test_set_refuses_an_invalid_value(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.settings_path is not None
    before = instance.settings_path.read_text(encoding="utf-8")
    assert main(["config", "set", "poll_interval", "0", "--instance", "claude"]) == EXIT_FAILED
    assert "nulla" in capsys.readouterr().out
    assert instance.settings_path.read_text(encoding="utf-8") == before


def test_unset_removes_a_key(instance: Instance) -> None:
    assert instance.settings_path is not None
    assert main(["config", "unset", "bar_fg", "--instance", "claude"]) == EXIT_OK
    assert "bar_fg" not in store.load(instance.settings_path).data


def test_create_refuses_an_existing_file(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "create", "--instance", "claude"]) == EXIT_FAILED
    assert "esiste" in capsys.readouterr().out


def test_export_then_import(instance: Instance, tmp_path: Path) -> None:
    assert instance.settings_path is not None
    profile = tmp_path / "profile.json"
    assert main(["config", "export", str(profile), "--instance", "claude"]) == EXIT_OK
    assert main(["config", "set", "poll_interval", "999", "--instance", "claude"]) == EXIT_OK
    assert main(["config", "import", str(profile), "--instance", "claude"]) == EXIT_OK
    assert "poll_interval" not in store.load(instance.settings_path).data


def test_history_diff_and_restore(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.settings_path is not None
    main(["config", "set", "poll_interval", "100", "--instance", "claude"])
    main(["config", "set", "poll_interval", "200", "--instance", "claude"])
    capsys.readouterr()

    assert main(["config", "history", "--instance", "claude"]) == EXIT_OK
    assert len(capsys.readouterr().out.strip().splitlines()) == 2

    assert main(["config", "diff", "2", "--instance", "claude"]) == EXIT_OK
    assert "poll_interval" in capsys.readouterr().out

    assert main(["config", "restore", "2", "--instance", "claude"]) == EXIT_OK
    assert store.load(instance.settings_path).data["poll_interval"] == 100
    assert len(history.list_versions(instance)) == 3


def test_diff_with_a_bad_version_number(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "diff", "7", "--instance", "claude"]) == EXIT_FAILED
    assert "inesistente" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("300", 300), ("true", True), ('["a"]', ["a"]), ("#00cc66", "#00cc66"), ("ciao", "ciao")],
)
def test_parse_setting_value(raw: str, expected: object) -> None:
    assert parse_setting_value(raw) == expected
