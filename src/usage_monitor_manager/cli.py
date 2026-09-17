"""Command line interface (``umm``).

The CLI is the primary consumer of the core modules: tray and window are
clients of the same functions. Implemented so far: ``--version``, ``list``
(Phase 1) and ``restart`` / ``stop`` / ``start`` (Phase 2). The remaining
subcommands are wired up - so the surface is frozen and testable - and exit
with ``EXIT_NOT_IMPLEMENTED``.

Console encoding: the table marks the run state with U+25CF / U+25CB, which the
default Windows console code page (cp1252) cannot encode. We switch stdout to
UTF-8 when the stream lets us, and fall back to ASCII markers when it does not,
so ``umm list`` never dies with a ``UnicodeEncodeError``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .identity import normalize_config_dir
from .model import Instance, JsonDict

__all__ = [
    "ASCII_MARKERS",
    "EXIT_FAILED",
    "EXIT_NOT_IMPLEMENTED",
    "EXIT_NO_MATCH",
    "EXIT_UPDATES_AVAILABLE",
    "RUN_MARKERS",
    "build_parser",
    "format_instance_table",
    "main",
    "reconfigure_stdout",
    "select_instances",
]

EXIT_FAILED = 1
"""At least one operation failed."""

EXIT_NOT_IMPLEMENTED = 2
"""Exit code used by every not-yet-implemented handler."""

EXIT_NO_MATCH = 3
"""No instance matched the selectors."""

EXIT_UPDATES_AVAILABLE = 10
"""``umm check`` exit code when at least one update is available."""

_KIND_CHOICES = ("claude", "copilot", "codex")
_PROCESS_COMMANDS = ("restart", "stop", "start")


def _add_selection(parser: argparse.ArgumentParser) -> None:
    """Add the instance selection flags shared by start/stop/restart.

    The selectors are cumulative (their union is acted on) and ``--kind`` /
    ``--config-dir`` are repeatable, so ``--kind copilot --kind codex`` and
    ``--config-dir A --config-dir B`` both work.
    """
    parser.add_argument("--all", action="store_true", help="act on every known instance")
    parser.add_argument(
        "--kind",
        action="append",
        choices=_KIND_CHOICES,
        metavar="KEY",
        help="act on every instance of this kind (repeatable)",
    )
    parser.add_argument(
        "--config-dir",
        action="append",
        metavar="PATH",
        help="act on the instance using this config dir (repeatable)",
    )
    parser.add_argument("--id", dest="instance_id", metavar="KIND:SUFFIX", help="act on the instance with this id")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine readable output")


def build_parser() -> argparse.ArgumentParser:
    """Build the full ``umm`` argument parser."""
    parser = argparse.ArgumentParser(prog="umm", description="Usage Monitor Manager")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    listing = subparsers.add_parser("list", help="list discovered instances")
    _add_json(listing)

    for name, help_text in (
        ("restart", "restart instances"),
        ("stop", "stop instances"),
        ("start", "start instances"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        _add_selection(sub)
        _add_json(sub)

    check = subparsers.add_parser("check", help="check GitHub for monitor updates")
    check.add_argument("--kind", choices=_KIND_CHOICES, help="limit the check to this kind")
    _add_json(check)

    update = subparsers.add_parser("update", help="download and apply a monitor update")
    update.add_argument("--kind", choices=_KIND_CHOICES, help="update this kind")
    update.add_argument("--all", action="store_true", help="update every outdated executable")
    update.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    update.add_argument("--no-restart", action="store_true", help="do not restart the instances")
    _add_json(update)

    config = subparsers.add_parser("config", help="inspect or edit instance settings")
    config.add_argument("action", choices=("show", "edit", "validate", "set"))
    config.add_argument("key", nargs="?", help="settings key (for 'set')")
    config.add_argument("value", nargs="?", help="settings value (for 'set')")
    config.add_argument("--instance", help="target instance id")
    _add_json(config)

    self_update = subparsers.add_parser("self-update", help="update the manager itself")
    self_update.add_argument("--check-only", action="store_true", help="only report availability")
    _add_json(self_update)

    status = subparsers.add_parser("status", help="health summary for scripts and statuslines")
    _add_json(status)

    return parser


# --------------------------------------------------------------------------- #
# Output encoding
# --------------------------------------------------------------------------- #

RUN_MARKERS = ("\u25cf", "\u25cb")
"""Preferred run/stopped markers: BLACK CIRCLE / WHITE CIRCLE."""

ASCII_MARKERS = ("*", "-")
"""Fallback for a console whose code page cannot encode the circles."""


def reconfigure_stdout() -> None:
    """Switch stdout to UTF-8 when the stream supports it.

    The default Windows console hands Python a cp1252 writer, and everything
    the manager prints (paths, changelogs, the table markers) is happier in
    UTF-8. ``errors="replace"`` means a console font that cannot draw a glyph
    gets a substitute instead of a traceback. A stdout that is not a
    ``TextIOWrapper`` (pytest's ``capsys``, a wrapper installed by another
    tool) is left exactly as it is.
    """
    stream = sys.stdout
    if not isinstance(stream, io.TextIOWrapper):
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # pragma: no cover - closed or detached stream
        return


def _markers(encoding: str | None) -> tuple[str, str]:
    """Pick run-state markers that an output encoding can actually represent."""
    if not encoding:
        return ASCII_MARKERS
    try:
        "".join(RUN_MARKERS).encode(encoding)
    except (LookupError, UnicodeError):
        return ASCII_MARKERS
    return RUN_MARKERS


def _stdout_markers() -> tuple[str, str]:
    encoding = getattr(sys.stdout, "encoding", None)
    return _markers(encoding if isinstance(encoding, str) else None)


# --------------------------------------------------------------------------- #
# umm list
# --------------------------------------------------------------------------- #

_COLUMNS = ("KIND", "CONFIG DIR", "VERSION", "RUN", "PIDS", "SETTINGS", "AUTO", "SOURCES")


def _pids(instance: Instance) -> str:
    processes = instance.processes
    if processes is None:
        return "-"
    if processes.bootloader_pid is None:
        return str(processes.app_pid)
    return f"{processes.bootloader_pid}/{processes.app_pid}"


def _row(instance: Instance, markers: tuple[str, str]) -> tuple[str, ...]:
    label = instance.label or str(instance.config_dir)
    if instance.config_dir_unknown:
        label = f"{label} (?)"
    running, stopped = markers
    return (
        instance.kind.display_name,
        label,
        instance.exe_version or "?",
        running if instance.is_running else stopped,
        _pids(instance),
        str(instance.settings_path) if instance.settings_path else "-",
        "Y" if instance.autostart_entry else "N",
        ",".join(instance.sources) or "-",
    )


def format_instance_table(instances: Sequence[Instance], markers: tuple[str, str] | None = None) -> str:
    """Render the discovered instances as an aligned, human readable table.

    ``markers`` defaults to whatever ``sys.stdout`` can encode, so the table is
    printable on a cp1252 console as well as in a UTF-8 terminal.
    """
    if not instances:
        return "no usage-monitor instance found"
    chosen = markers if markers is not None else _stdout_markers()
    rows = [_COLUMNS, *(_row(instance, chosen) for instance in instances)]
    widths = [max(len(row[column]) for row in rows) for column in range(len(_COLUMNS))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows]
    return "\n".join(lines)


def _cmd_list(as_json: bool) -> int:
    from .discovery import discover  # imported lazily: psutil is not needed for --help

    instances = discover()
    if as_json:
        print(json.dumps([instance.to_dict() for instance in instances], indent=2))
    else:
        print(format_instance_table(instances))
    return 0


# --------------------------------------------------------------------------- #
# Instance selection, shared by restart / stop / start
# --------------------------------------------------------------------------- #


def _normcase(path: Path | str) -> str:
    return os.path.normcase(str(path))


def _normalize_instance_id(value: str) -> str:
    """``claude:578560bbc610``, ``claude_578560bbc610`` and ``claude`` compare equal."""
    return value.strip().casefold().replace(":", "_").rstrip("_")


def select_instances(
    instances: Sequence[Instance],
    *,
    every: bool = False,
    kind_keys: Sequence[str] | None = None,
    config_dirs: Sequence[str] | None = None,
    instance_id: str | None = None,
) -> list[Instance]:
    """Return the union of the selectors, in discovery order, without repeats.

    ``config_dirs`` go through :func:`identity.normalize_config_dir` and are
    compared with ``os.path.normcase``, so ``--config-dir
    %USERPROFILE%/.claude`` and ``--config-dir C:\\Users\\Marco\\.claude\\``
    select the same instance. ``instance_id`` accepts both the
    ``claude_578560bbc610`` spelling produced by ``Instance.instance_id`` and
    the documented ``KIND:SUFFIX`` form.
    """
    wanted_kinds = {key.casefold() for key in (kind_keys or ())}
    wanted_dirs: set[str] = set()
    for raw in config_dirs or ():
        normalized = normalize_config_dir(raw)
        if normalized is not None:
            wanted_dirs.add(_normcase(normalized))
    wanted_id = _normalize_instance_id(instance_id) if instance_id else None

    selected: list[Instance] = []
    for instance in instances:
        matches = (
            every
            or instance.kind.key.casefold() in wanted_kinds
            or _normcase(instance.config_dir) in wanted_dirs
            or (wanted_id is not None and _normalize_instance_id(instance.instance_id) == wanted_id)
        )
        if matches:
            selected.append(instance)
    return selected


# --------------------------------------------------------------------------- #
# umm restart / stop / start
# --------------------------------------------------------------------------- #


def _has_selector(args: argparse.Namespace) -> bool:
    return bool(args.all or args.kind or args.config_dir or args.instance_id)


def _already_running_row(instance: Instance) -> JsonDict:
    return {
        "instance_id": instance.instance_id,
        "kind": instance.kind.key,
        "config_dir": str(instance.config_dir),
        "action": "start",
        "ok": True,
        "detail": "already running",
        "already_running": True,
    }


def _run_process_command(command: str, targets: Sequence[Instance]) -> list[JsonDict]:
    from . import process_control

    if command == "restart":
        outcome = process_control.restart(targets)
        return [result.to_dict() for result in (*outcome.stops, *outcome.starts)]
    if command == "stop":
        return [process_control.stop(instance).to_dict() for instance in targets]

    # start: leave the live instances alone, launch the dormant ones in order
    dormant = [instance for instance in targets if not instance.is_running]
    started = {result.instance_id: result.to_dict() for result in process_control.start_missing(dormant)}
    rows: list[JsonDict] = []
    for instance in targets:
        if instance.is_running:
            rows.append(_already_running_row(instance))
        else:
            rows.append(started.get(instance.instance_id, _already_running_row(instance)))
    return rows


def _dry_run_plan(command: str, targets: Sequence[Instance]) -> list[str]:
    """What the command would do, without touching a single process."""
    from . import process_control

    lines: list[str] = []
    for instance in targets:
        if command in ("restart", "stop"):
            lines.append(process_control.describe_stop(instance))
        if command in ("restart", "start"):
            if command == "start" and instance.is_running:
                lines.append(f"would skip {instance.instance_id}: already running")
            else:
                lines.append(process_control.describe_start(instance))
    return lines


def _cmd_process(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from .discovery import discover

    command = str(args.command)
    if not _has_selector(args):
        parser.error(f"{command}: pick at least one of --all, --kind, --config-dir, --id")

    targets = select_instances(
        discover(),
        every=bool(args.all),
        kind_keys=args.kind,
        config_dirs=args.config_dir,
        instance_id=args.instance_id,
    )
    if not targets:
        print("no instance matched the given selectors")
        return EXIT_NO_MATCH

    if args.dry_run:
        lines = _dry_run_plan(command, targets)
        if args.json:
            print(json.dumps({"action": command, "dry_run": True, "plan": lines}, indent=2))
        else:
            for line in lines:
                print(line)
        return 0

    rows = _run_process_command(command, targets)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            status = "ok  " if row.get("ok") else "FAIL"
            print(f"{status} {row.get('action')} {row.get('instance_id')}: {row.get('detail')}")
    return EXIT_FAILED if any(not row.get("ok") for row in rows) else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``umm`` console script."""
    reconfigure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_NOT_IMPLEMENTED
    if args.command == "list":
        return _cmd_list(bool(args.json))
    if args.command in _PROCESS_COMMANDS:
        return _cmd_process(args, parser)
    print(f"umm {args.command}: not implemented")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover - module executed as a script
    raise SystemExit(main())
