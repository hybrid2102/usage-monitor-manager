"""Command line interface (``umm``).

The CLI is the primary consumer of the core modules: tray and window are
clients of the same functions. Implemented so far: ``--version``, ``list``
(Phase 1), ``restart`` / ``stop`` / ``start`` (Phase 2),
``check`` / ``update`` / ``rollback`` (Phase 3),
``tray`` / ``import-legacy`` / ``autostart`` (Phase 4), ``config`` (Phase 5)
and ``self-update`` / ``token`` / ``archive`` / ``skip`` / ``unskip`` /
``status`` plus ``--post-update`` (Phase 6).

Two of them are meant for a machine rather than a person.
``umm update --all --yes`` is the Task Scheduler entry point of PLAN.md
section 9.10: it never prompts, refuses an executable GitHub publishes no
sha256 for (exit 4) instead of asking, and with ``--log FILE`` appends one JSON
object per step so a scheduled run leaves an audit trail.
``umm status --json`` (section 9.11) is the statusline entry point: instances,
the stored update bookkeeping and a watchdog pass, without a single network
call unless ``--check`` is given; ``--write-file`` drops the same document on
disk for a consumer that cannot afford to start Python at all.

Phase 7 adds three more command groups: ``umm kind`` edits the registry of
"portable exe from GitHub Releases" entries (section 9.9), ``umm diag``
restarts one instance with ``--verbose`` and its output on a file
(section 9.7), and ``umm profile`` applies a named set of instances
(section 9.13).

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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, errors
from .i18n import T, t
from .identity import normalize_config_dir
from .model import Instance, JsonDict, UpdatePlan
from .versions import display_version

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .settings.store import ValidationReport
    from .updater import UpdateEvent

__all__ = [
    "ASCII_MARKERS",
    "build_plans",
    "skipped_plans",
    "format_kind_table",
    "EXIT_FAILED",
    "EXIT_NOT_IMPLEMENTED",
    "EXIT_NETWORK",
    "EXIT_NO_MATCH",
    "EXIT_UNVERIFIED",
    "EXIT_UPDATES_AVAILABLE",
    "RUN_MARKERS",
    "format_archive_table",
    "build_parser",
    "convert_markdown_links",
    "format_check_table",
    "format_instance_table",
    "EXIT_OK",
    "main",
    "reconfigure_stdout",
    "render_changelog",
    "select_instances",
]

# The exit codes live in errors.py, next to the exception hierarchy that maps
# onto them (PLAN.md section 7, phase 8); they are re-exported here because
# `from .cli import EXIT_FAILED` is what every caller and test already writes.
EXIT_OK = errors.EXIT_OK
"""Everything the command was asked to do succeeded."""

EXIT_FAILED = errors.EXIT_FAILED
"""At least one operation failed."""

EXIT_NOT_IMPLEMENTED = errors.EXIT_NOT_IMPLEMENTED
"""Exit code used by every not-yet-implemented handler."""

EXIT_NO_MATCH = errors.EXIT_NO_MATCH
"""No instance matched the selectors."""

EXIT_NETWORK = errors.EXIT_NETWORK
"""``umm check`` / ``umm update`` could not reach GitHub (or hit the rate limit)."""

EXIT_UNVERIFIED = errors.EXIT_UNVERIFIED
"""An update was skipped because GitHub publishes no sha256 for its asset.

