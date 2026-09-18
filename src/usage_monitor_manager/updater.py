"""Update execution: plan per executable, then download, verify, swap, restart.

Implements the ``updater.py`` part of PLAN.md section 6.3, as the sequence the
PowerShell updater performed, with the same safety properties:

1. download into ``<exe>.download``, **next to the target**, so the final step
   is a rename on one volume and never a copy across devices;
2. verify the sha256 (GitHub ``assets[].digest``, else the ``.sha256``
   sidecar). No digest at all means *unverifiable*: the caller has to say yes
   explicitly through ``confirm_unverified``, otherwise the download is deleted
   and nothing is touched;
3. read the Authenticode state - information only, it never blocks;
4. stop the running instances that use that executable, remembering which ones
   were running;
5. ``exe -> exe.bak`` then ``download -> exe``, each with retries, because
   OneDrive and antivirus scanners hold the file for a few seconds
   (PLAN.md section 8); a failure restores the ``.bak``;
6. restart exactly the instances that were running, two seconds apart;
7. post-check after five seconds: each restarted instance is alive and the PE
   version now equals the target. The ``.bak`` is deleted only then; otherwise
   it is kept and the result says ``rollback_available``.

Two rules bind this module. **An update is per executable, a restart is per
instance**: the two Claude instances share one file, so one plan stops and
restarts both. And **every process operation goes through
``process_control``** - this module never spawns or kills anything itself.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import github, process_control, versions, watchdog
from .integrity import IntegrityResult, authenticode_status, normalize_digest, parse_sidecar, sha256_file
from .model import Instance, JsonDict, MonitorKind, ReleaseInfo, UpdatePlan
from .versions import VersionStatus

_log = logging.getLogger("umm.updater")

__all__ = [
    "DEFAULT_POST_CHECK_DELAY",
    "DEFAULT_SWAP_ATTEMPTS",
    "DEFAULT_SWAP_DELAY",
    "EventCallback",
    "FileSwapper",
    "SwapError",
    "UpdateEvent",
    "UpdateResult",
    "UpdateStep",
    "apply",
    "backup_path_for",
    "describe",
    "download_path_for",
    "plan_status",
    "plan_updates",
    "resolve_expected_digest",
    "rollback",
]

DEFAULT_SWAP_ATTEMPTS = 5
DEFAULT_SWAP_DELAY = 2.0
"""Five attempts two seconds apart: the window in which OneDrive or an
antivirus scanner typically lets go of a freshly written executable."""

DEFAULT_POST_CHECK_DELAY = 5.0
"""How long to let a restarted monitor settle before declaring the update good."""


UpdateStep = Literal[
    "plan",
    "download",
    "verify",
    "signature",
    "stop",
    "backup",
    "swap",
    "restore",
    "start",
    "post_check",
    "cleanup",
    "rollback",
    "abort",
    "done",
]


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    """One step of an update, for the CLI log and the future progress bar."""

    step: UpdateStep
    message: str
    progress: float | None = None
    """0.0-1.0 during the download, ``None`` for steps without a measure."""

    def to_dict(self) -> JsonDict:
        return {"step": self.step, "message": self.message, "progress": self.progress}


EventCallback = Callable[[UpdateEvent], None]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Outcome of applying (or rolling back) one plan."""

    exe_path: Path
    kind: str
    ok: bool
    detail: str
    from_version: str | None = None
    to_version: str | None = None
    tag: str | None = None
    integrity: IntegrityResult | None = None
    stopped: tuple[str, ...] = ()
    restarted: tuple[str, ...] = ()
    rollback_available: bool = False
    backup_path: Path | None = None
    events: tuple[UpdateEvent, ...] = field(default=())

    def to_dict(self) -> JsonDict:
        return {
            "exe_path": str(self.exe_path),
            "kind": self.kind,
            "ok": self.ok,
            "detail": self.detail,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "tag": self.tag,
            "integrity": None if self.integrity is None else self.integrity.to_dict(),
            "stopped": list(self.stopped),
            "restarted": list(self.restarted),
            "rollback_available": self.rollback_available,
            "backup_path": None if self.backup_path is None else str(self.backup_path),
            "events": [event.to_dict() for event in self.events],
        }


