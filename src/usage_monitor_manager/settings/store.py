"""Read, validate and write ``usage-monitor-settings.json``.

Three rules shape this module, all of them from PLAN.md section 6.4:

* **Read like the monitor**: ``utf-8-sig``, so a file written by PowerShell 5
  or old Notepad (with a BOM) loads exactly as the monitor loads it.
* **Validate like the monitor**: :func:`validate` is a port of the monitors'
  ``settings._validate()`` - the same rules, the same messages, the same
  normalizations (thresholds sorted and deduped, a command string turned into
  a one-element list, ...). The user has to see the problem here, not in a
  MessageBox at the next restart.
* **Never lose data**: unknown keys are preserved verbatim, the previous
  content is kept as ``.bak``, the write is atomic (tmp + ``os.replace``) and
  every successful save is archived by :mod:`usage_monitor_manager.history`.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ..model import Instance, JsonDict
from .schema import THRESHOLD_PREFIX, Schema, schema_for

__all__ = [
    "ICON_BAR_MODES",
    "LoadedSettings",
    "ValidationReport",
    "as_json_array",
    "as_json_object",
    "copy_sections",
    "create_default",
    "load",
    "save",
    "suggested_path",
    "validate",
]

ICON_BAR_MODES = ("overage", "utilization")
"""Sorted, because the monitors' error message lists them sorted."""

_ENCODING_READ = "utf-8-sig"
_ENCODING_WRITE = "utf-8"


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    """The result of reading a settings file: data, existence, parse error."""

    path: Path
    data: JsonDict
    exists: bool
    error: str | None = None
    text: str | None = None
    """The raw file content, for the JSON tab (``None`` when unreadable)."""

    def to_dict(self) -> JsonDict:
        return {
            "path": str(self.path),
            "data": self.data,
            "exists": self.exists,
            "error": self.error,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """What the monitor would have complained about, before it complains."""

    errors: list[tuple[str, str]] = field(default_factory=lambda: [])
    normalized: JsonDict = field(default_factory=lambda: {})
    unknown_keys: list[str] = field(default_factory=lambda: [])

    @property
    def ok(self) -> bool:
        return not self.errors

    def formatted(self) -> list[str]:
        """The monitors' own error lines, two-space indent included."""
        return [f"  {key}: {message}" for key, message in self.errors]

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "errors": [{"key": key, "message": message} for key, message in self.errors],
            "normalized": self.normalized,
            "unknown_keys": list(self.unknown_keys),
        }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load(path: Path | str) -> LoadedSettings:
    """Read one settings file without validating it.

    A missing file is not an error (``exists=False``, empty data); an empty
    file reads as ``{}``, exactly like the monitors do.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding=_ENCODING_READ)
    except FileNotFoundError:
        return LoadedSettings(path=target, data={}, exists=False)
    except (OSError, UnicodeDecodeError) as error:
        return LoadedSettings(path=target, data={}, exists=True, error=str(error))

    stripped = text.strip()
    if not stripped:
        return LoadedSettings(path=target, data={}, exists=True, text=text)
    try:
        payload: object = json.loads(stripped)
    except json.JSONDecodeError as error:
        return LoadedSettings(path=target, data={}, exists=True, error=str(error), text=text)
    data = as_json_object(payload)
    if data is None:
        detail = f"Expected a JSON object, got {type(payload).__name__}"
        return LoadedSettings(path=target, data={}, exists=True, error=detail, text=text)
    return LoadedSettings(path=target, data=data, exists=True, text=text)


def suggested_path(instance: Instance) -> Path:
    """Where a missing settings file should be created for ``instance``.

    The first entry of the monitor's own search order: the instance's config
    dir when it is a custom one, the executable's directory otherwise.
    """
    from ..discovery import settings_candidates

    candidates = settings_candidates(instance.kind, instance.config_dir, instance.exe_path)
    return candidates[0]


def create_default(path: Path | str) -> Path:
    """Create an empty settings file (``{}``) the user can then fill in.

    The monitors never create this file; the manager does, because "every key
    is optional" is easier to explain with a file that exists.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, {})
    return target


