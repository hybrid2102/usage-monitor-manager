"""Self-update of the manager: rename-swap of the running exe and relaunch.

Implements PLAN.md section 6.5. The engine is the one the monitors already use
(:mod:`.github`, :mod:`.integrity`, ``updater.FileSwapper``); what is different
is that the file being replaced is *the running process*.

Windows lets a running executable be **renamed but not overwritten**, which is
the whole sequence::

    UsageMonitorManager.exe.download   <- download + sha256
    UsageMonitorManager.exe            -> UsageMonitorManager.exe.bak
    UsageMonitorManager.exe.download   -> UsageMonitorManager.exe
    Popen([new exe, "--post-update", "<our pid>"])    detached, scrubbed env
    exit the tray cleanly

The new process waits for the old pid to die (it still holds the
single-instance mutex and the ``.bak`` file), deletes the ``.bak``, then starts
the tray normally and says "Manager aggiornato alla vX.Y.Z".

Three rules this module will not bend:

* **frozen builds only.** From a checkout there is no executable to swap;
  :func:`apply_self` refuses with a message instead of renaming
  ``python.exe``.
* **the child gets a scrubbed environment.** ``process_control.inherited_env``
  removes ``_MEIPASS2`` and friends: a one-file build that inherited them
  would try to load the *old*, half-deleted bundle.
* **no release is not an error.** ``hybrid2102/usage-monitor-manager`` has
  published none yet, so :func:`check_self` reports the status ``unknown``
  with "nessuna release pubblicata" and the UI shows exactly that.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, github, process_control, versions
from .integrity import IntegrityResult, authenticode_status, sha256_file
from .model import JsonDict, ReleaseInfo
from .updater import EventCallback, FileSwapper, SwapError, UpdateEvent, UpdateStep, resolve_expected_digest
from .versions import VersionStatus

__all__ = [
    "ASSET_NAME",
    "DEFAULT_WAIT_TIMEOUT",
    "MANAGER_REPO",
    "POST_UPDATE_FLAG",
    "PostUpdateResult",
    "SelfUpdateCheck",
    "SelfUpdateResult",
    "apply_self",
    "check_self",
    "describe_self",
    "finish_post_update",
    "manager_exe_path",
]

MANAGER_REPO = "hybrid2102/usage-monitor-manager"
"""The manager updates itself from its own repository (PLAN.md section 6.5)."""

ASSET_NAME = "UsageMonitorManager.exe"
POST_UPDATE_FLAG = "--post-update"

DEFAULT_WAIT_TIMEOUT = 30.0
"""How long the new process waits for the old one to die before giving up."""

_NO_RELEASE_HINT = "nessuna release pubblicata"


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def is_frozen() -> bool:
    """True inside the PyInstaller build, i.e. when a self-update is possible."""
    return bool(getattr(sys, "frozen", False))


def manager_exe_path() -> Path:
    """The executable to replace: ``sys.executable`` of the frozen build."""
    return Path(sys.executable)


# --------------------------------------------------------------------------- #
# Check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelfUpdateCheck:
    """What the manager knows about its own next version."""

    status: VersionStatus
    current_version: str
    detail: str
    release: ReleaseInfo | None = None
    error: str | None = None
    skipped: bool = False

    @property
    def available(self) -> bool:
        """An update the user has not skipped is waiting."""
        return self.status is VersionStatus.UPDATE_AVAILABLE and not self.skipped

    @property
    def target_version(self) -> str | None:
        return None if self.release is None else self.release.version

    @property
    def tag(self) -> str | None:
        return None if self.release is None else self.release.tag

    def to_dict(self) -> JsonDict:
        return {
            "repo": MANAGER_REPO,
            "status": str(self.status),
            "current_version": self.current_version,
            "target_version": self.target_version,
            "tag": self.tag,
            "detail": self.detail,
            "error": self.error,
            "skipped": self.skipped,
            "available": self.available,
            "frozen": is_frozen(),
            "release": None if self.release is None else self.release.to_dict(),
        }


def check_self(
    *,
    include_prerelease: bool = False,
    token: str | None = None,
    cache_dir: Path | None = None,
    current_version: str | None = None,
    skipped_tag: str | None = None,
) -> SelfUpdateCheck:
    """Ask GitHub whether a newer manager exists. Never raises.

    A repository without releases, a network failure and an exhausted rate
    limit all produce ``status=unknown`` with a readable ``detail``: a manager
    that cannot check itself must still run.
    """
    local = current_version or __version__
    try:
        release = github.fetch_latest_release(
            MANAGER_REPO,
            include_prerelease=include_prerelease,
            token=token,
            cache_dir=cache_dir,
        )
    except github.RateLimitedError as error:
        return SelfUpdateCheck(
            status=VersionStatus.UNKNOWN,
            current_version=local,
            detail=str(error),
            error=str(error),
        )
    except github.GitHubError as error:
        message = str(error)
        no_release = "published no release" in message or "does not exist (404)" in message
        return SelfUpdateCheck(
            status=VersionStatus.UNKNOWN,
            current_version=local,
            detail=f"{_NO_RELEASE_HINT} per {MANAGER_REPO}" if no_release else message,
            error=None if no_release else message,
        )

    status = versions.status_for(local, release.version)
    skipped = bool(skipped_tag) and skipped_tag == release.tag
    if status is VersionStatus.UPDATE_AVAILABLE:
        detail = f"{release.version} disponibile (hai la {local})"
        if skipped:
            detail += " - versione saltata"
    elif status is VersionStatus.NEWER_LOCAL:
        detail = f"build locale {local}, l'ultima pubblicata e' la {release.version}"
    elif status is VersionStatus.UP_TO_DATE:
        detail = f"il manager e' aggiornato ({local})"
    else:  # pragma: no cover - only with an unparsable version
        detail = f"versione non confrontabile ({local} / {release.version})"
    return SelfUpdateCheck(
        status=status,
        current_version=local,
        detail=detail,
        release=release,
        skipped=skipped,
    )


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SelfUpdateResult:
    """Outcome of :func:`apply_self`; ``exiting`` means "now quit the tray"."""

    ok: bool
    detail: str
    from_version: str
    to_version: str | None = None
    tag: str | None = None
    integrity: IntegrityResult | None = None
    exe_path: Path | None = None
    backup_path: Path | None = None
    new_pid: int | None = None
    exiting: bool = False
    events: tuple[UpdateEvent, ...] = field(default=())

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "tag": self.tag,
            "integrity": None if self.integrity is None else self.integrity.to_dict(),
            "exe_path": None if self.exe_path is None else str(self.exe_path),
            "backup_path": None if self.backup_path is None else str(self.backup_path),
            "new_pid": self.new_pid,
            "exiting": self.exiting,
            "events": [event.to_dict() for event in self.events],
        }


Spawner = Callable[[list[str], dict[str, str], Path], int]
"""``(argv, env, cwd) -> pid``; the seam the tests replace to assert the relaunch."""


def apply_self(  # noqa: PLR0911, PLR0915 - the sequence is the unit here
    on_event: EventCallback | None = None,
    *,
    check: SelfUpdateCheck | None = None,
    include_prerelease: bool = False,
    token: str | None = None,
    cache_dir: Path | None = None,
    confirm_unverified: Callable[[], bool] = lambda: False,
    exe_path: Path | None = None,
    frozen: bool | None = None,
    swapper: FileSwapper | None = None,
    spawn: Spawner | None = None,
    on_exit: Callable[[], None] | None = None,
) -> SelfUpdateResult:
    """Replace the running manager and relaunch it, then ask the tray to quit.

    The caller (tray or CLI) is responsible for actually leaving: this function
    returns ``exiting=True`` after spawning the successor and calls ``on_exit``
    if one was given, so the icon and the webview loop can come down in order.
    """
    events: list[UpdateEvent] = []

    def log(step: UpdateStep, message: str, progress: float | None = None) -> None:
        event = UpdateEvent(step=step, message=message, progress=progress)
        events.append(event)
        if on_event is not None:
            on_event(event)

    def result(ok: bool, detail: str, **extra: object) -> SelfUpdateResult:
        log("done" if ok else "abort", detail)
        return SelfUpdateResult(
            ok=ok,
            detail=detail,
            from_version=__version__,
            events=tuple(events),
            **extra,  # type: ignore[arg-type]
        )

    running_frozen = is_frozen() if frozen is None else frozen
    if not running_frozen:
        return result(
            False,
            "self-update non disponibile da sorgente: aggiorna il checkout con git, oppure usa UsageMonitorManager.exe",
        )

    state = (
        check
        if check is not None
        else check_self(include_prerelease=include_prerelease, token=token, cache_dir=cache_dir)
    )
    if state.release is None or state.status is not VersionStatus.UPDATE_AVAILABLE:
        return result(False, f"nessun aggiornamento da applicare: {state.detail}")
    release = state.release
    asset = release.asset
    if asset is None:
        return result(False, f"la release {release.tag} non pubblica un asset {ASSET_NAME}")

    exe = Path(exe_path) if exe_path is not None else manager_exe_path()
    download = exe.with_name(f"{exe.name}.download")
    backup = exe.with_name(f"{exe.name}.bak")
    swap = swapper if swapper is not None else FileSwapper(on_retry=lambda message: log("swap", message))

    log("plan", f"manager {__version__} -> {release.version} ({release.tag}) in {exe}")

    def progress(done: int, total: int) -> None:
        log("download", f"{asset.name}: {done} of {total or '?'} bytes", (done / total) if total else None)

    try:
        github.download(asset, download, progress, token=token)
    except github.GitHubError as error:
        swap.discard(download)
        return result(False, f"download non riuscito: {error}", exe_path=exe)

    expected, source = resolve_expected_digest(release, token=token, on_note=lambda message: log("verify", message))
    signature = authenticode_status(download)
    if expected is None:
        integrity = IntegrityResult(hash_verified=False, source="none", signature=signature)
        log("verify", integrity.summary())
        if not confirm_unverified():
            swap.discard(download)
            return result(
                False,
                f"annullato: {release.tag} non pubblica uno sha256 e il download non verificato "
                "non e' stato confermato",
                integrity=integrity,
                exe_path=exe,
            )
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
                f"sha256 diverso: atteso {expected}, ottenuto {actual}; download eliminato, nulla e' stato toccato",
                integrity=integrity,
                exe_path=exe,
            )
    log("signature", f"Authenticode: {signature} (informativo, le build non sono firmate)")

    try:
        swap.backup(exe)
        log("backup", f"{exe.name} spostato in {backup.name}")
    except SwapError as error:
        swap.discard(download)
        return result(False, f"annullato: {error}", integrity=integrity, exe_path=exe)

    try:
        swap.promote(download, exe)
    except SwapError as error:
        log("restore", f"ripristino di {backup.name}: {error}")
        try:
            swap.restore(backup, exe)
            restored = True
        except SwapError as restore_error:
            log("restore", f"anche il ripristino e' fallito: {restore_error}")
            restored = False
        detail = (
            f"swap non riuscito, eseguibile precedente ripristinato: {error}"
            if restored
            else f"swap non riuscito E ripristino fallito: {error}; {backup} contiene ancora il manager"
        )
        return result(False, detail, integrity=integrity, exe_path=exe, backup_path=None if restored else backup)
    log("swap", f"{download.name} e' ora {exe.name}")

    argv = [str(exe), POST_UPDATE_FLAG, str(os.getpid())]
    env = process_control.inherited_env()
    launcher = spawn if spawn is not None else _default_spawn
    try:
        pid = launcher(argv, env, exe.parent)
    except OSError as error:
        return result(
            False,
            f"il nuovo manager non e' partito ({error}); {exe.name} e' gia' la {release.version}, "
            f"riavvialo a mano ({backup.name} contiene la versione precedente)",
            integrity=integrity,
            exe_path=exe,
            backup_path=backup,
            to_version=release.version,
            tag=release.tag,
        )
    log("start", f"nuovo manager avviato (pid {pid}) con {POST_UPDATE_FLAG} {os.getpid()}")
    if on_exit is not None:
        on_exit()
    return result(
        True,
        f"aggiornato alla {release.version}: il nuovo processo sta subentrando",
        integrity=integrity,
        exe_path=exe,
        backup_path=backup,
        to_version=release.version,
        tag=release.tag,
        new_pid=pid,
        exiting=True,
    )


def _default_spawn(argv: list[str], env: dict[str, str], cwd: Path) -> int:
    return process_control.spawn_detached(argv, env, cwd)


# --------------------------------------------------------------------------- #
# --post-update
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PostUpdateResult:
    """What the successor process managed to do before starting the tray."""

    ok: bool
    detail: str
    old_pid: int
    version: str = __version__
    backup_removed: bool = False
    waited_seconds: float = 0.0
    message: str = ""

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "old_pid": self.old_pid,
            "version": self.version,
            "backup_removed": self.backup_removed,
            "waited_seconds": round(self.waited_seconds, 3),
            "message": self.message,
        }


def _pid_alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 - psutil raises a family of them
        return False


def finish_post_update(
    old_pid: int,
    *,
    timeout: float = DEFAULT_WAIT_TIMEOUT,
    exe_path: Path | None = None,
    poll_interval: float = 0.25,
    is_alive: Callable[[int], bool] | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> PostUpdateResult:
    """Wait for the previous manager to die, then drop its ``.bak``.

    Returns a result whose ``message`` is the notification the tray shows
    ("Manager aggiornato alla vX.Y.Z"). Failing to remove the ``.bak`` is not
    fatal: it is one stale file, and refusing to start the tray over it would
    be worse.
    """
    alive = is_alive if is_alive is not None else _pid_alive
    wait = sleep if sleep is not None else _sleep
    clock = monotonic if monotonic is not None else _monotonic

    exe = Path(exe_path) if exe_path is not None else manager_exe_path()
    backup = exe.with_name(f"{exe.name}.bak")
    message = f"Manager aggiornato alla v{__version__}"

    started = clock()
    waited = 0.0
    while old_pid > 0 and alive(old_pid):
        waited = clock() - started
        if waited >= timeout:
            return PostUpdateResult(
                ok=False,
                detail=f"il processo precedente ({old_pid}) e' ancora vivo dopo {timeout:g}s",
                old_pid=old_pid,
                waited_seconds=waited,
                message=message,
            )
        wait(poll_interval)
    waited = clock() - started

    removed = False
    if backup.exists():
        for attempt in range(1, 4):
            try:
                backup.unlink(missing_ok=True)
                removed = True
                break
            except OSError:
                if attempt == 3:
                    break
                wait(1.0)
    detail = f"processo {old_pid} terminato dopo {waited:.1f}s" if old_pid > 0 else "nessun processo da attendere"
    detail += f"; {backup.name} rimosso" if removed else f"; {backup.name} non presente o non rimovibile"
    return PostUpdateResult(
        ok=True,
        detail=detail,
        old_pid=old_pid,
        backup_removed=removed,
        waited_seconds=waited,
        message=message,
    )


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def describe_self(check: SelfUpdateCheck, *, exe_path: Path | None = None) -> list[str]:
    """Every step :func:`apply_self` would take, touching nothing."""
    if not is_frozen():
        return ["non farebbe nulla: il self-update funziona solo sulla build congelata (.exe)"]
    if check.release is None or check.status is not VersionStatus.UPDATE_AVAILABLE:
        return [f"non farebbe nulla: {check.detail}"]
    exe = Path(exe_path) if exe_path is not None else manager_exe_path()
    release = check.release
    asset = release.asset
    lines = [f"aggiornerebbe il manager {__version__} -> {release.version} ({release.tag}) in {exe}"]
    if asset is None:
        lines.append(f"si fermerebbe: la release {release.tag} non pubblica un asset {ASSET_NAME}")
        return lines
    lines.append(f"scaricherebbe {asset.name} ({asset.size} byte) in {exe.name}.download")
    lines.append(
        "verificherebbe lo sha256 dal digest dell'API"
        if asset.digest
        else (
            f"verificherebbe lo sha256 da {release.sidecar_sha_url}"
            if release.sidecar_sha_url
            else "non troverebbe nessuno sha256 e chiederebbe conferma esplicita"
        )
    )
    lines.append(f"rinominerebbe {exe.name} in {exe.name}.bak e {exe.name}.download in {exe.name}")
    lines.append(f"avvierebbe {exe.name} {POST_UPDATE_FLAG} <pid> con ambiente ripulito e uscirebbe")
    lines.append(f"il nuovo processo attenderebbe la morte del pid, cancellerebbe {exe.name}.bak e notificherebbe")
    return lines