class SwapError(RuntimeError):
    """A rename kept failing: the message names both paths and the attempts."""


# --------------------------------------------------------------------------- #
# Seams (tests replace these instead of waiting in real time)
# --------------------------------------------------------------------------- #


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _os_replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


# --------------------------------------------------------------------------- #
# File operations
# --------------------------------------------------------------------------- #


def download_path_for(exe_path: Path) -> Path:
    """``<exe>.download``: same directory, so the swap is a rename."""
    return exe_path.with_name(f"{exe_path.name}.download")


def backup_path_for(exe_path: Path) -> Path:
    """``<exe>.bak``: kept until the post-start check succeeds."""
    return exe_path.with_name(f"{exe_path.name}.bak")


class FileSwapper:
    """Every file move of an update, with the retries the real world needs.

    Isolated in its own class for one reason: this is the part that can leave a
    machine without a working executable, so it has to be testable on its own,
    against a genuinely locked file and against a rename that always fails.
    ``replace`` and ``sleep`` are injectable for exactly that.
    """

    def __init__(
        self,
        *,
        attempts: int = DEFAULT_SWAP_ATTEMPTS,
        delay: float = DEFAULT_SWAP_DELAY,
        replace: Callable[[Path, Path], None] = _os_replace,
        sleep: Callable[[float], None] | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> None:
        self.attempts = max(1, attempts)
        self.delay = max(0.0, delay)
        self._replace = replace
        self._sleep = sleep if sleep is not None else _sleep
        self._on_retry = on_retry

    def move(self, src: Path, dst: Path) -> None:
        """Rename ``src`` onto ``dst``, retrying while the file is held.

        Raises:
            SwapError: the rename still failed after the last attempt.
        """
        last: OSError | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                self._replace(src, dst)
                return
            except OSError as error:
                last = error
                if attempt == self.attempts:
                    break
                if self._on_retry is not None:
                    self._on_retry(
                        f"{src.name} -> {dst.name} failed ({error.strerror or error}); "
                        f"retry {attempt + 1}/{self.attempts} in {self.delay:g}s"
                    )
                self._sleep(self.delay)
        reason = last.strerror if last is not None and last.strerror else last
        raise SwapError(f"cannot rename {src} to {dst} after {self.attempts} attempts: {reason}") from last

    def backup(self, exe_path: Path) -> Path:
        """Move the current executable aside; a stale ``.bak`` is removed first."""
        backup = backup_path_for(exe_path)
        if backup.exists():
            self.discard(backup)
        self.move(exe_path, backup)
        return backup

    def promote(self, download: Path, exe_path: Path) -> None:
        """Put the freshly downloaded file in place of the executable."""
        self.move(download, exe_path)

    def restore(self, backup: Path, exe_path: Path) -> None:
        """Put the backup back where the executable belongs."""
        self.move(backup, exe_path)

    def discard(self, path: Path) -> bool:
        """Best-effort delete, with the same retries; never raises."""
        for attempt in range(1, self.attempts + 1):
            try:
                path.unlink(missing_ok=True)
                return True
            except OSError as error:
                if attempt == self.attempts:
                    if self._on_retry is not None:
                        self._on_retry(f"could not delete {path}: {error.strerror or error}")
                    return False
                self._sleep(self.delay)
        return False  # pragma: no cover - unreachable, the loop always returns


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path))


def plan_status(plan: UpdatePlan) -> VersionStatus:
    """Classify a plan: ``update_available``, ``up_to_date``, ..."""
    return versions.status_for(plan.current_version, plan.target.version)


