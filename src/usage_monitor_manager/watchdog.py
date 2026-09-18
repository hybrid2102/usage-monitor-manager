"""Instance watchdog: crashes, anomalous duplicates, orphan pairs (PLAN.md section 9.2).

A background thread takes a snapshot of the running monitors every 30 seconds
and compares it with the previous one. Three anomalies are reported:

``crashed``
    the ``(kind, suffix)`` was running in the previous snapshot and is not in
    this one. If ``preferences.auto_restart_crashed`` is on, the instance is
    started again; otherwise the user gets a notification and a "Ripara"
    button.
``duplicate``
    two application processes answer for the same ``(kind, suffix)``. The
    monitors are singletons (a Win32 mutex per suffix), so this means one of
    them is wedged. Repair keeps the **oldest** process and terminates the rest.
``orphan``
    the PyInstaller pair is incomplete: an application process without its
    bootloader parent, or a bootloader whose child is gone. Repair stops what
    is left and starts the instance cleanly.

Two design points deserve naming.

**The manager must not report its own work as a crash.** Stopping an instance,
restarting it, or updating its executable all make it disappear between two
snapshots. Every such operation registers the instance in an *expected down*
set (:func:`expect_down`) with a short time to live; the watchdog consults and
purges that set before deciding anything. The set is module-level on purpose:
the CLI, the tray and ``updater.apply`` all announce their intentions to the
same place without having to be handed the watchdog object.

**Nothing here touches a real process in a test.** The snapshot provider, the
clock and the waiter are injected, and the repair actions go through
``process_control``, which the tests already replace.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from . import manager_config
from .model import JsonDict

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .model import Instance

__all__ = [
    "DEFAULT_EXPECTED_TTL",
    "DEFAULT_INTERVAL_SECONDS",
    "EventType",
    "ExpectedDown",
    "InstanceState",
    "Snapshot",
    "Watchdog",
    "WatchdogEvent",
    "WatchdogState",
    "clear_expected",
    "default_snapshot",
    "expect_down",
    "expected_down",
    "is_expected_down",
]

DEFAULT_INTERVAL_SECONDS = 30.0
"""How often the watchdog looks (PLAN.md section 9.2)."""

DEFAULT_EXPECTED_TTL = 90.0
"""How long a manager-initiated stop suppresses the crash detection.

Long enough for a stop plus an update swap plus a restart (the updater waits
five seconds for the post-check alone), short enough that a genuine crash a
couple of minutes later is still seen.
"""

EventType = Literal["crashed", "duplicate", "orphan"]

_SEVERITY: dict[EventType, int] = {"crashed": 3, "duplicate": 2, "orphan": 1}


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


# --------------------------------------------------------------------------- #
# Expected-down registry
# --------------------------------------------------------------------------- #


class ExpectedDown:
    """Instances the manager itself took down, with a time to live."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[str, datetime] = {}

    def expect(self, instance_id: str, ttl: float = DEFAULT_EXPECTED_TTL) -> None:
        """Announce that ``instance_id`` is about to disappear on purpose."""
        if not instance_id:
            return
        with self._lock:
            self._until[instance_id] = self._clock() + timedelta(seconds=max(0.0, ttl))

    def clear(self, instance_id: str) -> None:
        """Forget one announcement (the instance is back up)."""
        with self._lock:
            self._until.pop(instance_id, None)

    def purge(self) -> None:
        """Drop the announcements whose time to live has run out."""
        now = self._clock()
        with self._lock:
            for key in [key for key, deadline in self._until.items() if deadline <= now]:
                del self._until[key]

    def is_expected(self, instance_id: str) -> bool:
        """True while ``instance_id`` is still allowed to be missing."""
        self.purge()
        with self._lock:
            return instance_id in self._until

    def snapshot(self) -> dict[str, datetime]:
        with self._lock:
            return dict(self._until)


_expected = ExpectedDown()


def expected_down() -> ExpectedDown:
    """The process-wide registry every component announces its stops to."""
    return _expected


