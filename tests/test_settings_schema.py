"""The schema must cover every key the three monitors document.

The fixtures under ``tests/fixtures/docs`` are verbatim copies of the upstream
``docs/configuration.md``; the keys are extracted from the first cell of the
markdown tables (``| `key` | default | description |``), which is how those
documents list every setting. Refreshing the fixtures is therefore enough to
notice a new upstream key: this test turns red until the schema declares it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from usage_monitor_manager.settings.schema import (
    LANGUAGES,
    SECTION_ORDER,
    THRESHOLD_PREFIX,
    Schema,
    schema_for,
)

DOCS = Path(__file__).parent / "fixtures" / "docs"
KIND_DOCS = {
    "claude": DOCS / "claude-configuration.md",
    "copilot": DOCS / "copilot-configuration.md",
    "codex": DOCS / "codex-configuration.md",
}

# Values that live in a table cell but are not settings keys: the bar display
# modes have a table of their own in the "Tray icon bars" section.
NOT_KEYS = frozenset({"utilization", "overage"})

_KEY_ROW = re.compile(r"^\|\s*`([a-z_][a-z_0-9]*)`", re.MULTILINE)


def documented_keys(path: Path) -> set[str]:
    return {key for key in _KEY_ROW.findall(path.read_text(encoding="utf-8")) if key not in NOT_KEYS}


@pytest.mark.parametrize("kind", sorted(KIND_DOCS))
def test_every_documented_key_is_in_the_schema(kind: str) -> None:
    schema = schema_for(kind)
    missing = sorted(key for key in documented_keys(KIND_DOCS[kind]) if schema.field_for(key) is None)
    assert missing == []


@pytest.mark.parametrize("kind", sorted(KIND_DOCS))
def test_schema_keys_are_documented_or_deliberate(kind: str) -> None:
    """The only keys beyond the docs are the ones we add on purpose."""
    documented = documented_keys(KIND_DOCS[kind])
    extra = {entry.key for entry in schema_for(kind)} - documented
    deliberate = {
        THRESHOLD_PREFIX,  # the family field itself, not a key
        "on_double_click_command",  # legacy alias, mentioned in prose
        f"notify_{kind}_update",  # only Claude documents its own row
    }
    assert extra <= deliberate


def test_claude_only_keys_are_absent_from_copilot() -> None:
    copilot = schema_for("copilot")
    for key in ("currency_symbol", "alert_time_aware", "alert_extra_usage_spent"):
        assert copilot.field_for(key) is None
    assert copilot.fields.get("alert_thresholds_five_hour") is None
    assert copilot.field_for("alert_thresholds_premium_interactions") is not None


def test_notify_key_is_per_kind() -> None:
    assert schema_for("claude").field_for("notify_claude_update") is not None
    assert schema_for("claude").field_for("notify_codex_update") is None
    assert schema_for("codex").field_for("notify_codex_update") is not None


def test_prefix_family_resolves_variant_keys() -> None:
    schema = schema_for("claude")
    family = schema.field_for("alert_thresholds_seven_day_opus")
    assert family is not None
    assert family.key == THRESHOLD_PREFIX
    assert family.type == "prefix_family"
    assert f"{THRESHOLD_PREFIX}seven_day_cowork" in family.suggestions


def test_icon_fields_entry_count_differs_for_copilot() -> None:
    assert schema_for("claude").fields["icon_fields"].min_entries == 2
    assert schema_for("copilot").fields["icon_fields"].min_entries == 1
    assert schema_for("copilot").fields["icon_fields"].max_entries == 2


def test_language_enum_lists_the_thirteen_locales() -> None:
    choices = schema_for("codex").fields["language"].choices
    assert choices[0] == ""
    assert tuple(choices[1:]) == LANGUAGES
    assert len(LANGUAGES) == 13


def test_sections_are_ordered_and_non_empty() -> None:
    schema = schema_for("claude")
    sections = [section for section, _label, _fields in schema.sections()]
    assert sections == [section for section in SECTION_ORDER if section in sections]
    assert all(fields for _id, _label, fields in schema.sections())
    assert schema.section_order == SECTION_ORDER


def test_defaults_match_the_documentation() -> None:
    claude = schema_for("claude")
    assert claude.default_for("poll_interval") == 180
    assert claude.default_for("alert_thresholds_seven_day") == [95]
    assert claude.default_for("icon_fields") == ["five_hour", "seven_day"]
    assert claude.default_for("bar_marker") == "#fffc"
    assert schema_for("copilot").default_for("tooltip_fields") == ["premium_interactions"]


def test_to_dict_is_json_ready() -> None:
    payload = schema_for("copilot").to_dict()
    assert payload["kind"] == "copilot"
    assert {entry["key"] for entry in payload["fields"]} == set(schema_for("copilot").keys())
    assert payload["quota_fields"] == ["chat", "completions", "premium_interactions"]
    assert payload["rgba_subkeys"] == ["fg", "fg_half", "fg_dim", "fg_warn"]


def test_unknown_kind_raises() -> None:
    with pytest.raises(KeyError):
        schema_for("gemini")


def test_schema_instances_are_cached() -> None:
    first = schema_for("claude")
    assert isinstance(first, Schema)
    assert schema_for("CLAUDE") is first