def plan_updates(
    instances: Sequence[Instance],
    *,
    kinds: Sequence[str] | None = None,
    include_prerelease: bool = False,
    prerelease_for: Callable[[str], bool] | None = None,
    token: str | None = None,
    cache_dir: Path | None = None,
) -> list[UpdatePlan]:
    """One :class:`UpdatePlan` per distinct executable path.

    Instances sharing an executable (the two Claude ones) collapse into a
    single plan listing both in ``affected_instances``: the update happens
    once, the restart happens twice. The local version is read from the PE of
    the file on disk - not from whatever discovery cached - and the target from
    the kind's release repo, with one API call per repo even when several
    executables belong to it.

    ``prerelease_for(repo)`` overrides ``include_prerelease`` per repository,
    which is the per-repo prerelease channel of PLAN.md section 9.5.

    Raises:
        github.GitHubError: propagated unchanged, so the CLI can map a network
            failure to its own exit code instead of guessing.
    """
    wanted = {key.casefold() for key in kinds} if kinds else None
    groups: dict[str, list[Instance]] = {}
    order: list[str] = []
    for instance in instances:
        if instance.exe_path is None:
            continue
        if wanted is not None and instance.kind.key.casefold() not in wanted:
            continue
        key = _normcase(instance.exe_path)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(instance)

    releases: dict[str, ReleaseInfo] = {}
    plans: list[UpdatePlan] = []
    for key in order:
        members = groups[key]
        kind: MonitorKind = members[0].kind
        exe_path = members[0].exe_path
        assert exe_path is not None  # guarded when grouping
        if kind.repo not in releases:
            releases[kind.repo] = github.fetch_latest_release(
                kind.repo,
                include_prerelease=include_prerelease if prerelease_for is None else prerelease_for(kind.repo),
                token=token,
                cache_dir=cache_dir,
            )
        plans.append(
            UpdatePlan(
                exe_path=exe_path,
                kind=kind,
                current_version=versions.exe_version(exe_path),
                target=releases[kind.repo],
                affected_instances=tuple(members),
            )
        )
    return plans


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #


class _Log:
    """Collects the events while forwarding them to the caller's callback."""

    def __init__(self, on_event: EventCallback | None) -> None:
        self._on_event = on_event
        self.events: list[UpdateEvent] = []

    def __call__(self, step: UpdateStep, message: str, progress: float | None = None) -> None:
        event = UpdateEvent(step=step, message=message, progress=progress)
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)


def _running_instances(plan: UpdatePlan) -> list[Instance]:
    return [instance for instance in plan.affected_instances if instance.is_running]


def resolve_expected_digest(
    target: ReleaseInfo,
    *,
    token: str | None = None,
    on_note: Callable[[str], None] | None = None,
) -> tuple[str | None, Literal["digest", "sidecar", "none"]]:
    """The sha256 we expect for a release asset, and where it came from.

    The GitHub API ``digest`` field wins; failing that the ``.exe.sha256``
    sidecar is fetched. ``("none", None)`` means *unverifiable*, which every
    caller must turn into an explicit question rather than a silent install.
    Shared by :func:`apply` and by ``self_update.apply_self``.
    """

    def note(message: str) -> None:
        if on_note is not None:
            on_note(message)

    asset = target.asset
    if asset is not None:
        digest = normalize_digest(asset.digest)
        if digest:
            return digest, "digest"
    sidecar_url = target.sidecar_sha_url
    if sidecar_url:
        try:
            text = github.fetch_text(sidecar_url, token=token)
        except github.GitHubError as error:
            note(f"could not fetch the .sha256 sidecar: {error}")
            return None, "none"
        digest = parse_sidecar(text)
        if digest:
            return digest, "sidecar"
        note(f"the .sha256 sidecar at {sidecar_url} holds no usable digest")
    return None, "none"


def _expected_digest(
    plan: UpdatePlan,
    log: _Log,
    *,
    token: str | None,
) -> tuple[str | None, Literal["digest", "sidecar", "none"]]:
    """The sha256 we expect, and where it came from."""
    return resolve_expected_digest(plan.target, token=token, on_note=lambda message: log("verify", message))