def expect_down(instance_id: str, ttl: float = DEFAULT_EXPECTED_TTL) -> None:
    """Shorthand for ``expected_down().expect(...)``."""
    _expected.expect(instance_id, ttl)


def clear_expected(instance_id: str) -> None:
    """Shorthand for ``expected_down().clear(...)``."""
    _expected.clear(instance_id)


def is_expected_down(instance_id: str) -> bool:
    """Shorthand for ``expected_down().is_expected(...)``."""
    return _expected.is_expected(instance_id)


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InstanceState:
    """What one ``(kind, suffix)`` looked like at one point in time."""

    instance_id: str
    kind: str
    instance_suffix: str
    config_dir: Path
    exe_path: Path | None = None
    app_pids: tuple[int, ...] = ()
    bootloader_pids: tuple[int, ...] = ()
    incomplete_pairs: int = 0
    started_at: datetime | None = None

    @property
    def running(self) -> bool:
        return bool(self.app_pids)

    def to_dict(self) -> JsonDict:
        return {
            "id": self.instance_id,
            "kind": self.kind,
            "instance_suffix": self.instance_suffix,
            "config_dir": str(self.config_dir),
            "exe_path": None if self.exe_path is None else str(self.exe_path),
            "app_pids": list(self.app_pids),
            "bootloader_pids": list(self.bootloader_pids),
            "incomplete_pairs": self.incomplete_pairs,
            "running": self.running,
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The running world at one instant, keyed by instance id."""

    taken_at: datetime
    states: Mapping[str, InstanceState] = field(default_factory=lambda: {})

    def get(self, instance_id: str) -> InstanceState | None:
        return self.states.get(instance_id)


def default_snapshot() -> Snapshot:
    """Build a snapshot from the live process table.

    Uses :func:`discovery.running_processes`, the *uncollapsed* view: two app
    processes for one suffix stay two entries here, which is the whole point.
    """
    from .discovery import running_processes

    states: dict[str, InstanceState] = {}
    for item in running_processes():
        key = item.instance_id
        previous = states.get(key)
        pair = item.processes
        app_pids = ((previous.app_pids if previous else ()) or ()) + (pair.app_pid,)
        bootloaders = previous.bootloader_pids if previous else ()
        if pair.bootloader_pid is not None:
            bootloaders = (*bootloaders, pair.bootloader_pid)
        incomplete = (previous.incomplete_pairs if previous else 0) + (1 if pair.bootloader_pid is None else 0)
        states[key] = InstanceState(
            instance_id=key,
            kind=item.kind.key,
            instance_suffix=item.instance_suffix,
            config_dir=item.config_dir,
            exe_path=item.exe_path,
            app_pids=tuple(sorted(app_pids)),
            bootloader_pids=tuple(sorted(bootloaders)),
            incomplete_pairs=incomplete,
            started_at=(previous.started_at if previous else None) or pair.started_at,
        )
    return Snapshot(taken_at=_utc_now(), states=states)


# --------------------------------------------------------------------------- #
# Events and state
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WatchdogEvent:
    """One anomaly, as the dashboard badge and the tray tooltip show it."""

    type: EventType
    instance_id: str
    kind: str
    detail: str
    at: datetime
    pids: tuple[int, ...] = ()
    repaired: bool = False
    repair_detail: str | None = None

    @property
    def severity(self) -> int:
        return _SEVERITY.get(self.type, 0)

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "id": self.instance_id,
            "kind": self.kind,
            "detail": self.detail,
            "at": self.at.isoformat(),
            "pids": list(self.pids),
            "repaired": self.repaired,
            "repair_detail": self.repair_detail,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class WatchdogState:
    """The findings of the last poll, as the UI reads them."""

    last_poll: datetime | None = None
    events: tuple[WatchdogEvent, ...] = ()

    @property
    def count(self) -> int:
        return len(self.events)

    def for_instance(self, instance_id: str) -> tuple[WatchdogEvent, ...]:
        return tuple(event for event in self.events if event.instance_id == instance_id)

    def status_of(self, instance_id: str) -> str | None:
        """The worst anomaly of one instance, for the dashboard status column."""
        events = self.for_instance(instance_id)
        if not events:
            return None
        return max(events, key=lambda event: event.severity).type

    def to_dict(self) -> JsonDict:
        return {
            "last_poll": None if self.last_poll is None else self.last_poll.isoformat(),
            "count": self.count,
            "events": [event.to_dict() for event in self.events],
        }


SnapshotProvider = Callable[[], Snapshot]
EventCallback = Callable[[Sequence[WatchdogEvent]], None]
Waiter = Callable[[float], bool]


# --------------------------------------------------------------------------- #
# The watchdog
# --------------------------------------------------------------------------- #


class Watchdog:
    """Compare consecutive snapshots and report (or repair) the anomalies."""

    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider | None = None,
        on_events: EventCallback | None = None,
        config_path: Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
        wait: Waiter | None = None,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        expected: ExpectedDown | None = None,
    ) -> None:
        self._provider = snapshot_provider if snapshot_provider is not None else default_snapshot
        self._on_events = on_events
        self._config_path = config_path
        self._clock = clock
        self._interval = max(1.0, interval)
        self._expected = expected if expected is not None else _expected
        self._stop = threading.Event()
        self._wait: Waiter = wait if wait is not None else self._default_wait
        self._lock = threading.Lock()
        self._state = WatchdogState()
        self._previous: Snapshot | None = None
        self._thread: threading.Thread | None = None

    # -- state ---------------------------------------------------------------

    @property
    def state(self) -> WatchdogState:
        with self._lock:
            return self._state

    @property
    def previous(self) -> Snapshot | None:
        return self._previous

    # -- one pass ------------------------------------------------------------

    def poll(self) -> WatchdogState:
        """One comparison. Never raises: a failed scan clears nothing."""
        try:
            current = self._provider()
        except Exception:  # noqa: BLE001 - a broken scan must not kill the thread
            return self.state
        previous = self._previous
        self._previous = current
        now = self._clock()
        events: list[WatchdogEvent] = []
        if previous is not None:
            events.extend(self._crashes(previous, current, now))
        events.extend(self._duplicates(current, now))
        events.extend(self._orphans(current, now))

        events = [self._maybe_auto_restart(event) for event in events]
        state = WatchdogState(last_poll=now, events=tuple(events))
        with self._lock:
            self._state = state
        if events and self._on_events is not None:
            self._on_events(tuple(events))
        return state

    def _crashes(self, previous: Snapshot, current: Snapshot, now: datetime) -> list[WatchdogEvent]:
        found: list[WatchdogEvent] = []
        for instance_id, before in previous.states.items():
            if not before.running:
                continue
            after = current.get(instance_id)
            if after is not None and after.running:
                self._expected.clear(instance_id)
                continue
            if self._expected.is_expected(instance_id):
                continue
            found.append(
                WatchdogEvent(
                    type="crashed",
                    instance_id=instance_id,
                    kind=before.kind,
                    detail=(
                        f"{instance_id} was running (pid {', '.join(str(pid) for pid in before.app_pids)}) "
                        "and is not any more"
                    ),
                    at=now,
                    pids=before.app_pids,
                )
            )
        return found

    def _duplicates(self, current: Snapshot, now: datetime) -> list[WatchdogEvent]:
        return [
            WatchdogEvent(
                type="duplicate",
                instance_id=state.instance_id,
                kind=state.kind,
                detail=(
                    f"{len(state.app_pids)} processes answer for {state.instance_id} "
                    f"(pid {', '.join(str(pid) for pid in state.app_pids)}); it is a singleton"
                ),
                at=now,
                pids=state.app_pids,
            )
            for state in current.states.values()
            if len(state.app_pids) > 1
        ]

    def _orphans(self, current: Snapshot, now: datetime) -> list[WatchdogEvent]:
        return [
            WatchdogEvent(
                type="orphan",
                instance_id=state.instance_id,
                kind=state.kind,
                detail=(
                    f"{state.instance_id} has an incomplete process pair: "
                    f"{state.incomplete_pairs} of {len(state.app_pids)} without a bootloader parent"
                ),
                at=now,
                pids=state.app_pids,
            )
            for state in current.states.values()
            if state.incomplete_pairs and len(state.app_pids) == 1
        ]

    # -- repair --------------------------------------------------------------

    def _preferences(self) -> manager_config.Preferences:
        return manager_config.load(self._config_path).preferences

    def _maybe_auto_restart(self, event: WatchdogEvent) -> WatchdogEvent:
        if event.type != "crashed" or not self._preferences().auto_restart_crashed:
            return event
        outcome = self.repair(event)
        return replace(
            event,
            repaired=bool(outcome.get("ok")),
            repair_detail=str(outcome.get("detail", "")),
        )

    def repair(self, event: WatchdogEvent) -> JsonDict:
        """Act on one finding: start, deduplicate, or restart an orphan pair."""
        from . import process_control

        instance = self._instance(event.instance_id)
        if event.type == "duplicate":
            return self._kill_duplicates(event)
        if instance is None:
            return {"ok": False, "action": event.type, "detail": f"{event.instance_id} is no longer known"}
        expect_down(instance.instance_id)
        if event.type == "orphan":
            outcome = process_control.restart([instance])
            steps = [step.detail for step in (*outcome.stops, *outcome.starts)]
            return {
                "ok": outcome.ok,
                "action": "restart",
                "detail": "; ".join(steps) or "nothing to restart",
                "id": instance.instance_id,
            }
        started = process_control.start(instance)
        return {"ok": started.ok, "action": "start", "detail": started.detail, "id": instance.instance_id}

    def _kill_duplicates(self, event: WatchdogEvent) -> JsonDict:
        """Terminate every duplicate but the oldest, and only our own processes."""
        from . import process_control

        survivors = sorted(event.pids)
        if len(survivors) < 2:
            return {"ok": True, "action": "kill_duplicates", "detail": "no duplicate left", "killed": []}
        ordered = sorted(survivors, key=self._start_time)
        keep, doomed = ordered[0], ordered[1:]
        killed: list[int] = []
        failed: list[str] = []
        for pid in doomed:
            try:
                proc = process_control.open_process(pid)
                if not self._is_monitor(proc, event):
                    failed.append(f"{pid} is not a monitor process any more")
                    continue
                proc.terminate()
                proc.wait(timeout=10)
            except Exception as error:  # noqa: BLE001 - psutil raises a family of them
                failed.append(f"{pid}: {error}")
                continue
            killed.append(pid)
        detail = f"kept {keep}, terminated {', '.join(str(pid) for pid in killed) or 'nothing'}"
        if failed:
            detail += f"; failed: {'; '.join(failed)}"
        return {"ok": not failed, "action": "kill_duplicates", "detail": detail, "kept": keep, "killed": killed}

    def _start_time(self, pid: int) -> float:
        from . import process_control

        try:
            return float(process_control.open_process(pid).create_time())
        except Exception:  # noqa: BLE001 - an unreadable process sorts last
            return float("inf")

    def _is_monitor(self, proc: Any, event: WatchdogEvent) -> bool:
        """Only terminate a process whose executable is the instance's own."""
        state = self._previous.get(event.instance_id) if self._previous is not None else None
        expected = state.exe_path if state is not None else None
        if expected is None:
            return False
        try:
            actual: object = proc.exe()
        except Exception:  # noqa: BLE001 - access denied means "not ours to kill"
            return False
        return bool(actual) and os.path.normcase(str(actual)) == os.path.normcase(str(expected))

    def _instance(self, instance_id: str) -> Instance | None:
        from .discovery import discover

        for found in discover():
            if found.instance_id == instance_id:
                return found
        return None

    # -- thread --------------------------------------------------------------

    def _default_wait(self, seconds: float) -> bool:
        return self._stop.wait(seconds)

    def run_loop(self) -> None:
        """The loop, runnable on the caller's thread (that is how it is tested)."""
        while not self._wait(self._interval):
            self.poll()

    def start(self) -> None:
        """Start the background thread; calling it twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(target=self.run_loop, name="umm-watchdog", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the loop to end and wait for the thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
