"""Command line interface (``umm``).

The CLI is the primary consumer of the core modules: tray and window are
clients of the same functions. Implemented so far: ``--version``, ``list``
(Phase 1), ``restart`` / ``stop`` / ``start`` (Phase 2),
``check`` / ``update`` / ``rollback`` (Phase 3) and
``tray`` / ``import-legacy`` / ``autostart`` (Phase 4). The remaining
subcommands are wired up - so the surface is frozen and testable - and exit
with ``EXIT_NOT_IMPLEMENTED``.

``umm update`` prints the release notes *before* the download starts and then
redraws a single progress line in place, so the changelog stays readable for
the whole download; on a non-TTY stdout the progress becomes plain lines
instead (the PowerShell updater behaved the same way).

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
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .identity import normalize_config_dir
from .model import Instance, JsonDict, UpdatePlan
from .versions import display_version

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .updater import UpdateEvent

__all__ = [
    "ASCII_MARKERS",
    "EXIT_FAILED",
    "EXIT_NOT_IMPLEMENTED",
    "EXIT_NETWORK",
    "EXIT_NO_MATCH",
    "EXIT_UPDATES_AVAILABLE",
    "RUN_MARKERS",
    "build_parser",
    "convert_markdown_links",
    "format_check_table",
    "format_instance_table",
    "main",
    "reconfigure_stdout",
    "render_changelog",
    "select_instances",
]

EXIT_FAILED = 1
"""At least one operation failed."""

EXIT_NOT_IMPLEMENTED = 2
"""Exit code used by every not-yet-implemented handler."""

EXIT_NO_MATCH = 3
"""No instance matched the selectors."""

EXIT_NETWORK = 3
"""``umm check`` / ``umm update`` could not reach GitHub (or hit the rate limit)."""

EXIT_UPDATES_AVAILABLE = 10
"""``umm check`` exit code when at least one update is available."""

_KIND_CHOICES = ("claude", "copilot", "codex")
_PROCESS_COMMANDS = ("restart", "stop", "start")
_AUTOSTART_ACTIONS = ("on", "off", "status")


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
    check.add_argument(
        "--kind",
        action="append",
        choices=_KIND_CHOICES,
        metavar="KEY",
        help="limit the check to this kind (repeatable)",
    )
    check.add_argument("--changelog", action="store_true", help="also print the release notes")
    check.add_argument("--prerelease", action="store_true", help="consider prereleases too")
    _add_json(check)

    update = subparsers.add_parser("update", help="download and apply a monitor update")
    update.add_argument(
        "--kind",
        action="append",
        choices=_KIND_CHOICES,
        metavar="KEY",
        help="update this kind (repeatable)",
    )
    update.add_argument("--all", action="store_true", help="update every outdated executable")
    update.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    update.add_argument("--no-restart", action="store_true", help="do not restart the instances")
    update.add_argument("--dry-run", action="store_true", help="print every step, change nothing")
    update.add_argument("--prerelease", action="store_true", help="consider prereleases too")
    _add_json(update)

    rollback = subparsers.add_parser("rollback", help="restore the previous executable from its .bak")
    rollback.add_argument(
        "--kind",
        action="append",
        choices=_KIND_CHOICES,
        metavar="KEY",
        required=True,
        help="roll back this kind's executable (repeatable)",
    )
    rollback.add_argument("--no-restart", action="store_true", help="do not restart the instances")
    _add_json(rollback)

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

    subparsers.add_parser("tray", help="run the tray icon (the default when started with no arguments)")

    import_legacy = subparsers.add_parser(
        "import-legacy",
        help="import shortcuts, Run entries and the old config editor's profiles",
    )
    import_legacy.add_argument("--dry-run", action="store_true", help="print what would be imported, change nothing")
    _add_json(import_legacy)

    autostart = subparsers.add_parser("autostart", help="inspect or change autostart with Windows")
    target = autostart.add_mutually_exclusive_group(required=True)
    target.add_argument("--manager", nargs="?", const="status", choices=_AUTOSTART_ACTIONS, metavar="ACTION")
    target.add_argument("--instance", metavar="ID", help="instance id (see 'umm list')")
    autostart.add_argument(
        "action",
        nargs="?",
        choices=_AUTOSTART_ACTIONS,
        default="status",
        help="on | off | status (default: status)",
    )
    _add_json(autostart)

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
        display_version(instance.exe_version) or "?",
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


# --------------------------------------------------------------------------- #
# umm check / update / rollback
# --------------------------------------------------------------------------- #

_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")


def convert_markdown_links(text: str) -> str:
    """Render markdown links as ``text (url)`` and images as their alt text.

    Port of ``Convert-MarkdownLink`` from the PowerShell updater, with one
    deliberate difference: that script dropped the URL, while a console user
    reading release notes usually wants it. Images keep only their alt text -
    the URL of a screenshot is noise in a terminal.
    """
    without_images = _IMAGE_LINK.sub(r"\1", text)
    return _INLINE_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1), without_images)


def render_changelog(body: str) -> list[str]:
    """Release notes as plain text lines: headings, bullets, code kept verbatim."""
    if not body.strip():
        return ["  no release notes were published for this version"]
    lines: list[str] = []
    in_code = False
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            lines.append(f"    {line}")
            continue
        line = convert_markdown_links(line)
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        heading = re.match(r"^#{1,6}\s*(.+)$", stripped)
        if heading:
            lines.extend(["", f"  {heading.group(1).strip()}"])
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            lines.append(f"  - {bullet.group(1).strip()}")
            continue
        lines.append(f"  {stripped}")
    return lines


def _print_changelog(plan: UpdatePlan) -> None:
    print("")
    print(f"What's new in {plan.kind.display_name} {plan.target.tag}  ({plan.target.html_url})")
    for line in render_changelog(plan.target.body_markdown):
        print(line)
    print("")


_CHECK_COLUMNS = ("KIND", "EXE", "LOCAL", "LATEST", "STATUS", "PUBLISHED")


def _published(plan: UpdatePlan) -> str:
    published = plan.target.published_at
    return "-" if published is None else published.astimezone().strftime("%Y-%m-%d")


def format_check_table(plans: Sequence[UpdatePlan]) -> str:
    """Render the check result: kind, exe, local, latest, status, published date."""
    from .updater import plan_status

    if not plans:
        return "no usage-monitor executable to check"
    rows: list[tuple[str, ...]] = [_CHECK_COLUMNS]
    for plan in plans:
        rows.append(
            (
                plan.kind.display_name,
                str(plan.exe_path),
                display_version(plan.current_version) or "?",
                plan.target.version or "?",
                str(plan_status(plan)),
                _published(plan),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(_CHECK_COLUMNS))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows
    )


def _build_plans(kinds: Sequence[str] | None, *, prerelease: bool) -> list[UpdatePlan]:
    from .discovery import discover
    from .updater import plan_updates

    return list(plan_updates(discover(), kinds=kinds, include_prerelease=prerelease))


def _cmd_check(args: argparse.Namespace) -> int:
    from .github import GitHubError
    from .updater import plan_status
    from .versions import VersionStatus

    try:
        plans = _build_plans(args.kind, prerelease=bool(args.prerelease))
    except GitHubError as error:
        print(f"umm check: {error}", file=sys.stderr)
        return EXIT_NETWORK

    if args.json:
        payload: list[JsonDict] = []
        for plan in plans:
            entry = plan.to_dict()
            entry["status"] = str(plan_status(plan))
            payload.append(entry)
        print(json.dumps(payload, indent=2))
    else:
        print(format_check_table(plans))
        if args.changelog:
            for plan in plans:
                _print_changelog(plan)
    return EXIT_UPDATES_AVAILABLE if any(plan_status(p) is VersionStatus.UPDATE_AVAILABLE for p in plans) else 0


class _ProgressPrinter:
    """One line redrawn in place on a TTY, plain lines on a pipe or a log file."""

    def __init__(self, *, tty: bool) -> None:
        self.tty = tty
        self._last = -1.0
        self._active = False

    def __call__(self, event: UpdateEvent) -> None:
        if event.step == "download" and event.progress is not None:
            percent = round(event.progress * 100)
            if self.tty:
                print(f"\r  downloading {percent:3d}%", end="", flush=True)
                self._active = True
                return
            if percent < 100 and percent - self._last < 25:
                return
            self._last = percent
            print(f"  downloading {percent:3d}%")
            return
        if self._active:
            print("")
            self._active = False
        print(f"  {event.message}")


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ").strip().casefold()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _cmd_update(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from .github import GitHubError
    from .updater import apply as apply_plan
    from .updater import describe, plan_status
    from .versions import VersionStatus

    if not args.kind and not args.all:
        parser.error("update: pick --kind KEY or --all")

    try:
        plans = _build_plans(args.kind, prerelease=bool(args.prerelease))
    except GitHubError as error:
        print(f"umm update: {error}", file=sys.stderr)
        return EXIT_NETWORK

    outdated = [p for p in plans if plan_status(p) is VersionStatus.UPDATE_AVAILABLE]
    if not outdated:
        message = "nothing to update: every executable is already up to date"
        print(json.dumps({"action": "update", "plans": [], "detail": message}, indent=2) if args.json else message)
        return 0

    restart = not args.no_restart
    if args.dry_run:
        plan_lines: list[JsonDict] = []
        for plan in outdated:
            steps = describe(plan, restart=restart)
            plan_lines.append({"kind": plan.kind.key, "exe_path": str(plan.exe_path), "steps": steps})
            if not args.json:
                _print_changelog(plan)
                for line in steps:
                    print(f"  {line}")
        if args.json:
            print(json.dumps({"action": "update", "dry_run": True, "plans": plan_lines}, indent=2))
        return 0

    results: list[JsonDict] = []
    failed = False
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    for plan in outdated:
        if not args.json:
            _print_changelog(plan)  # printed before the download, as the old updater did
        if not args.yes and not _confirm(f"update {plan.kind.display_name} to {plan.target.version}?"):
            print("skipped")
            continue
        printer = _ProgressPrinter(tty=tty) if not args.json else None
        result = apply_plan(
            plan,
            on_event=printer,
            confirm_unverified=(lambda: True)
            if args.yes
            else (lambda: _confirm("no sha256 published; install anyway?")),
            restart=restart,
        )
        results.append(result.to_dict())
        failed = failed or not result.ok
        if not args.json:
            print(f"{'ok  ' if result.ok else 'FAIL'} {plan.kind.key}: {result.detail}")
    if args.json:
        print(json.dumps(results, indent=2))
    return EXIT_FAILED if failed else 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    from .discovery import discover
    from .kinds import get_kind
    from .updater import rollback as rollback_exe

    instances = discover()
    results: list[JsonDict] = []
    failed = False
    matched = False
    for key in args.kind:
        kind = get_kind(key)
        exe_paths: list[Path] = []
        for instance in instances:
            if instance.kind.key == kind.key and instance.exe_path is not None:
                if all(_normcase(instance.exe_path) != _normcase(known) for known in exe_paths):
                    exe_paths.append(instance.exe_path)
        if not exe_paths:
            print(f"no {kind.display_name} executable found", file=sys.stderr)
            continue
        matched = True
        for exe_path in exe_paths:
            result = rollback_exe(
                exe_path,
                instances=instances,
                restart=not args.no_restart,
                on_event=None if args.json else lambda event: print(f"  {event.message}"),
            )
            results.append(result.to_dict())
            failed = failed or not result.ok
            if not args.json:
                print(f"{'ok  ' if result.ok else 'FAIL'} {kind.key}: {result.detail}")
    if args.json:
        print(json.dumps(results, indent=2))
    if not matched:
        return EXIT_NO_MATCH
    return EXIT_FAILED if failed else 0


# --------------------------------------------------------------------------- #
# Phase 4: tray, legacy import, autostart
# --------------------------------------------------------------------------- #


def _cmd_tray() -> int:
    from .ui.tray import run_tray

    return run_tray()


def _cmd_import_legacy(args: argparse.Namespace) -> int:
    from .legacy import import_legacy

    report = import_legacy(dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        if args.dry_run:
            print("umm import-legacy --dry-run: nessuna modifica verra' scritta")
        for line in report.lines():
            print(line)
    return 0


def _autostart_payload(name: str, command: str | None) -> JsonDict:
    return {"target": name, "enabled": command is not None, "command": command}


def _cmd_autostart_manager(action: str, as_json: bool) -> int:
    from .platform import get_manager_autostart, set_manager_autostart

    if action == "status":
        command = get_manager_autostart()
    else:
        try:
            command = set_manager_autostart(action == "on")
        except OSError as error:
            print(f"umm autostart: {error}", file=sys.stderr)
            return EXIT_FAILED
    if as_json:
        print(json.dumps(_autostart_payload("manager", command), indent=2))
    else:
        state = f"si ({command})" if command else "no"
        print(f"manager: avvio con Windows: {state}")
    return 0


def _cmd_autostart_instance(instance_id: str, action: str, as_json: bool) -> int:
    from . import process_control
    from .discovery import discover

    wanted = _normalize_instance_id(instance_id)
    matches = [i for i in discover() if _normalize_instance_id(i.instance_id) == wanted]
    if not matches:
        print(f"umm autostart: nessuna istanza con id {instance_id!r}", file=sys.stderr)
        return EXIT_NO_MATCH
    instance = matches[0]
    try:
        if action == "status":
            command = process_control.get_instance_autostart(instance)
        else:
            command = process_control.set_instance_autostart(instance, action == "on")
    except (OSError, ValueError) as error:
        print(f"umm autostart: {error}", file=sys.stderr)
        return EXIT_FAILED
    if as_json:
        print(json.dumps(_autostart_payload(instance.instance_id, command), indent=2))
    else:
        state = f"si ({command})" if command else "no"
        print(f"{instance.instance_id}: avvio con Windows: {state}")
    return 0


def _cmd_autostart(args: argparse.Namespace) -> int:
    as_json = bool(args.json)
    if args.instance:
        return _cmd_autostart_instance(args.instance, args.action, as_json)
    action = args.manager if args.manager != "status" else args.action
    return _cmd_autostart_manager(action, as_json)


def _attach_console_if_frozen(args: Sequence[str]) -> None:
    """A windowed build asked to run a CLI command borrows the caller's console.

    ``UsageMonitorManager.exe`` is built with ``--noconsole`` (it is a tray
    app), so without this ``UsageMonitorManager.exe --version`` would print
    into the void. The tray path deliberately does not attach: starting the
    tray from Explorer must not pop a console up.
    """
    if not getattr(sys, "frozen", False):
        return
    if not args or args[0] in ("tray", "--tray"):
        return
    from .platform import attach_parent_console

    attach_parent_console()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``umm`` console script."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    _attach_console_if_frozen(raw_args)
    reconfigure_stdout()
    parser = build_parser()
    raw = raw_args
    # PLAN.md section 6.5 writes the autostart command as `"<exe>" --tray`.
    args = parser.parse_args(["tray" if token == "--tray" else token for token in raw])
    if args.command is None:
        parser.print_help()
        return EXIT_NOT_IMPLEMENTED
    if args.command == "tray":
        return _cmd_tray()
    if args.command == "list":
        return _cmd_list(bool(args.json))
    if args.command in _PROCESS_COMMANDS:
        return _cmd_process(args, parser)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "update":
        return _cmd_update(args, parser)
    if args.command == "rollback":
        return _cmd_rollback(args)
    if args.command == "import-legacy":
        return _cmd_import_legacy(args)
    if args.command == "autostart":
        return _cmd_autostart(args)
    print(f"umm {args.command}: not implemented")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover - module executed as a script
    raise SystemExit(main())