def _restart_instances(
    targets: Sequence[Instance],
    log: _Log,
    *,
    gap_seconds: float,
) -> list[str]:
    """Start instances through ``process_control``, two seconds apart."""
    restarted: list[str] = []
    for index, instance in enumerate(targets):
        if index:
            _sleep(gap_seconds)
        result = process_control.start(instance)
        log("start", f"{instance.instance_id}: {result.detail}")
        if result.ok:
            restarted.append(instance.instance_id)
    return restarted


def _post_check(
    plan: UpdatePlan,
    restarted: Sequence[str],
    log: _Log,
    *,
    delay: float,
) -> tuple[bool, str]:
    """After ``delay`` seconds: every restarted instance alive, version on target."""
    from . import discovery

    _sleep(delay)
    installed = versions.exe_version(plan.exe_path)
    if versions.compare(installed, plan.target.version) != 0:
        shown = versions.display_version(installed) or "no version"
        return False, f"the executable reports {shown} after the swap, expected {plan.target.version}"
    log("post_check", f"{plan.exe_path.name} now reports {versions.display_version(installed)}")
    if not restarted:
        return True, "version verified; no instance needed restarting"
    alive = {
        instance.instance_id
        for instance in discovery.discover()
        if instance.is_running and instance.instance_id in set(restarted)
    }
    missing = [instance_id for instance_id in restarted if instance_id not in alive]
    if missing:
        return False, f"restarted but not running after {delay:g}s: {', '.join(missing)}"
    log("post_check", f"running again: {', '.join(restarted)}")
    return True, "version verified and every restarted instance is running"