# --------------------------------------------------------------------------- #
# Validation: a port of the monitors' settings._validate()
# --------------------------------------------------------------------------- #


def _type_name(value: object) -> str:
    return type(value).__name__


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _valid_rgba(value: object) -> bool:
    """A list of exactly 4 integers in 0-255 (the monitors' ``_valid_rgba``)."""
    channels = as_json_array(value)
    if channels is None or len(channels) != 4:
        return False
    return all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in channels)


def _keys_of(schema: Schema, *types: str) -> set[str]:
    return {entry.key for entry in schema if entry.type in types}


@dataclass(frozen=True, slots=True)
class _Rules:
    """The key sets the monitor's ``_validate`` branches on, for one kind."""

    numeric: dict[str, int]
    colors: set[str]
    percents: set[str]
    strings: set[str]
    commands: set[str]
    bools: set[str]
    string_lists: set[str]
    wildcard_lists: set[str]
    icons: set[str]
    amounts: set[str]
    icon_entries: tuple[int, ...]


def _rules(kind: str) -> _Rules:
    schema = schema_for(kind)
    icon_field = schema.fields["icon_fields"]
    lower = icon_field.min_entries or 2
    upper = icon_field.max_entries or 2
    return _Rules(
        numeric={entry.key: entry.minimum or 0 for entry in schema if entry.type == "int"},
        colors=_keys_of(schema, "hex_color"),
        percents=_keys_of(schema, "percent"),
        # ``language`` is an enum for the editor but a plain string for the
        # monitor: an unknown code is accepted and simply falls back.
        strings=_keys_of(schema, "string") | {"language"},
        commands=_keys_of(schema, "command"),
        bools=_keys_of(schema, "bool"),
        string_lists=_keys_of(schema, "string_list"),
        wildcard_lists=_keys_of(schema, "wildcard_list"),
        icons=_keys_of(schema, "rgba_dict"),
        amounts=_keys_of(schema, "amount_list"),
        icon_entries=tuple(range(lower, upper + 1)),
    )


def _entries_message(allowed: Sequence[int], count: int) -> str:
    if len(allowed) == 1:
        return f"expected exactly {allowed[0]} entries, got {count}"
    listed = " or ".join(str(value) for value in allowed)
    return f"expected {listed} entries, got {count}"


def _dedupe(values: Iterable[str], *, keep_wildcard: bool = False) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        if keep_wildcard and item == "*":
            seen.add(item)
            out.append(item)
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def as_json_array(value: object) -> list[Any] | None:
    """``value`` as a typed list, or ``None`` when it is not a JSON array."""
    if not isinstance(value, list):
        return None
    return list(cast(list[Any], value))


def as_json_object(value: object) -> dict[str, Any] | None:
    """``value`` as a typed mapping, or ``None`` when it is not a JSON object."""
    if not isinstance(value, dict):
        return None
    raw = cast(dict[Any, Any], value)
    return {str(key): item for key, item in raw.items()}


