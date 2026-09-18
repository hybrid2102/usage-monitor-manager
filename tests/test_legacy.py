"""Tests for the legacy import wizard (PLAN.md section 9.3).

The three sources are all injected: a temporary ``Tools`` directory with fake
shortcuts (``platform.read_shortcuts`` is monkeypatched, so no COM call), a
dict of Run entries and a ``profiles.json`` written under ``tmp_path``. The
real registry, the real shortcuts and the real config editor file are never
read.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from usage_monitor_manager import legacy, manager_config
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.manager_config import DeclaredInstance, ManagerConfig


class Box:
    """A fake machine: a home dir, an exe dir, shortcuts, Run entries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.tools = root / "Tools"
        for directory in (self.home, self.tools):
            directory.mkdir(parents=True, exist_ok=True)
        self.shortcuts: dict[Path, tuple[Path | None, list[str]]] = {}
        self.run_entries: dict[str, str] = {}
        self.profiles = root / "profiles.json"
        self.config = root / "manager-config.json"

    def exe(self, kind_key: str) -> Path:
        path = self.tools / get_kind(kind_key).exe_name
        path.write_bytes(b"MZ fake")
        return path

    def shortcut(self, kind_key: str, label: str, config_dir: Path | None) -> Path:
        exe = self.exe(kind_key)
        link = self.tools / f"{exe.name} - {label}.lnk"
        link.write_bytes(b"lnk")
        args = [f"--config-dir={config_dir}"] if config_dir is not None else []
        self.shortcuts[link] = (exe, args)
        return link

    def run_entry(self, kind_key: str, config_dir: Path | None, *, name: str | None = None) -> None:
        kind = get_kind(kind_key)
        exe = self.exe(kind_key)
        command = f'"{exe}"'
        if config_dir is not None:
            command += f' --config-dir="{config_dir}"'
        self.run_entries[name or kind.run_key_base] = command

    def write_profiles(self, *entries: tuple[str, Path]) -> None:
        self.profiles.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profiles": [
                        {"key": f"profile_{index}", "label": label, "path": str(path / "usage-monitor-settings.json")}
                        for index, (label, path) in enumerate(entries)
                    ],
                }
            ),
            encoding="utf-8",
        )

    def find(self) -> tuple[list[legacy.LegacyFinding], list[str]]:
        return legacy.find_legacy(
            exe_dirs=[self.tools],
            run_entries=self.run_entries,
            profiles_file=self.profiles,
        )

    def run_import(self, *, dry_run: bool = False) -> legacy.ImportReport:
        return legacy.import_legacy(
            dry_run=dry_run,
            config_path=self.config,
            exe_dirs=[self.tools],
            run_entries=self.run_entries,
            profiles_file=self.profiles,
        )


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Box]:
    machine = Box(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: machine.home))
    monkeypatch.setattr(
        legacy.platform,
        "read_shortcuts",
        lambda paths: {p: machine.shortcuts[p] for p in paths if p in machine.shortcuts},
    )
    yield machine


# --------------------------------------------------------------------------- #
# Label heuristics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kind_key", "dir_name", "expected"),
    [
        ("claude", ".claude", "Claude"),
        ("claude", ".claude-valeria", "Claude Valeria"),
        ("claude", ".claude_work", "Claude Work"),
        ("copilot", ".copilot", "Copilot"),
        ("codex", ".codex-side-project", "Codex Side Project"),
        ("codex", "scratch", "Codex Scratch"),
    ],
)
def test_suggest_label(box: Box, kind_key: str, dir_name: str, expected: str) -> None:
    kind = get_kind(kind_key)
    assert legacy.suggest_label(kind, box.home / dir_name) == expected


# --------------------------------------------------------------------------- #
# The three sources
# --------------------------------------------------------------------------- #


def test_finds_a_legacy_shortcut(box: Box) -> None:
    valeria = box.home / ".claude-valeria"
    box.shortcut("claude", "valeria", valeria)
    findings, notes = box.find()
    assert notes == []
    assert len(findings) == 1
    assert findings[0].source == legacy.SOURCE_SHORTCUT
    assert findings[0].kind == "claude"
    assert findings[0].config_dir == valeria
    assert findings[0].label == "Claude Valeria"
    assert findings[0].detail == "UsageMonitorForClaude.exe - valeria.lnk"


def test_finds_run_entries_including_the_default_instance(box: Box) -> None:
    box.run_entry("claude", None)
    box.run_entry("copilot", box.home / ".copilot")
    findings, _ = box.find()
    assert [(f.kind, f.source) for f in findings] == [
        ("claude", legacy.SOURCE_RUN_KEY),
        ("copilot", legacy.SOURCE_RUN_KEY),
    ]
    assert findings[0].config_dir == box.home / ".claude"
    assert findings[0].label == "Claude"


def test_foreign_run_entries_are_ignored(box: Box) -> None:
    box.run_entries["OneDrive"] = r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background'
    box.run_entries["Nonsense"] = ""
    findings, _ = box.find()
    assert findings == []