def apply(  # noqa: PLR0911, PLR0912, PLR0915 - the sequence is the unit here
    plan: UpdatePlan,
    *,
    on_event: EventCallback | None = None,
    confirm_unverified: Callable[[], bool] = lambda: False,
    restart: bool = True,
    swapper: FileSwapper | None = None,
    token: str | None = None,
    post_check_delay: float = DEFAULT_POST_CHECK_DELAY,
    gap_seconds: float = process_control.DEFAULT_GAP_SECONDS,
    archive_keep: int | None = None,
) -> UpdateResult:
    """Run one plan end to end, keeping a restorable ``.bak`` until it is proven.

    ``confirm_unverified`` is only consulted when GitHub publishes neither a
    digest nor a sidecar; its default of ``False`` means "abort", so a caller
    that forgets about it can never install an unverified executable.

    On success the displaced executable does not simply disappear: it is moved
    into ``<exe_dir>/.umm-archive`` (PLAN.md section 9.4) so that
    ``umm rollback --version`` still works weeks later. ``archive_keep``
    overrides ``preferences.archive_keep``; zero disables the archive.
    """
    log = _Log(on_event)
    exe_path = plan.exe_path
    _log.info("update %s -> %s at %s", plan.kind.key, plan.target.version, exe_path)
    swap = swapper if swapper is not None else FileSwapper(on_retry=lambda message: log("swap", message))
    download = download_path_for(exe_path)
    was_running = _running_instances(plan)

    def result(ok: bool, detail: str, **extra: object) -> UpdateResult:
        log("done" if ok else "abort", detail)
        return UpdateResult(
            exe_path=exe_path,
            kind=plan.kind.key,
            ok=ok,
            detail=detail,
            from_version=versions.display_version(plan.current_version),
            to_version=plan.target.version,
            tag=plan.target.tag,
            events=tuple(log.events),
            **extra,  # type: ignore[arg-type]
        )

    asset = plan.target.asset
    if asset is None:
        return result(False, f"release {plan.target.tag} of {plan.kind.repo} publishes no .exe asset")

    log(
        "plan",
        f"{plan.kind.display_name}: {versions.display_version(plan.current_version) or '?'} -> {plan.target.version} "
        f"({exe_path}), {len(plan.affected_instances)} instance(s), {len(was_running)} running",
    )

    # 1. download ---------------------------------------------------------- #
    def progress(done: int, total: int) -> None:
        fraction = (done / total) if total else None
        log("download", f"{asset.name}: {done} of {total or '?'} bytes", fraction)

    log("download", f"downloading {asset.name} to {download.name}")
    try:
        github.download(asset, download, progress, token=token)
    except github.GitHubError as error:
        swap.discard(download)
        return result(False, f"download failed: {error}")

    # 2. verify ------------------------------------------------------------ #
    expected, source = _expected_digest(plan, log, token=token)
    signature = authenticode_status(download)
    if expected is None:
        integrity = IntegrityResult(hash_verified=False, source="none", signature=signature)
        log("verify", integrity.summary())
        if not confirm_unverified():
            swap.discard(download)
            return result(
                False,
                f"aborted: {plan.target.tag} publishes no sha256 and the unverified download was not confirmed",
                integrity=integrity,
            )
        log("verify", "the caller accepted an unverifiable download")
    else:
        actual = sha256_file(download)
        integrity = IntegrityResult(
            hash_verified=actual == expected,
            source=source,
            signature=signature,
            expected=expected,
            actual=actual,
        )
        log("verify", integrity.summary())
        if not integrity.hash_verified:
            swap.discard(download)
            return result(
                False,
                f"sha256 mismatch: expected {expected}, got {actual}; download deleted, nothing was touched",
                integrity=integrity,
            )

    # 3. signature (information only) -------------------------------------- #
    log("signature", f"Authenticode: {signature} (informational, unsigned builds are expected)")

    # 4. stop the running instances ---------------------------------------- #
    stopped: list[str] = []
    for instance in was_running:
        watchdog.expect_down(instance.instance_id)  # an update is not a crash (PLAN.md section 9.2)
        stop_result = process_control.stop(instance)
        log("stop", f"{instance.instance_id}: {stop_result.detail}")
        if not stop_result.ok:
            swap.discard(download)
            already = set(stopped)
            recovered = _restart_instances(
                [i for i in was_running if i.instance_id in already], log, gap_seconds=gap_seconds
            )
            return result(
                False,
                f"aborted: could not stop {instance.instance_id} ({stop_result.detail}); "
                "the executable was not touched",
                integrity=integrity,
                stopped=tuple(stopped),
                restarted=tuple(recovered),
            )
        stopped.append(instance.instance_id)
    if not was_running:
        log("stop", "no running instance uses this executable")

    # 5. swap --------------------------------------------------------------- #
    try:
        backup = swap.backup(exe_path)
        log("backup", f"{exe_path.name} moved aside to {backup.name}")
    except SwapError as error:
        swap.discard(download)
        recovered = _restart_instances(was_running, log, gap_seconds=gap_seconds) if restart else []
        return result(
            False,
            f"aborted: {error}",
            integrity=integrity,
            stopped=tuple(stopped),
            restarted=tuple(recovered),
        )

    try:
        swap.promote(download, exe_path)
        log("swap", f"{download.name} is now {exe_path.name}")
    except SwapError as error:
        log("restore", f"putting {backup.name} back: {error}")
        try:
            swap.restore(backup, exe_path)
            restored = True
        except SwapError as restore_error:
            log("restore", f"the restore failed too: {restore_error}")
            restored = False
        recovered = _restart_instances(was_running, log, gap_seconds=gap_seconds) if restart else []
        detail = (
            f"swap failed and the previous executable was restored: {error}"
            if restored
            else f"swap failed AND the restore failed: {error}; {backup} still holds the old executable"
        )
        return result(
            False,
            detail,
            integrity=integrity,
            stopped=tuple(stopped),
            restarted=tuple(recovered),
            rollback_available=not restored,
            backup_path=backup if not restored else None,
        )

    # 6. restart ------------------------------------------------------------ #
    if not restart:
        log("start", "--no-restart: the instances stay stopped")
        restarted: list[str] = []
    else:
        restarted = _restart_instances(was_running, log, gap_seconds=gap_seconds)

    # 7. post-check and cleanup -------------------------------------------- #
    ok, detail = _post_check(plan, restarted, log, delay=post_check_delay)
    if ok:
        _archive_previous(plan, backup, log, keep=archive_keep)
        if swap.discard(backup):
            log("cleanup", f"{backup.name} deleted")
        else:
            log("cleanup", f"{backup.name} could not be deleted; it is harmless and can be removed by hand")
        return result(
            True,
            f"updated to {plan.target.version}: {detail}",
            integrity=integrity,
            stopped=tuple(stopped),
            restarted=tuple(restarted),
        )
    log("cleanup", f"{backup.name} kept: run 'umm rollback --kind {plan.kind.key}' to go back")
    return result(
        False,
        f"the post-update check failed: {detail}",
        integrity=integrity,
        stopped=tuple(stopped),
        restarted=tuple(restarted),
        rollback_available=True,
        backup_path=backup,
    )


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def rollback(
    plan_or_exe_path: UpdatePlan | Path | str,
    *,
    on_event: EventCallback | None = None,
    instances: Sequence[Instance] | None = None,
    swapper: FileSwapper | None = None,
    restart: bool = True,
    gap_seconds: float = process_control.DEFAULT_GAP_SECONDS,
) -> UpdateResult:
    """Put ``<exe>.bak`` back: stop, restore, restart what was running.

    Accepts the plan that produced the backup or just the executable path (the
    CLI's ``umm rollback --kind K``); in the latter case the affected instances
    come from ``instances`` or from a fresh discovery.
    """
    log = _Log(on_event)
    if isinstance(plan_or_exe_path, UpdatePlan):
        exe_path = plan_or_exe_path.exe_path
        kind_key = plan_or_exe_path.kind.key
        affected: Sequence[Instance] = plan_or_exe_path.affected_instances
    else:
        exe_path = Path(plan_or_exe_path)
        affected = _instances_for_exe(exe_path, instances)
        kind_key = affected[0].kind.key if affected else ""

    swap = swapper if swapper is not None else FileSwapper(on_retry=lambda message: log("rollback", message))
    backup = backup_path_for(exe_path)

    def result(ok: bool, detail: str, **extra: object) -> UpdateResult:
        log("done" if ok else "abort", detail)
        return UpdateResult(
            exe_path=exe_path,
            kind=kind_key,
            ok=ok,
            detail=detail,
            from_version=versions.display_version(versions.exe_version(exe_path)),
            events=tuple(log.events),
            **extra,  # type: ignore[arg-type]
        )

    if not backup.exists():
        return result(False, f"nothing to roll back: {backup} does not exist")

    was_running = [instance for instance in affected if instance.is_running]
    stopped: list[str] = []
    for instance in was_running:
        watchdog.expect_down(instance.instance_id)  # a rollback is not a crash either
        stop_result = process_control.stop(instance)
        log("stop", f"{instance.instance_id}: {stop_result.detail}")
        if not stop_result.ok:
            return result(
                False,
                f"aborted: could not stop {instance.instance_id} ({stop_result.detail}); "
                "the executable was not touched",
                stopped=tuple(stopped),
                rollback_available=True,
                backup_path=backup,
            )
        stopped.append(instance.instance_id)

    current = download_path_for(exe_path).with_name(f"{exe_path.name}.rolled-back")
    if exe_path.exists():
        try:
            swap.move(exe_path, current)
            log("rollback", f"{exe_path.name} set aside as {current.name}")
        except SwapError as error:
            return result(
                False,
                f"aborted: {error}",
                stopped=tuple(stopped),
                rollback_available=True,
                backup_path=backup,
            )
    try:
        swap.restore(backup, exe_path)
        log("rollback", f"{backup.name} restored as {exe_path.name}")
    except SwapError as error:
        return result(
            False,
            f"the restore failed: {error}; {backup} still holds the previous executable",
            stopped=tuple(stopped),
            rollback_available=True,
            backup_path=backup,
        )
    swap.discard(current)

    restarted = _restart_instances(was_running, log, gap_seconds=gap_seconds) if restart else []
    restored_version = versions.display_version(versions.exe_version(exe_path))
    return result(
        True,
        f"rolled back to {restored_version or 'the previous executable'}",
        stopped=tuple(stopped),
        restarted=tuple(restarted),
    )