def validate(kind: str, data: Mapping[str, Any]) -> ValidationReport:  # noqa: C901, PLR0912, PLR0915 - one branch per monitor rule, kept in the monitors' order on purpose
    """Validate ``data`` the way the monitor of ``kind`` would.

    Returns the errors (``(key, message)``, the monitor's own wording), the
    normalized copy with invalid keys dropped, and the keys the schema does
    not know - which are *kept* in ``normalized``, because the monitor ignores
    them and the manager must not delete what it does not understand.
    """
    rules = _rules(kind)
    normalized: JsonDict = copy.deepcopy(dict(data))
    errors: list[tuple[str, str]] = []
    unknown: list[str] = []
    drop: list[str] = []

    for key in list(normalized):
        value: Any = normalized[key]
        as_list = as_json_array(value)
        as_dict = as_json_object(value)

        if key in rules.numeric:
            minimum = rules.numeric[key]
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append((key, f"expected an integer, got {_type_name(value)}"))
                drop.append(key)
            elif value < minimum:
                errors.append((key, f"must be >= {minimum}, got {value}"))
                drop.append(key)

        elif key in rules.colors:
            if not isinstance(value, str):
                errors.append((key, f"expected a color string, got {_type_name(value)}"))
                drop.append(key)

        elif key.startswith(THRESHOLD_PREFIX):
            if as_list is None:
                errors.append((key, f"expected an array, got {_type_name(value)}"))
                drop.append(key)
            elif any(not _is_number(item) or not (1 <= item <= 100) for item in as_list):
                errors.append((key, "all values must be numbers between 1 and 100"))
                drop.append(key)
            else:
                normalized[key] = sorted(set(as_list))

        elif key in rules.amounts:
            if as_list is None:
                errors.append((key, f"expected an array, got {_type_name(value)}"))
                drop.append(key)
            elif any(not _is_number(item) or item <= 0 for item in as_list):
                errors.append((key, "all values must be numbers greater than 0"))
                drop.append(key)
            else:
                normalized[key] = sorted(set(as_list))

        elif key in rules.percents:
            if not _is_number(value):
                errors.append((key, f"expected a number, got {_type_name(value)}"))
                drop.append(key)
            elif not (1 <= value <= 100):
                errors.append((key, f"must be between 1 and 100, got {value}"))
                drop.append(key)

        elif key in rules.strings:
            if not isinstance(value, str):
                errors.append((key, f"expected a string, got {_type_name(value)}"))
                drop.append(key)

        elif key == "time_format":
            if value not in ("24h", "12h"):
                errors.append((key, f'must be "24h" or "12h", got {value!r}'))
                drop.append(key)

        elif key == "icon_style":
            if value not in ("number+bars", "numbers"):
                errors.append((key, f'must be "number+bars" or "numbers", got {value!r}'))
                drop.append(key)

        elif key in rules.commands:
            if isinstance(value, str):
                # An empty or whitespace-only string means "not set".
                normalized[key] = [value] if value.strip() else []
            elif as_list is not None:
                if any(not isinstance(item, str) or not item.strip() for item in as_list):
                    errors.append((key, "all items must be non-empty strings"))
                    drop.append(key)
            else:
                errors.append((key, f"expected a string or array of strings, got {_type_name(value)}"))
                drop.append(key)

        elif key in rules.bools:
            if not isinstance(value, bool):
                errors.append((key, f"expected true or false, got {_type_name(value)}"))
                drop.append(key)

        elif key in rules.string_lists:
            if as_list is None:
                errors.append((key, f"expected an array, got {_type_name(value)}"))
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in as_list):
                errors.append((key, "all entries must be non-empty strings"))
                drop.append(key)
            else:
                normalized[key] = _dedupe(str(item) for item in as_list)

        elif key in rules.wildcard_lists:
            if as_list is None:
                errors.append((key, f"expected an array, got {_type_name(value)}"))
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in as_list):
                errors.append((key, "all entries must be non-empty strings"))
                drop.append(key)
            elif as_list.count("*") > 1:
                errors.append((key, '"*" may appear at most once'))
                drop.append(key)
            else:
                normalized[key] = _dedupe((str(item) for item in as_list), keep_wildcard=True)

        elif key == "icon_fields":
            if as_list is None:
                errors.append((key, f"expected an array, got {_type_name(value)}"))
                drop.append(key)
            elif len(as_list) not in rules.icon_entries:
                errors.append((key, _entries_message(rules.icon_entries, len(as_list))))
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in as_list):
                errors.append((key, "all entries must be non-empty strings"))
                drop.append(key)
            else:
                entries = [str(item) for item in as_list]
                invalid = [e for e in entries if ":" in e and e.split(":", 1)[1] not in ICON_BAR_MODES]
                if invalid:
                    joined = ", ".join(invalid)
                    errors.append((key, f"unknown bar mode in: {joined} (valid: {', '.join(ICON_BAR_MODES)})"))
                    drop.append(key)

        elif key in rules.icons:
            if as_dict is None:
                errors.append((key, f"expected an object, got {_type_name(value)}"))
                drop.append(key)
            else:
                for channel in list(as_dict):
                    if not _valid_rgba(as_dict[channel]):
                        errors.append((f"{key}.{channel}", "expected [R, G, B, A] with integers 0–255"))
                        del as_dict[channel]
                normalized[key] = as_dict

        elif key == "cli_command":
            if as_dict is None:
                errors.append((key, f"expected an object mapping a name to a command array, got {_type_name(value)}"))
                drop.append(key)
            else:
                for name in as_dict:
                    command = as_json_array(as_dict[name])
                    if not name.strip():
                        errors.append((key, "names must be non-empty strings"))
                        drop.append(key)
                        break
                    if not command or any(not isinstance(item, str) or not item.strip() for item in command):
                        errors.append((f"{key}.{name}", "expected a non-empty array of non-empty strings"))
                        drop.append(key)
                        break

        else:
            unknown.append(key)

    for key in drop:
        normalized.pop(key, None)

    return ValidationReport(errors=errors, normalized=normalized, unknown_keys=unknown)


