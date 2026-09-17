"""Tests for the config-dir parsing and suffix algorithm.

The suffix must stay bit-identical to the monitors' own implementation: the
known-value test below is the contract (registry key
``UsageMonitorForClaude_578560bbc610`` on the dev machine).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from usage_monitor_manager.identity import config_dir_suffix, parse_config_dir

VALERIA = Path(r"C:\Users\Marco\.claude-valeria")


def test_parse_equals_form() -> None:
    assert parse_config_dir(["app.exe", r"--config-dir=C:\Users\Marco\.claude-valeria"]) == VALERIA


def test_parse_separate_form_and_quotes() -> None:
    assert parse_config_dir(["app.exe", "--config-dir", r'"C:\Users\Marco\.claude-valeria"']) == VALERIA


def test_parse_strips_trailing_separator() -> None:
    assert parse_config_dir([r"--config-dir=C:\Users\Marco\.claude-valeria\\"]) == VALERIA


def test_parse_expands_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMM_TEST_DIR", r"C:\Users\Marco")
    assert parse_config_dir([r"--config-dir=%UMM_TEST_DIR%\.claude-valeria"]) == VALERIA


def test_parse_expands_user() -> None:
    assert parse_config_dir(["--config-dir=~/.claude"]) == Path.home() / ".claude"


def test_parse_missing_or_empty() -> None:
    assert parse_config_dir(["app.exe", "--verbose"]) is None
    assert parse_config_dir(["--config-dir="]) is None
    assert parse_config_dir(["--config-dir"]) is None


def test_parse_last_occurrence_wins() -> None:
    assert parse_config_dir(["--config-dir=~/.claude", r"--config-dir=C:\Users\Marco\.claude-valeria"]) == VALERIA


def test_suffix_empty_for_default_dir(tmp_path: Path) -> None:
    default = tmp_path / ".claude"
    default.mkdir()
    assert config_dir_suffix(default, default) == ""
    assert config_dir_suffix(str(default).upper(), default) == ""


def test_suffix_is_hash_for_other_dir(tmp_path: Path) -> None:
    default = tmp_path / ".claude"
    other = tmp_path / ".claude-other"
    suffix = config_dir_suffix(other, default)
    assert suffix.startswith("_")
    assert len(suffix) == 13
    assert suffix == config_dir_suffix(other, default)


def test_suffix_is_stable_under_case_and_trailing_slash(tmp_path: Path) -> None:
    default = tmp_path / ".claude"
    other = tmp_path / ".claude-other"
    assert config_dir_suffix(str(other).upper(), default) == config_dir_suffix(other, default)
    assert config_dir_suffix(f"{other}{os.sep}", default) == config_dir_suffix(other, default)


@pytest.mark.skipif(not VALERIA.exists(), reason="known-value check needs the dev machine's config dir")
def test_known_real_suffix() -> None:
    assert config_dir_suffix(VALERIA, Path.home() / ".claude") == "_578560bbc610"