def _archive_previous(plan: UpdatePlan, backup: Path, log: _Log, *, keep: int | None) -> None:
    """Move the replaced executable into ``.umm-archive`` (PLAN.md section 9.4).

    Runs between the successful post-check and the deletion of the ``.bak``,
    and never fails the update: an archive that cannot be written is a lost
    convenience, not a broken installation.
    """
    from . import archive, manager_config

    limit = keep if keep is not None else manager_config.load().preferences.archive_keep
    if limit <= 0:
        archive.record_current(plan.exe_path, version=plan.target.version, tag=plan.target.tag)
        return
    kept = archive.archive_exe(
        plan.exe_path,
        versions.display_version(plan.current_version),
        source_path=backup,
        keep=limit,
    )
    archive.record_current(plan.exe_path, version=plan.target.version, tag=plan.target.tag)
    if kept is None:
        log("cleanup", f"the previous executable could not be archived in {archive.ARCHIVE_DIR_NAME}")
        return
    log("cleanup", f"previous build archived as {archive.ARCHIVE_DIR_NAME}/{kept.path.name}")


def _instances_for_exe(exe_path: Path, instances: Sequence[Instance] | None) -> list[Instance]:
    from . import discovery

    known: Iterable[Instance] = instances if instances is not None else discovery.discover()
    wanted = _normcase(exe_path)
    return [instance for instance in known if instance.exe_path is not None and _normcase(instance.exe_path) == wanted]


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def describe(plan: UpdatePlan, *, restart: bool = True) -> list[str]:
    """Every step :func:`apply` would take, as lines, touching nothing."""
    exe_path = plan.exe_path
    download = download_path_for(exe_path)
    backup = backup_path_for(exe_path)
    asset = plan.target.asset
    running = _running_instances(plan)
    local = versions.display_version(plan.current_version) or "?"

    lines = [
        f"would update {plan.kind.display_name} {local} -> {plan.target.version} ({plan.target.tag}) at {exe_path}",
    ]
    if asset is None:
        lines.append(f"would stop: release {plan.target.tag} publishes no .exe asset")
        return lines
    lines.append(f"would download {asset.name} ({asset.size} bytes) to {download}")
    if normalize_digest(asset.digest):
        lines.append("would verify sha256 against the GitHub API digest")
    elif plan.target.sidecar_sha_url:
        lines.append(f"would verify sha256 against {plan.target.sidecar_sha_url}")
    else:
        lines.append("would find no published sha256 and ask for explicit confirmation")
    lines.append(f"would read the Authenticode state of {download.name} (information only)")
    for instance in running:
        lines.append(process_control.describe_stop(instance))
    if not running:
        lines.append("would stop nothing: no running instance uses this executable")
    lines.append(
        f"would rename {exe_path.name} to {backup.name} (retry {DEFAULT_SWAP_ATTEMPTS}x{DEFAULT_SWAP_DELAY:g}s)"
    )
    lines.append(f"would rename {download.name} to {exe_path.name}, restoring {backup.name} on failure")
    if restart:
        for instance in running:
            lines.append(process_control.describe_start(instance))
    elif running:
        lines.append("would leave the instances stopped (--no-restart)")
    lines.append(
        f"would check after {DEFAULT_POST_CHECK_DELAY:g}s that the instances run and the PE version is "
        f"{plan.target.version}, then delete {backup.name}"
    )
    return lines