def validate_draft(kind: str, data: Mapping[str, Any]) -> JsonDict:
    """JSON-ready :func:`validate`, for the window's live validation."""
    return validate(kind, data).to_dict()


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def dumps(data: Mapping[str, Any]) -> str:
    """The canonical serialization: ``indent=2``, UTF-8 text, trailing newline."""
    return json.dumps(dict(data), indent=2, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(data), encoding=_ENCODING_WRITE, newline="\n")
    os.replace(tmp, path)


def save(
    path: Path | str,
    data: Mapping[str, Any],
    *,
    instance: Instance | None = None,
    archive: bool = True,
) -> Path:
    """Write ``data`` atomically, keeping a ``.bak`` and archiving a copy.

    The previous content becomes ``<name>.bak`` before the replace, so a
    failure halfway leaves either the old file or the old file *and* its
    backup - never a truncated settings file. When ``instance`` is given (and
    ``archive`` is true) the new content is also filed in the instance's
    history directory (PLAN.md section 9.6).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_name(target.name + ".bak")
        try:
            backup.write_bytes(target.read_bytes())
        except OSError:
            pass
    _atomic_write(target, data)
    if archive and instance is not None:
        from ..history import archive_settings

        archive_settings(instance, data)
    return target


# --------------------------------------------------------------------------- #
# Copying between instances
# --------------------------------------------------------------------------- #


def _section_of(key: str, kind: str | None) -> str | None:
    kinds = (kind,) if kind else ("claude", "copilot", "codex")
    for candidate in kinds:
        found = schema_for(str(candidate)).field_for(key)
        if found is not None:
            return found.section
    return None


def copy_sections(
    src_data: Mapping[str, Any],
    dst_data: Mapping[str, Any],
    sections: Sequence[str],
    kind: str | None = None,
) -> JsonDict:
    """Return ``dst_data`` with every key of ``sections`` taken from the source.

    A key present in the destination but absent from the source is *removed*,
    so copying a section really makes the two instances agree on it. Keys
    outside the chosen sections (including unknown ones) are untouched.
    """
    wanted = {str(section) for section in sections}
    result: JsonDict = copy.deepcopy(dict(dst_data))
    for key in list(result):
        if _section_of(key, kind) in wanted:
            del result[key]
    for key, value in src_data.items():
        if _section_of(key, kind) in wanted:
            result[key] = copy.deepcopy(value)
    return result
