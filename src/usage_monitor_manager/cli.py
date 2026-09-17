"""Command line interface (``umm``).

The CLI is the primary consumer of the core modules: tray and window are
clients of the same functions. Implemented so far: ``--version`` and ``list``
(Phase 1). The remaining subcommands are wired up - so the surface is frozen
and testable - and exit with ``EXIT_NOT_IMPLEMENTED``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import __version__
from .model import Instance

__all__ = [
    "EXIT_NOT_IMPLEMENTED",
    "EXIT_UPDATES_AVAILABLE",
    "build_parser",
    "format_instance_table",
    "main",
]

EXIT_NOT_IMPLEMENTED = 2
"""Exit code used by every not-yet-implemented handler."""

EXIT_UPDATES_AVAILABLE = 10
"""``umm check`` exit code when at least one update is available."""

_KIND_CHOICES = ("claude", "copilot", "codex")


def _add_selection(parser: argparse.ArgumentParser) -> None:
    """Add the instance selection flags shared by start/stop/restart."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="act on every known instance")
    group.add_argument("--kind", choices=_KIND_CHOICES, help="act on every instance of this kind")
    group.add_argument("--config-dir", help="act on the instance using this config dir")
    group.add_argument("--id", dest="instance_id", help="act on the instance with this id")


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


_COLUMNS = ("KIND", "CONFIG DIR", "VERSION", "RUN", "PIDS", "SETTINGS", "AUTO", "SOURCES")


def _pids(instance: Instance) -> str:
    processes = instance.processes
    if processes is None:
        return "-"
    if processes.bootloader_pid is None:
        return str(processes.app_pid)
    return f"{processes.bootloader_pid}/{processes.app_pid}"


def _row(instance: Instance) -> tuple[str, ...]:
    label = instance.label or str(instance.config_dir)
    if instance.config_dir_unknown:
        label = f"{label} (?)"
    return (
        instance.kind.display_name,
        label,
        instance.exe_version or "?",
        "●" if instance.is_running else "○",
        _pids(instance),
        str(instance.settings_path) if instance.settings_path else "-",
        "Y" if instance.autostart_entry else "N",
        ",".join(instance.sources) or "-",
    )


def format_instance_table(instances: Sequence[Instance]) -> str:
    """Render the discovered instances as an aligned, human readable table."""
    if not instances:
        return "no usage-monitor instance found"
    rows = [_COLUMNS, *(_row(instance) for instance in instances)]
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


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``umm`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_NOT_IMPLEMENTED
    if args.command == "list":
        return _cmd_list(bool(args.json))
    print(f"umm {args.command}: not implemented")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover - module executed as a script
    raise SystemExit(main())
