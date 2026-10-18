"""Store: validation parity with the monitors, round-trip, history, presets.

The validation cases mirror ``tests/test_settings.py`` of the three monitors
(a representative subset: numeric bounds, booleans, colours, threshold
sorting/dedupe, command string to list, the wildcard rule, the RGBA shape).
Every write goes to ``tmp_path``; nothing here touches a real settings file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from usage_monitor_manager import history
from usage_monitor_manager.identity import config_dir_suffix
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.model import Instance
from usage_monitor_manager.settings import presets, store


def make_instance(kind_key: str, config_dir: Path, *, settings: Path | None = None) -> Instance:
    kind = get_kind(kind_key)
    return Instance(
        kind=kind,
        config_dir=config_dir,
        instance_suffix=config_dir_suffix(config_dir, kind.default_config_dir),
        exe_path=config_dir / "UsageMonitor.exe",
        settings_path=settings,
        settings_exists=settings is not None,
    )


# --------------------------------------------------------------------------- #
# Validation parity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("data", "key", "message"),
    [
        ({"poll_interval": "180"}, "poll_interval", "expected an integer, got str"),
        ({"poll_interval": True}, "poll_interval", "expected an integer, got bool"),
        ({"poll_interval": 0}, "poll_interval", "must be >= 1, got 0"),
        ({"idle_pause": -1}, "idle_pause", "must be >= 0, got -1"),
        ({"bar_fg": 123}, "bar_fg", "expected a color string, got int"),
        ({"alert_time_aware": "yes"}, "alert_time_aware", "expected true or false, got str"),
        ({"alert_thresholds_five_hour": 50}, "alert_thresholds_five_hour", "expected an array, got int"),
        (
            {"alert_thresholds_five_hour": [0, 50]},
            "alert_thresholds_five_hour",
            "all values must be numbers between 1 and 100",
        ),
        (
            {"alert_extra_usage_spent": [0]},
            "alert_extra_usage_spent",
            "all values must be numbers greater than 0",
        ),
        ({"alert_time_aware_below": 0}, "alert_time_aware_below", "must be between 1 and 100, got 0"),
        ({"time_format": "26h"}, "time_format", 'must be "24h" or "12h", got \'26h\''),
        ({"icon_style": "bars"}, "icon_style", 'must be "number+bars" or "numbers", got \'bars\''),
        ({"currency_symbol": 5}, "currency_symbol", "expected a string, got int"),
        ({"quick_action_command": 7}, "quick_action_command", "expected a string or array of strings, got int"),
        ({"quick_action_command": ["", "x"]}, "quick_action_command", "all items must be non-empty strings"),
        ({"tooltip_fields": ["", "a"]}, "tooltip_fields", "all entries must be non-empty strings"),
        ({"popup_fields": ["*", "a", "*"]}, "popup_fields", '"*" may appear at most once'),
        ({"icon_fields": ["five_hour"]}, "icon_fields", "expected exactly 2 entries, got 1"),
        (
            {"icon_fields": ["five_hour:nope", "seven_day"]},
            "icon_fields",
            "unknown bar mode in: five_hour:nope (valid: overage, utilization)",
        ),
        ({"icon_light": []}, "icon_light", "expected an object, got list"),
        ({"icon_light": {"fg": [1, 2, 3]}}, "icon_light.fg", "expected [R, G, B, A] with integers 0–255"),
        (
            {"cli_command": []},
            "cli_command",
            "expected an object mapping a name to a command array, got list",
        ),
        ({"cli_command": {"WSL": []}}, "cli_command.WSL", "expected a non-empty array of non-empty strings"),
    ],
)
def test_validation_messages_match_the_monitor(data: dict[str, Any], key: str, message: str) -> None:
    report = store.validate("claude", data)
    assert (key, message) in report.errors
    assert report.formatted()[0].startswith("  ")


def test_invalid_keys_are_dropped_from_the_normalized_copy() -> None:
    report = store.validate("claude", {"poll_interval": "x", "bar_fg": "#00cc66"})
    assert report.normalized == {"bar_fg": "#00cc66"}
    assert not report.ok


def test_thresholds_are_sorted_and_deduped() -> None:
    report = store.validate("claude", {"alert_thresholds_five_hour": [95, 50, 95, 80]})
    assert report.ok
    assert report.normalized["alert_thresholds_five_hour"] == [50, 80, 95]


def test_command_string_becomes_a_list_and_empty_string_becomes_empty() -> None:
    report = store.validate("claude", {"quick_action_command": "notepad", "on_reset_command": "  "})
    assert report.normalized["quick_action_command"] == ["notepad"]
    assert report.normalized["on_reset_command"] == []
    assert report.ok


def test_string_lists_are_deduped_but_the_wildcard_survives() -> None:
    report = store.validate("claude", {"tooltip_fields": ["a", "a", "b"], "popup_fields": ["*", "a", "a"]})
    assert report.normalized["tooltip_fields"] == ["a", "b"]
    assert report.normalized["popup_fields"] == ["*", "a"]


def test_bad_rgba_channels_are_removed_but_the_key_stays() -> None:
    report = store.validate("claude", {"icon_light": {"fg": [1, 2, 3, 4], "fg_dim": "red"}})
    assert report.normalized["icon_light"] == {"fg": [1, 2, 3, 4]}
    assert report.errors == [("icon_light.fg_dim", "expected [R, G, B, A] with integers 0–255")]


def test_copilot_accepts_one_icon_field_claude_does_not() -> None:
    assert store.validate("copilot", {"icon_fields": ["chat"]}).ok
    assert not store.validate("claude", {"icon_fields": ["five_hour"]}).ok


def test_unknown_keys_are_reported_and_preserved() -> None:
    report = store.validate("copilot", {"notify_claude_update": True, "wat": [1]})
    assert sorted(report.unknown_keys) == ["notify_claude_update", "wat"]
    assert report.normalized["wat"] == [1]
    assert report.ok


def test_prefix_family_variants_validate_like_the_base_key() -> None:
    report = store.validate("claude", {"alert_thresholds_seven_day_opus": [80, 50]})
    assert report.normalized["alert_thresholds_seven_day_opus"] == [50, 80]


# --------------------------------------------------------------------------- #
# Load / save
# --------------------------------------------------------------------------- #


def test_load_reads_utf8_with_bom(tmp_path: Path) -> None:
    path = tmp_path / "usage-monitor-settings.json"
    path.write_bytes(b'\xef\xbb\xbf{"currency_symbol": "\xe2\x82\xac"}')
    loaded = store.load(path)
    assert loaded.exists
    assert loaded.data == {"currency_symbol": "€"}


def test_load_of_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    loaded = store.load(tmp_path / "nope.json")
    assert (loaded.exists, loaded.data, loaded.error) == (False, {}, None)


def test_load_reports_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{", encoding="utf-8")
    assert store.load(path).error is not None


def test_save_round_trip_preserves_unknown_keys_and_order(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    data = {"zzz_custom": {"a": 1}, "bar_fg": "#00cc66", "poll_interval": 300}
    store.save(path, data)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert "\r\n" not in text
    assert list(store.load(path).data) == ["zzz_custom", "bar_fg", "poll_interval"]
    assert store.load(path).data == data


def test_save_keeps_a_backup_of_the_previous_content(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    store.save(path, {"poll_interval": 1})
    store.save(path, {"poll_interval": 2})
    assert store.load(path).data == {"poll_interval": 2}
    assert store.load(path.with_name("s.json.bak")).data == {"poll_interval": 1}


def test_create_default_writes_an_empty_object(tmp_path: Path) -> None:
    created = store.create_default(tmp_path / "sub" / "s.json")
    assert created.read_text(encoding="utf-8") == "{}\n"


def test_suggested_path_is_the_first_search_entry(tmp_path: Path) -> None:
    instance = make_instance("claude", tmp_path / ".claude-valeria")
    assert store.suggested_path(instance) == tmp_path / ".claude-valeria" / "usage-monitor-settings.json"


def test_copy_sections_replaces_only_the_chosen_sections() -> None:
    src = {"bar_fg": "#111111", "poll_interval": 60, "custom": 1}
    dst = {"bar_fg": "#222222", "bg": "#000000", "poll_interval": 180, "custom": 2}
    merged = store.copy_sections(src, dst, ["popup_colors"], "claude")
    assert merged["bar_fg"] == "#111111"
    assert "bg" not in merged  # the section is copied whole
    assert merged["poll_interval"] == 180
    assert merged["custom"] == 2


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


@pytest.fixture
def archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Instance:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    settings_path = tmp_path / ".claude" / "usage-monitor-settings.json"
    return make_instance("claude", tmp_path / ".claude", settings=settings_path)


def test_every_save_is_archived(archived: Instance) -> None:
    assert archived.settings_path is not None
    store.save(archived.settings_path, {"poll_interval": 1}, instance=archived)
    store.save(archived.settings_path, {"poll_interval": 2}, instance=archived)
    versions = history.list_versions(archived)
    assert len(versions) == 2
    assert history.read_version(archived, versions[0].stamp) == {"poll_interval": 2}
    assert history.history_dir(archived).name.startswith("claude")


def test_history_is_pruned_to_the_last_fifty(archived: Instance) -> None:
    assert archived.settings_path is not None
    for number in range(55):
        store.save(archived.settings_path, {"poll_interval": number + 1}, instance=archived)
    assert len(history.list_versions(archived)) == history.KEEP_VERSIONS


def test_diff_reports_added_removed_and_changed() -> None:
    lines = {line.key: line for line in history.diff({"a": 1, "b": 2}, {"b": 3, "c": 4})}
    assert lines["a"].change == "removed"
    assert lines["b"].change == "changed"
    assert (lines["b"].before, lines["b"].after) == (2, 3)
    assert lines["c"].change == "added"


def test_restore_rewrites_the_file_and_archives_the_restore(archived: Instance) -> None:
    assert archived.settings_path is not None
    store.save(archived.settings_path, {"poll_interval": 1}, instance=archived)
    store.save(archived.settings_path, {"poll_interval": 2}, instance=archived)
    oldest = history.list_versions(archived)[-1]
    assert history.restore(archived, oldest.stamp) == {"poll_interval": 1}
    assert store.load(archived.settings_path).data == {"poll_interval": 1}
    assert len(history.list_versions(archived)) == 3


def test_export_and_import_round_trip(archived: Instance, tmp_path: Path) -> None:
    assert archived.settings_path is not None
    store.save(archived.settings_path, {"poll_interval": 42, "custom": True}, instance=archived)
    profile = history.export(archived, tmp_path / "profile.json")
    store.save(archived.settings_path, {}, instance=archived)
    result = history.import_(archived, profile)
    assert result.ok
    assert store.load(archived.settings_path).data == {"poll_interval": 42, "custom": True}
    assert result.unknown_keys == ["custom"]


def test_import_refuses_an_invalid_profile(archived: Instance, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"poll_interval": "x"}', encoding="utf-8")
    result = history.import_(archived, bad)
    assert not result.ok
    assert result.errors == [("poll_interval", "expected an integer, got str")]
    assert not (archived.settings_path or Path("x")).exists()


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #


def test_palette_preserves_brand_colour_with_a_small_blend() -> None:
    palette = presets.palette_from_primary("#D97757")
    assert palette == {"bar": "#D97757", "light": "#DF8B70", "dark": "#A35941"}


@pytest.mark.parametrize("primary", [color for _, color in presets.BRAND_PRESETS])
def test_preset_icon_colours_contrast_with_both_taskbar_themes(primary: str) -> None:
    palette = presets.palette_from_primary(primary)

    def luminance(color: str) -> float:
        rgb = [int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    assert (luminance(palette["light"]) + 0.05) / (luminance("#202020") + 0.05) >= 4.5
    assert 1.05 / (luminance(palette["dark"]) + 0.05) >= 4.5


def test_palette_accepts_the_short_form_and_rejects_nonsense() -> None:
    assert presets.palette_from_primary("#fff")["bar"] == "#FFFFFF"
    with pytest.raises(ValueError, match="esadecimale"):
        presets.palette_from_primary("rosso")


def test_readable_text_color_flips_on_luminance() -> None:
    assert presets.readable_text_color("#FFFFFF") == "#111111"
    assert presets.readable_text_color("#000000") == "#FFFFFF"


def test_apply_palette_tints_bars_preserving_alpha_and_warnings() -> None:
    data = {
        "bg": "#1e1e1e",
        "custom": "retained",
        "icon_light": {"fg": [1, 2, 3, 4], "fg_half": [5, 6, 7, 65], "fg_warn": [9, 9, 9, 9]},
    }
    merged = presets.apply_palette(data, "#10A37F", "codex")
    assert merged["bg"] == "#1e1e1e"
    assert merged["custom"] == "retained"
    assert merged["bar_fg"] == "#10A37F"
    assert merged["icon_light"]["fg"] == presets.rgba_from_hex(presets.palette_from_primary("#10A37F")["light"])
    assert merged["icon_light"]["fg_warn"] == [9, 9, 9, 9]
    assert merged["icon_light"]["fg_half"] == merged["icon_light"]["fg"][:3] + [65]
    assert merged["icon_light"]["fg_dim"] == merged["icon_light"]["fg"][:3] + [140]
    assert merged["icon_dark"]["fg_half"] == merged["icon_dark"]["fg"][:3] + [80]
    assert data["icon_light"]["fg_half"] == [5, 6, 7, 65]
    assert merged["icon_dark"]["fg"] == presets.rgba_from_hex(presets.palette_from_primary("#10A37F")["dark"])
    assert store.validate("codex", merged).ok


def test_brand_preset_for_matches_a_profile_label() -> None:
    assert presets.brand_preset_for("Claude Valeria") == ("Claude", "#D97757")
    assert presets.brand_preset_for("qualcosa") is None