def test_finds_the_config_editor_profiles(box: Box) -> None:
    box.write_profiles(("Claude Valeria", box.home / ".claude-valeria"), ("Codex", box.home / ".codex"))
    findings, notes = box.find()
    assert notes == []
    assert [(f.kind, f.label, f.source) for f in findings] == [
        ("claude", "Claude Valeria", legacy.SOURCE_PROFILES),
        ("codex", "Codex", legacy.SOURCE_PROFILES),
    ]


def test_profile_without_a_recognisable_kind_is_noted(box: Box) -> None:
    box.write_profiles(("Mystery", box.home / ".gemini"))
    findings, notes = box.find()
    assert findings == []
    assert any("non riconosciuto" in note for note in notes)


def test_broken_profiles_file_is_noted_not_fatal(box: Box) -> None:
    box.profiles.write_text("{{{", encoding="utf-8")
    findings, notes = box.find()
    assert findings == []
    assert any("JSON" in note for note in notes)


def test_missing_profiles_file_is_silent(box: Box) -> None:
    findings, notes = box.find()
    assert (findings, notes) == ([], [])


def test_the_config_editor_label_wins_over_a_generated_one(box: Box) -> None:
    valeria = box.home / ".claude-valeria"
    box.shortcut("claude", "valeria", valeria)
    box.run_entry("claude", valeria, name="UsageMonitorForClaude_578560bbc610")
    box.write_profiles(("Claude di Valeria", valeria))
    findings, _ = box.find()
    assert len(findings) == 1
    assert findings[0].source == legacy.SOURCE_PROFILES
    assert findings[0].label == "Claude di Valeria"


def test_findings_are_sorted_by_kind_then_config_dir(box: Box) -> None:
    box.run_entry("codex", None)
    box.run_entry("copilot", None)
    box.run_entry("claude", None)
    box.shortcut("claude", "valeria", box.home / ".claude-valeria")
    findings, _ = box.find()
    assert [f.kind for f in findings] == ["claude", "claude", "copilot", "codex"]
    assert [f.config_dir.name for f in findings[:2]] == [".claude", ".claude-valeria"]


# --------------------------------------------------------------------------- #
# import_legacy / run_once
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing(box: Box) -> None:
    box.run_entry("claude", None)
    report = box.run_import(dry_run=True)
    assert report.applied is False
    assert len(report.added) == 1
    assert box.config.exists() is False
    assert manager_config.load(box.config).legacy_import_done is False
    assert "da aggiungere: 1" in report.lines()


def test_a_real_run_declares_the_instances_and_sets_the_flag(box: Box) -> None:
    box.run_entry("claude", None)
    box.shortcut("claude", "valeria", box.home / ".claude-valeria")
    report = box.run_import()
    assert report.applied is True
    config = manager_config.load(box.config)
    assert config.legacy_import_done is True
    assert [(i.kind, i.label) for i in config.instances] == [("claude", "Claude"), ("claude", "Claude Valeria")]
    assert all(i.enabled for i in config.instances)


def test_an_already_declared_instance_keeps_its_own_label(box: Box) -> None:
    valeria = box.home / ".claude-valeria"
    manager_config.save(
        ManagerConfig(instances=(DeclaredInstance(kind="claude", config_dir=valeria, label="La mia"),)),
        box.config,
    )
    box.shortcut("claude", "valeria", valeria)
    report = box.run_import()
    assert report.added == ()
    assert [entry.label for entry in report.already_known] == ["La mia"]
    assert [i.label for i in manager_config.load(box.config).instances] == ["La mia"]


def test_report_serializes(box: Box) -> None:
    box.run_entry("copilot", None)
    payload = box.run_import(dry_run=True).to_dict()
    assert payload["total_found"] == 1
    assert payload["applied"] is False
    assert payload["findings"][0]["kind"] == "copilot"
    assert json.dumps(payload)


def test_empty_machine_reports_nothing_found(box: Box) -> None:
    report = box.run_import(dry_run=True)
    assert report.total_found == 0
    assert report.lines() == ["nessuna configurazione legacy trovata"]


def test_run_once_only_runs_once(box: Box) -> None:
    box.run_entry("codex", None)
    first = legacy.run_once(
        box.config,
        exe_dirs=[box.tools],
        run_entries=box.run_entries,
        profiles_file=box.profiles,
    )
    assert first is not None
    assert first.applied is True
    second = legacy.run_once(
        box.config,
        exe_dirs=[box.tools],
        run_entries=box.run_entries,
        profiles_file=box.profiles,
    )
    assert second is None


def test_unreadable_shortcuts_are_noted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    box.shortcut("claude", "valeria", box.home / ".claude-valeria")
    monkeypatch.setattr(legacy.platform, "read_shortcuts", lambda paths: {})
    findings, notes = box.find()
    assert findings == []
    assert any("non leggibili" in note for note in notes)