Only produced by the unattended path (``--yes``): interactively the user is
asked, and ``--allow-unverified`` opts back in explicitly.
"""

EXIT_UPDATES_AVAILABLE = errors.EXIT_UPDATES_AVAILABLE
"""``umm check`` exit code when at least one update is available."""


def _kind_choices() -> tuple[str, ...]:
    """The registered kind keys, custom ones included (PLAN.md section 9.9).

    Read when the parser is built, which happens after
    :func:`kinds.reload` in :func:`main`: a kind the user added with
    ``umm kind add`` is a valid ``--kind`` value in the same breath.
    """
    from . import kinds as _kinds

    return _kinds.kind_keys()


def _skip_choices() -> tuple[str, ...]:
    """``umm skip`` also accepts ``manager``: the manager skips its own releases."""
    return (*_kind_choices(), "manager")


_KIND_ACTIONS = ("list", "add", "remove")
_DIAG_ACTIONS = ("start", "stop", "show")
_PROFILE_ACTIONS = ("list", "add", "remove", "apply", "show")
_TOKEN_ACTIONS = ("set", "clear", "status")
_ARCHIVE_ACTIONS = ("list", "prune")
_PROCESS_COMMANDS = ("restart", "stop", "start")
_AUTOSTART_ACTIONS = ("on", "off", "status")


def _add_selection(parser: argparse.ArgumentParser) -> None:
    """Add the instance selection flags shared by start/stop/restart.

    The selectors are cumulative (their union is acted on) and ``--kind`` /
    ``--config-dir`` are repeatable, so ``--kind copilot --kind codex`` and
    ``--config-dir A --config-dir B`` both work.
    """
    parser.add_argument("--all", action="store_true", help=T["cli.help.select_all"])
    parser.add_argument(
        "--kind",
        action="append",
        choices=_kind_choices(),
        metavar="KEY",
        help=T["cli.help.select_kind"],
    )
    parser.add_argument(
        "--config-dir",
        action="append",
        metavar="PATH",
        help=T["cli.help.select_config_dir"],
    )
    parser.add_argument("--id", dest="instance_id", metavar="KIND:SUFFIX", help=T["cli.help.select_id"])
    parser.add_argument("--dry-run", action="store_true", help=T["cli.help.dry_run"])


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help=T["cli.help.json"])


def build_parser() -> argparse.ArgumentParser:
    """Build the full ``umm`` argument parser."""
    parser = argparse.ArgumentParser(prog="umm", description=T["app.name"])
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--debug", action="store_true", help=T["cli.help.debug"])
    parser.add_argument(
        "--post-update",
        metavar="PID",
        type=int,
        help=T["cli.help.post_update"],
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    listing = subparsers.add_parser("list", help=T["cli.help.cmd_list"])
    _add_json(listing)

    for name, help_text in (
        ("restart", T["cli.help.cmd_restart"]),
        ("stop", T["cli.help.cmd_stop"]),
        ("start", T["cli.help.cmd_start"]),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        _add_selection(sub)
        _add_json(sub)

    check = subparsers.add_parser("check", help=T["cli.help.cmd_check"])
    check.add_argument(
        "--kind",
        action="append",
        choices=_kind_choices(),
        metavar="KEY",
        help=T["cli.help.check_kind"],
    )
    check.add_argument("--changelog", action="store_true", help=T["cli.help.changelog"])
    check.add_argument("--prerelease", action="store_true", help=T["cli.help.prerelease"])
    check.add_argument(
        "--include-skipped",
        action="store_true",
        help=T["cli.help.include_skipped"],
    )
    _add_json(check)

    update = subparsers.add_parser("update", help=T["cli.help.cmd_update"])
    update.add_argument(
        "--kind",
        action="append",
        choices=_kind_choices(),
        metavar="KEY",
        help=T["cli.help.update_kind"],
    )
    update.add_argument("--all", action="store_true", help=T["cli.help.update_all"])
    update.add_argument("--yes", action="store_true", help=T["cli.help.yes"])
    update.add_argument("--no-restart", action="store_true", help=T["cli.help.no_restart"])
    update.add_argument("--dry-run", action="store_true", help=T["cli.help.dry_run_steps"])
    update.add_argument("--prerelease", action="store_true", help=T["cli.help.prerelease"])
    update.add_argument(
        "--include-skipped",
        action="store_true",
        help=T["cli.help.update_skipped"],
    )
    update.add_argument(
        "--allow-unverified",
        action="store_true",
        help=T["cli.help.allow_unverified"],
    )
    update.add_argument("--log", metavar="FILE", help=T["cli.help.log_file"])
    _add_json(update)

    rollback = subparsers.add_parser("rollback", help=T["cli.help.cmd_rollback"])
    rollback.add_argument(
        "--kind",
        action="append",
        choices=_kind_choices(),
        metavar="KEY",
        required=True,
        help=T["cli.help.rollback_kind"],
    )
    rollback.add_argument(
        "--version",
        dest="target_version",
        metavar="V",
        help=T["cli.help.rollback_version"],
    )
    rollback.add_argument("--no-restart", action="store_true", help=T["cli.help.no_restart"])
    _add_json(rollback)

    archive = subparsers.add_parser("archive", help=T["cli.help.cmd_archive"])
    archive.add_argument("action", choices=_ARCHIVE_ACTIONS)
    archive.add_argument(
        "--kind",
        action="append",
        choices=_kind_choices(),
        metavar="KEY",
        help=T["cli.help.limit_kind"],
    )
    archive.add_argument("--keep", type=int, metavar="N", help=T["cli.help.archive_keep"])
    _add_json(archive)

    for name, help_text in (
        ("skip", T["cli.help.cmd_skip"]),
        ("unskip", T["cli.help.cmd_unskip"]),
    ):
        skip = subparsers.add_parser(name, help=help_text)
        skip.add_argument(
            "--kind",
            action="append",
            choices=_skip_choices(),
            metavar="KEY",
            required=True,
            help=T["cli.help.kind_or_manager"],
        )
        _add_json(skip)

    token = subparsers.add_parser("token", help=T["cli.help.cmd_token"])
    token.add_argument("action", choices=_TOKEN_ACTIONS)
    token.add_argument(
        "--from-env",
        metavar="VAR",
        help=T["cli.help.token_from_env"],
    )
    _add_json(token)

    config = subparsers.add_parser("config", help=T["cli.help.cmd_config"])
    config.add_argument("action", choices=CONFIG_ACTIONS)
    config.add_argument(
        "args",
        nargs="*",
        metavar="ARG",
        help=T["cli.help.config_args"],
    )
    config.add_argument("--instance", metavar="ID", help=T["cli.help.target_instance"])
    _add_json(config)

    self_update = subparsers.add_parser("self-update", help=T["cli.help.cmd_self_update"])
    self_update.add_argument("--check-only", action="store_true", help=T["cli.help.check_only"])
    self_update.add_argument("--yes", action="store_true", help=T["cli.help.yes"])
    self_update.add_argument("--dry-run", action="store_true", help=T["cli.help.dry_run_steps"])
    self_update.add_argument("--prerelease", action="store_true", help=T["cli.help.prerelease"])
    _add_json(self_update)

    status = subparsers.add_parser("status", help=T["cli.help.cmd_status"])
    status.add_argument("--check", action="store_true", help=T["cli.help.status_check"])
    status.add_argument(
        "--write-file",
        nargs="?",
        const="",
        metavar="PATH",
        dest="write_file",
        help=T["cli.help.write_file"],
    )
    _add_json(status)

    kind = subparsers.add_parser("kind", help=T["cli.help.cmd_kind"])
    kind.add_argument("action", choices=_KIND_ACTIONS)
    kind.add_argument("key", nargs="?", metavar="KEY", help=T["cli.help.kind_key"])
    kind.add_argument("--display-name", metavar="NAME", help=T["cli.help.kind_display"])
    kind.add_argument("--exe-name", metavar="FILE", help=T["cli.help.kind_exe"])
    kind.add_argument("--repo", metavar="OWNER/NAME", help=T["cli.help.kind_repo"])
    kind.add_argument("--asset-pattern", metavar="NAME", help=T["cli.help.kind_asset"])
    kind.add_argument("--sha-sidecar", action="store_true", help=T["cli.help.kind_sidecar"])
    kind.add_argument("--pe-product-name", metavar="NAME", help=T["cli.help.kind_product"])
    _add_json(kind)

    diag = subparsers.add_parser("diag", help=T["cli.help.cmd_diag"])
    diag.add_argument("action", choices=_DIAG_ACTIONS)
    diag.add_argument("--instance", metavar="ID", help=T["cli.help.instance_id"])
    diag.add_argument("--lines", type=int, default=200, metavar="N", help=T["cli.help.diag_lines"])
    diag.add_argument("--dry-run", action="store_true", help=T["cli.help.dry_run"])
    _add_json(diag)

    profile = subparsers.add_parser("profile", help=T["cli.help.cmd_profile"])
    profile.add_argument("action", choices=_PROFILE_ACTIONS)
    profile.add_argument("name", nargs="?", metavar="NAME", help=T["cli.help.profile_name"])
    profile.add_argument(
        "--instance",
        action="append",
        dest="instance_ids",
        metavar="ID",
        help=T["cli.help.profile_instance"],
    )
    profile.add_argument("--yes", action="store_true", help=T["cli.help.profile_yes"])
    profile.add_argument("--dry-run", action="store_true", help=T["cli.help.profile_dry_run"])
    profile.add_argument(
        "--no-stop-others",
        action="store_true",
        help=T["cli.help.profile_no_stop"],
    )
    _add_json(profile)

    subparsers.add_parser("tray", help=T["cli.help.cmd_tray"])

    import_legacy = subparsers.add_parser(
        "import-legacy",
        help=T["cli.help.cmd_import_legacy"],
    )
    import_legacy.add_argument("--dry-run", action="store_true", help=T["cli.help.dry_run_import"])
    _add_json(import_legacy)

    autostart = subparsers.add_parser("autostart", help=T["cli.help.cmd_autostart"])
    target = autostart.add_mutually_exclusive_group(required=True)
    target.add_argument("--manager", nargs="?", const="status", choices=_AUTOSTART_ACTIONS, metavar="ACTION")
    target.add_argument("--instance", metavar="ID", help=T["cli.help.instance_id"])
    autostart.add_argument(
        "action",
        nargs="?",
        choices=_AUTOSTART_ACTIONS,
        default="status",
        help=T["cli.help.autostart_action"],
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
        parser.error(t("cli.pick_a_selector", command=command))

    targets = select_instances(
        discover(),
        every=bool(args.all),
        kind_keys=args.kind,
        config_dirs=args.config_dir,
        instance_id=args.instance_id,
    )
    if not targets:
        print(T["cli.no_match"])
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
    print(
        t(
            "cli.whats_new",
            name=plan.kind.display_name,
            tag=plan.target.tag,
            url=plan.target.html_url,
        )
    )
    for line in render_changelog(plan.target.body_markdown):
        print(line)
    print("")


_CHECK_COLUMNS = ("KIND", "EXE", "LOCAL", "LATEST", "STATUS", "PUBLISHED")


def _published(plan: UpdatePlan) -> str:
    published = plan.target.published_at
    return "-" if published is None else published.astimezone().strftime("%Y-%m-%d")


def format_check_table(plans: Sequence[UpdatePlan], skipped: Sequence[str] = ()) -> str:
    """Render the check result: kind, exe, local, latest, status, published date.

    ``skipped`` holds the executable paths whose pending release the user chose
    to skip (PLAN.md section 9.5); their status reads ``skipped`` instead of
    ``update_available``, which is what makes the decision visible.
    """
    from .updater import plan_status

    if not plans:
        return "no usage-monitor executable to check"
    ignored = {_normcase(path) for path in skipped}
    rows: list[tuple[str, ...]] = [_CHECK_COLUMNS]
    for plan in plans:
        status = "skipped" if _normcase(plan.exe_path) in ignored else str(plan_status(plan))
        rows.append(
            (
                plan.kind.display_name,
                str(plan.exe_path),
                display_version(plan.current_version) or "?",
                plan.target.version or "?",
                status,
                _published(plan),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(_CHECK_COLUMNS))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows
    )


def build_plans(kinds: Sequence[str] | None, *, prerelease: bool) -> list[UpdatePlan]:
    from . import manager_config
    from .discovery import discover
    from .updater import plan_updates

    config = manager_config.load()
    return list(
        plan_updates(
            discover(),
            kinds=kinds,
            include_prerelease=prerelease,
            prerelease_for=None if prerelease else config.include_prerelease_for,
        )
    )


def skipped_plans(plans: Sequence[UpdatePlan]) -> list[UpdatePlan]:
    """The plans whose pending tag the user asked never to be offered again."""
    from . import manager_config
    from .updater import plan_status
    from .versions import VersionStatus

    config = manager_config.load()
    return [
        plan
        for plan in plans
        if plan_status(plan) is VersionStatus.UPDATE_AVAILABLE and config.is_skipped(plan.kind.repo, plan.target.tag)
    ]


def _cmd_check(args: argparse.Namespace) -> int:
    from .github import GitHubError
    from .updater import plan_status
    from .versions import VersionStatus

    try:
        plans = build_plans(args.kind, prerelease=bool(args.prerelease))
    except GitHubError as error:
        print(t("cli.check_error", error=error), file=sys.stderr)
        return EXIT_NETWORK

    skipped = [] if args.include_skipped else skipped_plans(plans)
    ignored = {_normcase(plan.exe_path) for plan in skipped}
    if args.json:
        payload: list[JsonDict] = []
        for plan in plans:
            entry = plan.to_dict()
            is_skipped = _normcase(plan.exe_path) in ignored
            entry["status"] = "skipped" if is_skipped else str(plan_status(plan))
            entry["skipped"] = is_skipped
            payload.append(entry)
        print(json.dumps(payload, indent=2))
    else:
        print(format_check_table(plans, [str(plan.exe_path) for plan in skipped]))
        if args.changelog:
            for plan in plans:
                _print_changelog(plan)
    available = [
        plan
        for plan in plans
        if plan_status(plan) is VersionStatus.UPDATE_AVAILABLE and _normcase(plan.exe_path) not in ignored
    ]
    return EXIT_UPDATES_AVAILABLE if available else 0


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
                print("\r" + t("cli.downloading", percent=percent), end="", flush=True)
                self._active = True
                return
            if percent < 100 and percent - self._last < 25:
                return
            self._last = percent
            print(t("cli.downloading", percent=percent))
            return
        if self._active:
            print("")
            self._active = False
        print(f"  {event.message}")


class _StepLog:
    """Append one JSON object per step to ``--log FILE`` (PLAN.md section 9.10).

    A scheduled update runs with no console attached, so the log file is the
    only record there is. Every line is self-contained (timestamp, executable,
    step, message) and a file that cannot be written degrades to a warning on
    stderr rather than aborting the update.
    """

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self._failed = False

    def write(self, payload: JsonDict) -> None:
        if self.path is None or self._failed:
            return
        from datetime import UTC, datetime

        entry = {"ts": datetime.now(tz=UTC).isoformat(), **payload}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            self._failed = True
            print(t("cli.log_not_written", path=self.path, error=error), file=sys.stderr)

    def event(self, plan: UpdatePlan, event: UpdateEvent) -> None:
        self.write(
            {
                "kind": plan.kind.key,
                "exe_path": str(plan.exe_path),
                "step": event.step,
                "message": event.message,
                "progress": event.progress,
            }
        )


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
        parser.error(T["cli.update_pick_kind"])

    try:
        plans = build_plans(args.kind, prerelease=bool(args.prerelease))
    except GitHubError as error:
        print(t("cli.update_error", error=error), file=sys.stderr)
        return EXIT_NETWORK

    outdated = [p for p in plans if plan_status(p) is VersionStatus.UPDATE_AVAILABLE]
    step_log = _StepLog(getattr(args, "log", None))
    if not args.include_skipped:
        ignored = {_normcase(plan.exe_path) for plan in skipped_plans(plans)}
        for plan in [p for p in outdated if _normcase(p.exe_path) in ignored]:
            message = f"{plan.kind.key}: {plan.target.tag} skipped by the user (--include-skipped to override)"
            step_log.write({"kind": plan.kind.key, "step": "skip", "message": message})
            if not args.json:
                print(message)
        outdated = [p for p in outdated if _normcase(p.exe_path) not in ignored]
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
    unverified = False
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    for plan in outdated:
        if not args.json:
            _print_changelog(plan)  # printed before the download, as the old updater did
        if not args.yes and not _confirm(f"update {plan.kind.display_name} to {plan.target.version}?"):
            print(T["cli.skipped"])
            continue
        printer = _ProgressPrinter(tty=tty) if not args.json else None

        def report(
            event: UpdateEvent,
            current: UpdatePlan = plan,
            show: _ProgressPrinter | None = printer,
        ) -> None:
            step_log.event(current, event)
            if show is not None:
                show(event)

        result = apply_plan(
            plan,
            on_event=report,
            confirm_unverified=_unverified_policy(args),
            restart=restart,
        )
        results.append(result.to_dict())
        skipped_unverified = (
            not result.ok and result.integrity is not None and result.integrity.unverifiable and not result.stopped
        )
        unverified = unverified or skipped_unverified
        failed = failed or (not result.ok and not skipped_unverified)
        step_log.write(
            {
                "kind": plan.kind.key,
                "exe_path": str(plan.exe_path),
                "step": "result",
                "ok": result.ok,
                "message": result.detail,
                "from_version": result.from_version,
                "to_version": result.to_version,
            }
        )
        if not args.json:
            print(f"{'ok  ' if result.ok else 'FAIL'} {plan.kind.key}: {result.detail}")
    if args.json:
        print(json.dumps(results, indent=2))
    if failed:
        return EXIT_FAILED
    return EXIT_UNVERIFIED if unverified else 0


def _unverified_policy(args: argparse.Namespace) -> Callable[[], bool]:
    """What to do when GitHub publishes no sha256 for the asset.

    Interactively: ask. Unattended (``--yes``): refuse, so a scheduled task
    never installs an executable it could not verify - the caller sees exit 4
    and can decide. ``--allow-unverified`` is the explicit opt-in.
    """
    if getattr(args, "allow_unverified", False):
        return lambda: True
    if args.yes:
        return lambda: False
    return lambda: _confirm("no sha256 published; install anyway?")


def _event_printer(event: UpdateEvent) -> None:
    """Print one update/rollback step, indented like the others."""
    print(f"  {event.message}")


def _exe_paths_for_kind(instances: Sequence[Instance], kind_key: str) -> list[Path]:
    """Every distinct executable path used by the instances of one kind."""
    paths: list[Path] = []
    for instance in instances:
        if instance.kind.key != kind_key or instance.exe_path is None:
            continue
        if all(_normcase(instance.exe_path) != _normcase(known) for known in paths):
            paths.append(instance.exe_path)
    return paths


def _cmd_rollback(args: argparse.Namespace) -> int:
    from . import archive
    from .discovery import discover
    from .kinds import get_kind
    from .updater import backup_path_for
    from .updater import rollback as rollback_exe

    instances = discover()
    results: list[JsonDict] = []
    failed = False
    matched = False
    for key in args.kind:
        kind = get_kind(key)
        exe_paths = _exe_paths_for_kind(instances, kind.key)
        if not exe_paths:
            print(t("cli.no_executable_for", name=kind.display_name), file=sys.stderr)
            continue
        matched = True
        for exe_path in exe_paths:
            report = None if args.json else _event_printer
            wanted: str | None = args.target_version
            # No --version means "the previous build": the .bak the updater may
            # still be holding, else the newest entry of the archive, else the
            # honest "nothing to roll back" of updater.rollback.
            use_backup = wanted is None and (backup_path_for(exe_path).exists() or not archive.list_archived(exe_path))
            if use_backup:
                # The freshest previous build is still the .bak the updater left.
                result = rollback_exe(
                    exe_path,
                    instances=instances,
                    restart=not args.no_restart,
                    on_event=report,
                )
            else:
                result = archive.restore(
                    exe_path,
                    wanted,
                    instances=instances,
                    restart=not args.no_restart,
                    on_event=report,
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
# umm archive (PLAN.md section 9.4)
# --------------------------------------------------------------------------- #

_ARCHIVE_COLUMNS = ("KIND", "VERSION", "ARCHIVED", "TAG", "FILE")


def format_archive_table(rows: Sequence[JsonDict]) -> str:
    """Render the kept builds: kind, version, date, release tag, file name."""
    if not rows:
        return "no archived build (.umm-archive is empty or absent)"
    table: list[tuple[str, ...]] = [_ARCHIVE_COLUMNS]
    for row in rows:
        stamp = str(row.get("archived_at") or "")
        table.append(
            (
                str(row.get("kind") or "?"),
                str(row.get("version") or "?"),
                stamp[:19].replace("T", " ") if stamp else "-",
                str(row.get("source_release_tag") or "-"),
                str(row.get("file") or "?"),
            )
        )
    widths = [max(len(row[column]) for row in table) for column in range(len(_ARCHIVE_COLUMNS))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in table
    )


def _archive_targets(kinds: Sequence[str] | None) -> list[tuple[str, Path]]:
    """``(kind key, executable)`` pairs the archive commands act on."""
    from .discovery import discover
    from .kinds import all_kinds

    instances = discover()
    wanted = [key.casefold() for key in kinds] if kinds else [kind.key for kind in all_kinds()]
    targets: list[tuple[str, Path]] = []
    for key in wanted:
        for exe_path in _exe_paths_for_kind(instances, key):
            targets.append((key, exe_path))
    return targets


def _cmd_archive(args: argparse.Namespace) -> int:
    from . import archive, manager_config

    targets = _archive_targets(args.kind)
    if not targets:
        message = "no usage-monitor executable found"
        print(json.dumps({"action": args.action, "entries": [], "detail": message}, indent=2) if args.json else message)
        return EXIT_NO_MATCH

    if args.action == "list":
        rows: list[JsonDict] = []
        for key, exe_path in targets:
            for item in archive.list_archived(exe_path):
                entry = item.to_dict()
                entry["kind"] = key
                entry["exe_path"] = str(exe_path)
                rows.append(entry)
        print(json.dumps(rows, indent=2) if args.json else format_archive_table(rows))
        return EXIT_OK

    keep = args.keep if args.keep is not None else manager_config.load().preferences.archive_keep
    removed: list[JsonDict] = []
    for key, exe_path in targets:
        for item in archive.prune(exe_path, keep=max(0, keep)):
            entry = item.to_dict()
            entry["kind"] = key
            removed.append(entry)
    if args.json:
        print(json.dumps({"action": "prune", "keep": keep, "removed": removed}, indent=2))
    else:
        print(t("cli.pruned", keep=keep, removed=len(removed)))
        for entry in removed:
            print(f"  {entry['kind']} {entry['version']} ({entry['file']})")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# umm skip / umm unskip (PLAN.md section 9.5)
# --------------------------------------------------------------------------- #


def _repo_and_tag(key: str, *, prerelease: bool) -> tuple[str, str | None, str]:
    """``(repo, latest tag, label)`` for one ``--kind`` value, ``manager`` included."""
    from .kinds import get_kind
    from .self_update import MANAGER_REPO, check_self

    if key == "manager":
        state = check_self(include_prerelease=prerelease)
        return MANAGER_REPO, state.tag, "Manager"
    kind = get_kind(key)
    plans = build_plans([key], prerelease=prerelease)
    tag = plans[0].target.tag if plans else None
    return kind.repo, tag, kind.display_name


def _cmd_skip(args: argparse.Namespace, *, skip: bool) -> int:
    from . import manager_config
    from .github import GitHubError

    rows: list[JsonDict] = []
    failed = False
    for key in args.kind:
        if not skip:
            repo = _repo_for(key)
            manager_config.unskip_version(repo)
            rows.append({"kind": key, "repo": repo, "skipped": None})
            if not args.json:
                print(t("cli.unskipped", kind=key))
            continue
        try:
            repo, tag, label = _repo_and_tag(key, prerelease=False)
        except GitHubError as error:
            print(t("cli.skip_error", error=error), file=sys.stderr)
            return EXIT_NETWORK
        if not tag:
            failed = True
            rows.append({"kind": key, "repo": repo, "skipped": None, "detail": "no published release"})
            if not args.json:
                print(t("cli.nothing_to_skip", kind=key), file=sys.stderr)
            continue
        manager_config.skip_version(repo, tag)
        rows.append({"kind": key, "repo": repo, "skipped": tag})
        if not args.json:
            print(t("cli.skip_done", label=label, tag=tag, kind=key))
    if args.json:
        print(json.dumps(rows, indent=2))
    return EXIT_FAILED if failed else EXIT_OK


def _repo_for(key: str) -> str:
    from .kinds import get_kind
    from .self_update import MANAGER_REPO

    return MANAGER_REPO if key == "manager" else get_kind(key).repo


# --------------------------------------------------------------------------- #
# umm token (PLAN.md section 6.5-bis)
# --------------------------------------------------------------------------- #


def _read_token_from_stdin() -> str:
    """Read one line from stdin without echoing it anywhere afterwards."""
    if sys.stdin is None:  # pragma: no cover - only without a stdin at all
        return ""
    if getattr(sys.stdin, "isatty", lambda: False)():
        print(T["cli.paste_token"])
    try:
        return sys.stdin.readline()
    except (OSError, ValueError):  # pragma: no cover - closed stdin
        return ""


def _cmd_token(args: argparse.Namespace) -> int:
    from . import github, secrets

    if args.action == "status":
        present = secrets.has_token()
        rate = github.rate_limit_state()
        payload: JsonDict = {
            "present": present,
            "source": secrets.token_source(),
            "target": secrets.DEFAULT_TARGET,
            "rate_limit": None if rate is None else rate.to_dict(),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                t(
                    "cli.token_state",
                    state=T["cli.token_present"] if present else T["cli.token_absent"],
                    source=payload["source"],
                )
            )
            print(t("cli.credential_target", target=secrets.DEFAULT_TARGET))
        return EXIT_OK

    if args.action == "clear":
        removed = secrets.delete_token()
        payload = {"present": False, "removed": removed, "target": secrets.DEFAULT_TARGET}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(T["cli.token_removed"] if removed else T["cli.token_never_stored"])
        return EXIT_OK

    raw = os.environ.get(args.from_env, "") if args.from_env else _read_token_from_stdin()
    if args.from_env and not raw.strip():
        print(t("cli.token_env_empty", name=args.from_env), file=sys.stderr)
        return EXIT_FAILED
    try:
        secrets.set_token(raw)
    except ValueError:
        print(T["cli.token_stdin_empty"], file=sys.stderr)
        return EXIT_FAILED
    except secrets.CredentialError as error:
        print(t("cli.token_set_error", error=error), file=sys.stderr)
        return EXIT_FAILED
    payload = {"present": True, "target": secrets.DEFAULT_TARGET, "source": "credential_manager"}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(t("cli.token_stored", target=secrets.DEFAULT_TARGET))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# umm self-update (PLAN.md section 6.5)
# --------------------------------------------------------------------------- #


def _cmd_self_update(args: argparse.Namespace) -> int:
    from . import manager_config, self_update

    config = manager_config.load()
    prerelease = bool(args.prerelease) or config.include_prerelease_for(self_update.MANAGER_REPO)
    state = self_update.check_self(
        include_prerelease=prerelease,
        skipped_tag=config.skipped_versions.get(self_update.MANAGER_REPO),
    )

    if args.check_only:
        print(json.dumps(state.to_dict(), indent=2) if args.json else state.detail)
        return EXIT_UPDATES_AVAILABLE if state.available else EXIT_OK

    if args.dry_run:
        steps = self_update.describe_self(state)
        if args.json:
            print(json.dumps({"action": "self-update", "dry_run": True, "steps": steps}, indent=2))
        else:
            for line in steps:
                print(f"  {line}")
        return EXIT_OK

    if not state.available:
        print(json.dumps(state.to_dict(), indent=2) if args.json else state.detail)
        return EXIT_OK

    if not self_update.is_frozen():
        message = (
            "self-update non disponibile da sorgente: aggiorna il checkout con git, oppure usa UsageMonitorManager.exe"
        )
        print(json.dumps({"ok": False, "detail": message}, indent=2) if args.json else message, file=sys.stderr)
        return EXIT_FAILED

    if not args.yes and not _confirm(f"update the manager to {state.target_version}?"):
        print(T["cli.skipped"])
        return EXIT_OK

    printer = None if args.json else _ProgressPrinter(tty=bool(getattr(sys.stdout, "isatty", lambda: False)()))
    result = self_update.apply_self(
        printer,
        check=state,
        confirm_unverified=(lambda: False) if args.yes else (lambda: _confirm("no sha256 published; install anyway?")),
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"{'ok  ' if result.ok else 'FAIL'} self-update: {result.detail}")
    return EXIT_OK if result.ok else EXIT_FAILED


def _cmd_post_update(pid: int) -> int:
    """``--post-update PID``: succeed the previous process, then be the tray."""
    from . import self_update
    from .ui import tray

    outcome = self_update.finish_post_update(pid)
    print(outcome.detail)
    return tray.run_tray(wait_for_mutex=self_update.DEFAULT_WAIT_TIMEOUT, messages=[outcome.message])


# --------------------------------------------------------------------------- #
# umm status (PLAN.md section 9.11, minimal form)
# --------------------------------------------------------------------------- #


def _status_payload(*, check: bool) -> JsonDict:
    """The health document; the builder itself lives in :mod:`.status_file`.

    It moved out of the CLI when the tray started writing the same document to
    ``status.json`` after every cycle (PLAN.md section 9.11): both callers need
    it, and only one of them is a command line.
    """
    from .status_file import build_status

    return build_status(check=check)


def _cmd_status(args: argparse.Namespace) -> int:
    from .status_file import write_status_file

    payload = _status_payload(check=bool(args.check))
    written: Path | None = None
    write_file = getattr(args, "write_file", None)
    if write_file is not None:
        try:
            written = write_status_file(payload, write_file or None)
        except OSError as error:
            print(t("cli.status_file_not_written", error=error))
            return EXIT_FAILED
    if args.json:
        print(json.dumps(payload, indent=2))
        if written is not None:
            print(t("cli.written_to", path=written), file=sys.stderr)
        return EXIT_OK
    instances = payload["instances"]
    print(
        t(
            "cli.status_header",
            version=payload["version"],
            running=payload["running"],
            total=len(instances),
        )
    )
    for row in instances:
        mark = T["cli.status_running"] if row["is_running"] else T["cli.status_stopped"]
        print(f"  {row['id']:<24} {row['local_version'] or '?':<10} {mark}")
    updates = payload["updates"]
    if updates["checked"]:
        suffix = t("cli.updates_error_suffix", error=updates["error"]) if updates["error"] else ""
        print(t("cli.updates_available", count=updates["available"], suffix=suffix))
    else:
        print(T["cli.updates_not_checked"])
    findings = payload["watchdog"]
    if findings["count"]:
        for event in findings["events"]:
            print(f"  ! {event['type']}: {event['detail']}")
    else:
        print(T["cli.watchdog_clean"])
    if written is not None:
        print(t("cli.status_file_is", path=written))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# umm kind (PLAN.md section 9.9)
# --------------------------------------------------------------------------- #


def format_kind_table(rows: Sequence[JsonDict]) -> str:
    """``umm kind list`` as a table: key, family, exe, repo, source."""
    headers = ("KEY", "FAMILY", "DISPLAY NAME", "EXE", "REPO", "SOURCE")
    body = [
        (
            str(row.get("key", "")),
            str(row.get("family", "")),
            str(row.get("display_name", "")),
            str(row.get("exe_name", "")),
            str(row.get("repo", "")),
            str(row.get("source", "")) + ("" if row.get("disabled_reason") is None else " (disabled)"),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(line[index]) for line in body)) if body else len(headers[index])
        for index in range(len(headers))
    ]
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()]
    for line in body:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)).rstrip())
    return "\n".join(lines)


def _cmd_kind(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from . import kinds as kind_registry
    from . import manager_config

    action = str(args.action)
    if action == "list":
        rows = [kind.to_dict() for kind in kind_registry.all_kinds()]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(format_kind_table(rows))
        return EXIT_OK

    key = (args.key or "").strip()
    if not key:
        parser.error(t("cli.kind_needs_key", action=action))

    if action == "remove":
        removed = kind_registry.remove_custom_kind(key)
        payload = {"key": key, "removed": removed}
        if args.json:
            print(json.dumps(payload, indent=2))
        elif removed:
            print(t("cli.kind_removed", key=key))
        else:
            print(t("cli.kind_not_custom", key=key))
        return EXIT_OK if removed else EXIT_NO_MATCH

    if not args.exe_name or not args.repo:
        parser.error(T["cli.kind_add_needs"])
    entry = manager_config.CustomKind(
        key=key.casefold(),
        display_name=(args.display_name or key).strip(),
        exe_name=str(args.exe_name).strip(),
        repo=str(args.repo).strip(),
        asset_pattern=(args.asset_pattern or "").strip(),
        sha_sidecar=bool(args.sha_sidecar),
        pe_product_name=(args.pe_product_name or None),
    )
    problems = kind_registry.add_custom_kind(entry)
    payload = {"key": entry.key, "added": not problems, "problems": problems, "kind": entry.to_dict()}
    if args.json:
        print(json.dumps(payload, indent=2))
    elif problems:
        for problem in problems:
            print(f"! {problem}")
    else:
        print(t("cli.kind_added", key=entry.key, exe=entry.exe_name, repo=entry.repo))
    return EXIT_FAILED if problems else EXIT_OK


# --------------------------------------------------------------------------- #
# umm diag (PLAN.md section 9.7)
# --------------------------------------------------------------------------- #


def _diag_instance(instance_id: str | None) -> Instance | None:
    """The instance ``--instance`` selects, or the recorded diagnostic one."""
    from . import diag
    from .discovery import discover

    instances = discover()
    if not instance_id:
        session = diag.read_state()
        instance_id = None if session is None else session.instance_id
    if not instance_id:
        return instances[0] if len(instances) == 1 else None
    matches = select_instances(instances, instance_id=instance_id)
    return matches[0] if len(matches) == 1 else None


def _cmd_diag(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from . import diag

    action = str(args.action)
    if action == "show":
        session = diag.read_state()
        lines = [] if session is None else diag.tail(session.log_path, max(1, int(args.lines)))
        payload = {
            "session": None if session is None else session.to_dict(),
            "lines": lines,
            "manager_log": str(diag.manager_log_path()),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return EXIT_OK
        if session is None:
            print(T["cli.diag_none"])
            print(t("cli.manager_log_is", path=diag.manager_log_path()))
            return EXIT_OK
        print(t("cli.diag_since", instance=session.instance_id, since=session.started_at or "?"))
        print(t("cli.log_is", path=session.log_path))
        for line in lines:
            print(line)
        return EXIT_OK

    instance = _diag_instance(args.instance)
    if instance is None:
        print(T["cli.diag_no_selection"])
        return EXIT_NO_MATCH

    if action == "start":
        if args.dry_run:
            for line in diag.describe_start(instance):
                print(line)
            return EXIT_OK
        result = diag.start_diagnostics(instance, on_event=None if args.json else _process_event_printer)
    else:
        if args.dry_run:
            print(process_control_describe_stop(instance))
            print(T["cli.diag_would_restart"])
            return EXIT_OK
        result = diag.stop_diagnostics(instance, on_event=None if args.json else _process_event_printer)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"{result.instance_id}: {result.detail}")
        if result.log_path is not None and action == "start":
            print(t("cli.log_is", path=result.log_path))
    return EXIT_OK if result.ok else EXIT_FAILED


def process_control_describe_stop(instance: Instance) -> str:
    """Thin wrapper so the diag dry-run reads like the update dry-run."""
    from .process_control import describe_stop

    return describe_stop(instance)


def _process_event_printer(event: Any) -> None:
    print(f"  {event.phase}: {event.message}")


# --------------------------------------------------------------------------- #
# umm profile (PLAN.md section 9.13)
# --------------------------------------------------------------------------- #


def _cmd_profile(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:  # noqa: PLR0911 - one branch per action
    from . import manager_config, profiles
    from .discovery import discover

    action = str(args.action)
    if action == "list":
        rows = [profile.to_dict() for profile in profiles.list_profiles()]
        if args.json:
            print(json.dumps(rows, indent=2))
        elif rows:
            for row in rows:
                print(
                    t(
                        "cli.profile_row",
                        name=row["name"],
                        members=", ".join(row["instance_ids"]) or T["cli.profile_empty"],
                    )
                )
        else:
            print(T["cli.no_profile_configured"])
        return EXIT_OK

    name = (args.name or "").strip()
    if not name:
        parser.error(t("cli.profile_needs_name", action=action))

    if action == "add":
        raw_ids: list[object] = list(args.instance_ids or [])
        ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        if not ids:
            parser.error(T["cli.profile_needs_member"])
        profile = manager_config.Profile(name=name, instance_ids=tuple(dict.fromkeys(ids)))
        manager_config.upsert_profile(profile)
        if args.json:
            print(json.dumps(profile.to_dict(), indent=2))
        else:
            print(t("cli.profile_row", name=name, members=", ".join(profile.instance_ids)))
        return EXIT_OK

    if action == "remove":
        removed = manager_config.remove_profile(name)
        if args.json:
            print(json.dumps({"name": name, "removed": removed}, indent=2))
        else:
            print(t("cli.profile_removed", name=name) if removed else t("cli.no_such_profile", name=name))
        return EXIT_OK if removed else EXIT_NO_MATCH

    profile = profiles.resolve(name)
    if profile is None:
        print(t("cli.no_such_profile_hint", name=name))
        return EXIT_NO_MATCH
    plan = profiles.plan(profile, discover(), stop_others=not bool(args.no_stop_others))

    if action == "show":
        if args.json:
            print(json.dumps(plan.to_dict(), indent=2))
        else:
            for line in profiles.describe(plan):
                print(line)
        return EXIT_OK

    if args.dry_run:
        lines = profiles.describe(plan)
        if args.json:
            print(json.dumps({**plan.to_dict(), "dry_run": True, "steps": lines}, indent=2))
        else:
            for line in lines:
                print(line)
        return EXIT_OK

    if plan.is_empty:
        print(t("cli.profile_nothing_to_do", name=name))
        return EXIT_OK
    if not args.yes:
        for line in profiles.describe(plan):
            print(line)
        if not _confirm(f"apply profile {name}?"):
            print(T["cli.cancelled"])
            return EXIT_OK
    result = profiles.apply_plan(plan, on_event=None if args.json else _process_event_printer)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for item in (*result.stops, *result.starts):
            print(f"{item.instance_id}: {item.detail}")
    return EXIT_OK if result.ok else EXIT_FAILED


# --------------------------------------------------------------------------- #
# Phase 4: tray, legacy import, autostart
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# umm config
# --------------------------------------------------------------------------- #

CONFIG_ACTIONS = (
    "show",
    "validate",
    "set",
    "unset",
    "create",
    "export",
    "import",
    "history",
    "diff",
    "restore",
)


def _config_instance(instance_id: str | None) -> Instance | None:
    """The single instance ``--instance`` selects, or ``None``."""
    from .discovery import discover

    instances = discover()
    if not instance_id:
        return instances[0] if len(instances) == 1 else None
    matches = select_instances(instances, instance_id=instance_id)
    return matches[0] if len(matches) == 1 else None


def parse_setting_value(raw: str) -> object:
    """JSON first, plain string as a fallback.

    ``umm config set poll_interval 300`` stores a number, ``umm config set
    bar_fg "#00cc66"`` stores the string (``#00cc66`` is not valid JSON), and
    ``umm config set tooltip_fields '["five_hour"]'`` stores the list.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _print_validation(report: ValidationReport, path: Path) -> None:
    errors = report.errors
    unknown = report.unknown_keys
    if errors:
        print(t("cli.invalid_values", path=path, count=len(errors)))
        for line in report.formatted():
            print(line)
    else:
        print(t("cli.valid", path=path))
    if unknown:
        print(t("cli.unknown_keys_kept", keys=", ".join(unknown)))


def _cmd_config(args: argparse.Namespace) -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915 - one branch per subaction
    from . import history as history_module
    from .settings import store

    instance = _config_instance(args.instance)
    if instance is None:
        print(t("cli.config_no_instance", selector=repr(args.instance)))
        return EXIT_NO_MATCH

    kind = instance.kind.key
    path = instance.settings_path or store.suggested_path(instance)
    loaded = store.load(path)
    rest = list(args.args)

    if args.action == "show":
        if args.json:
            print(json.dumps(loaded.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(
                t(
                    "cli.config_file_header",
                    path=path,
                    suffix="" if loaded.exists else T["cli.does_not_exist_suffix"],
                )
            )
            if loaded.error:
                print(t("cli.config_read_error", error=loaded.error))
            print(store.dumps(loaded.data).rstrip())
        return EXIT_OK

    if args.action == "validate":
        report = store.validate(kind, loaded.data)
        if args.json:
            payload = report.to_dict()
            payload["path"] = str(path)
            payload["exists"] = loaded.exists
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _print_validation(report, path)
        return EXIT_OK if report.ok and loaded.error is None else EXIT_FAILED

    if args.action == "create":
        if path.is_file():
            print(t("cli.config_exists", path=path))
            return EXIT_FAILED
        target = store.suggested_path(instance)
        store.create_default(target)
        print(t("cli.created", path=target))
        return EXIT_OK

    if args.action in ("set", "unset"):
        if not rest:
            print(t("cli.config_missing_key", action=args.action))
            return EXIT_FAILED
        key = rest[0]
        data = dict(loaded.data)
        if args.action == "set":
            if len(rest) < 2:
                print(T["cli.config_missing_value"])
                return EXIT_FAILED
            data[key] = parse_setting_value(rest[1])
        else:
            if key not in data:
                print(t("cli.config_not_set", key=key))
                return EXIT_OK
            del data[key]
        report = store.validate(kind, data)
        if report.errors:
            _print_validation(report, path)
            print(T["cli.nothing_written"])
            return EXIT_FAILED
        store.save(path, report.normalized, instance=instance)
        print(t("cli.key_set" if args.action == "set" else "cli.key_unset", key=key, path=path))
        return EXIT_OK

    if args.action == "export":
        if not rest:
            print(T["cli.config_missing_dest"])
            return EXIT_FAILED
        written = history_module.export(instance, Path(rest[0]), data=loaded.data)
        print(t("cli.exported_to", path=written))
        return EXIT_OK

    if args.action == "import":
        if not rest:
            print(T["cli.config_missing_source"])
            return EXIT_FAILED
        result = history_module.import_(instance, Path(rest[0]))
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(result.detail)
            for key, message in result.errors:
                print(f"  {key}: {message}")
        return EXIT_OK if result.ok else EXIT_FAILED

    versions = history_module.list_versions(instance)

    if args.action == "history":
        if args.json:
            print(json.dumps([version.to_dict() for version in versions], indent=2, ensure_ascii=False))
            return EXIT_OK
        if not versions:
            print(T["cli.no_archived_version"])
            return EXIT_OK
        for number, version in enumerate(versions, start=1):
            stamp = version.saved_at.isoformat() if version.saved_at else version.stamp
            print(t("cli.archived_row", number=number, stamp=stamp, size=version.size))
        return EXIT_OK

    if args.action in ("diff", "restore"):
        number = 1
        if rest:
            try:
                number = int(rest[0])
            except ValueError:
                print(t("cli.not_a_version_number", action=args.action, value=rest[0]))
                return EXIT_FAILED
        if not 1 <= number <= len(versions):
            print(t("cli.no_such_version", action=args.action, number=number, count=len(versions)))
            return EXIT_FAILED
        version = versions[number - 1]
        data = history_module.read_version(instance, version.stamp) or {}
        if args.action == "diff":
            lines = history_module.diff(data, loaded.data)
            if args.json:
                print(json.dumps([line.to_dict() for line in lines], indent=2, ensure_ascii=False))
            elif not lines:
                print(t("cli.version_identical", number=number))
            else:
                print(t("cli.version_vs_current", number=number, stamp=version.stamp))
                for line in lines:
                    before = json.dumps(line.before, ensure_ascii=False) if line.change != "added" else "-"
                    after = json.dumps(line.after, ensure_ascii=False) if line.change != "removed" else "-"
                    print(f"  {line.change:<7} {line.key}: {before} -> {after}")
            return EXIT_OK
        history_module.restore(instance, version.stamp)
        print(t("cli.version_restored", number=number, stamp=version.stamp, path=path))
        return EXIT_OK

    print(t("cli.not_implemented", command=f"config {args.action}"))
    return EXIT_NOT_IMPLEMENTED


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
            print(T["cli.import_dry_run"])
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
            print(t("cli.autostart_error", error=error), file=sys.stderr)
            return EXIT_FAILED
    if as_json:
        print(json.dumps(_autostart_payload("manager", command), indent=2))
    else:
        state = t("cli.autostart_yes", command=command) if command else T["cli.autostart_no"]
        print(t("cli.autostart_manager", state=state))
    return 0


def _cmd_autostart_instance(instance_id: str, action: str, as_json: bool) -> int:
    from . import process_control
    from .discovery import discover

    wanted = _normalize_instance_id(instance_id)
    matches = [i for i in discover() if _normalize_instance_id(i.instance_id) == wanted]
    if not matches:
        print(t("cli.autostart_no_instance", id=repr(instance_id)), file=sys.stderr)
        return EXIT_NO_MATCH
    instance = matches[0]
    try:
        if action == "status":
            command = process_control.get_instance_autostart(instance)
        else:
            command = process_control.set_instance_autostart(instance, action == "on")
    except (OSError, ValueError) as error:
        print(t("cli.autostart_error", error=error), file=sys.stderr)
        return EXIT_FAILED
    if as_json:
        print(json.dumps(_autostart_payload(instance.instance_id, command), indent=2))
    else:
        state = t("cli.autostart_yes", command=command) if command else T["cli.autostart_no"]
        print(t("cli.autostart_instance", instance=instance.instance_id, state=state))
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
    if not args or args[0] in ("tray", "--tray", "--post-update"):
        return
    from .platform import attach_parent_console

    attach_parent_console()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``umm`` console script.

    The only thing this adds to :func:`_dispatch` is the error policy of
    PLAN.md phase 8: an :class:`errors.UmmError` becomes its own exit code plus
    ``message`` and ``hint`` on stderr, an unexpected exception becomes one
    generic line with the traceback in ``manager.log``, and ``--debug`` lets
    either through as a real traceback.
    """
    debug = any(token == "--debug" for token in (sys.argv[1:] if argv is None else argv))
    try:
        return _dispatch(argv)
    except errors.UmmError as error:
        if debug:
            raise
        for line in error.lines:
            print(line, file=sys.stderr)
        return error.code
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return EXIT_FAILED
    except Exception as error:  # noqa: BLE001 - the last line of defence
        if debug:
            raise
        print(errors.report_unexpected(error, context="cli"), file=sys.stderr)
        print(T["error.debug_hint"], file=sys.stderr)
        return errors.exit_code_for(error)


def _dispatch(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments and run the command they name."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    _attach_console_if_frozen(raw_args)
    reconfigure_stdout()
    from . import diag, i18n, manager_config
    from . import kinds as kind_registry

    # The language has to be active before the parser is built: every help
    # string and every message below comes from the catalog.
    i18n.set_language(manager_config.load().preferences.language)
    # The user-defined kinds must be in the registry before the parser is
    # built: they are valid --kind values (PLAN.md section 9.9).
    for problem in kind_registry.reload():
        print(t("cli.config_problem", problem=problem), file=sys.stderr)
    diag.setup_manager_logging()
    parser = build_parser()
    raw = raw_args
    # PLAN.md section 6.5 writes the autostart command as `"<exe>" --tray`.
    args = parser.parse_args(["tray" if token == "--tray" else token for token in raw])
    if args.post_update is not None:
        return _cmd_post_update(int(args.post_update))
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
    if args.command == "archive":
        return _cmd_archive(args)
    if args.command in ("skip", "unskip"):
        return _cmd_skip(args, skip=args.command == "skip")
    if args.command == "token":
        return _cmd_token(args)
    if args.command == "self-update":
        return _cmd_self_update(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "kind":
        return _cmd_kind(args, parser)
    if args.command == "diag":
        return _cmd_diag(args, parser)
    if args.command == "profile":
        return _cmd_profile(args, parser)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "import-legacy":
        return _cmd_import_legacy(args)
    if args.command == "autostart":
        return _cmd_autostart(args)
    print(t("cli.not_implemented", command=args.command))
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover - module executed as a script
    raise SystemExit(main())
