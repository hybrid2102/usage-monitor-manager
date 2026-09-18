"""Periodic update checks and notifications (PLAN.md section 9.1).

A background thread asks :func:`updater.plan_updates` for the state of the
world every ``check_interval_hours`` (default 6), keeps the answer in an
in-memory :class:`UpdateState` that the tray and the window read, and calls
``on_updates_available`` once per *new* release so the user gets one toast for
"Copilot 0.3.2 disponibile" instead of one every six hours forever.

Three rules:

* **the first check is not at startup**: it happens ``first_delay`` seconds
  later (30 s by default), so the tray appears instantly and a machine that
  just booted is not competing with everything else for the network;
* **nothing is ever applied**: the scheduler only looks. Applying an update
  stays an explicit click (PLAN.md section 6.5);
* **dedupe by ``(repo, tag)``**, and a tag listed in ``skipped_versions``
  never notifies at all (the "salta versione" of section 9.5, whose UI lands
  in Phase 6 - the scheduler already honours the stored value).

Everything that touches time or the network is injected: ``clock``, ``wait``
and ``plan_provider``. The tests drive a whole week of checks in microseconds
and never reach GitHub.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, manager_config
from .model import JsonDict, UpdatePlan
from .self_update import MANAGER_REPO, SelfUpdateCheck, check_self
from .versions import VersionStatus

__all__ = [
    "DEFAULT_FIRST_DELAY_SECONDS",
    "Clock",
    "PlanProvider",
    "SelfCheckProvider",
    "UpdateScheduler",
    "UpdateState",
    "Waiter",
    "default_plan_provider",
]

DEFAULT_FIRST_DELAY_SECONDS = 30.0
"""How long after start the first check runs (PLAN.md section 9.1)."""

Clock = Callable[[], datetime]
"""Returns "now" as an aware datetime."""

Waiter = Callable[[float], bool]
"""Sleep for N seconds; return ``True`` when the scheduler must stop instead."""

PlanProvider = Callable[[], Sequence[UpdatePlan]]
"""Produces the current update plans; the default one hits discovery + GitHub."""

SelfCheckProvider = Callable[[bool], SelfUpdateCheck]
"""``provider(include_prerelease)``: the manager's own update state."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class UpdateState:
    """The scheduler's view of the world, as the UI reads it.

    Immutable on purpose: :attr:`UpdateScheduler.state` handing out a frozen
    snapshot means a menu being built cannot see the list change underneath it.
    """

    last_check: datetime | None = None
    plans: tuple[UpdatePlan, ...] = ()
    available: tuple[UpdatePlan, ...] = ()
    error: str | None = None
    checking: bool = False
    skipped: tuple[UpdatePlan, ...] = ()
    """Updates that exist but carry a tag the user chose to skip (section 9.5)."""
    self_update: SelfUpdateCheck | None = None
    """The manager's own update state; ``None`` before the first check."""

    @property
    def update_count(self) -> int:
        """How many executables have an update the user has not skipped.

        The manager itself counts as one, so the tray badge shows "2" when a
        monitor and the manager are both behind.
        """
        return len(self.available) + (1 if self.self_update is not None and self.self_update.available else 0)

    def to_dict(self) -> JsonDict:
        return {
            "last_check": None if self.last_check is None else self.last_check.isoformat(),
            "plans": [plan.to_dict() for plan in self.plans],
            "available": [plan.to_dict() for plan in self.available],
            "skipped": [plan.to_dict() for plan in self.skipped],
            "update_count": self.update_count,
            "error": self.error,
            "checking": self.checking,
            "self_update": None if self.self_update is None else self.self_update.to_dict(),
        }


def default_plan_provider(
    *,
    include_prerelease: bool = False,
    prerelease_for: Callable[[str], bool] | None = None,
) -> Sequence[UpdatePlan]:
    """Discover the instances and ask GitHub: the real check.

    Imported lazily so that ``import scheduler`` costs nothing (psutil and the
    network stack stay out of the tray's startup path).
    """
    from .discovery import discover
    from .updater import plan_updates

    return plan_updates(discover(), include_prerelease=include_prerelease, prerelease_for=prerelease_for)


def _is_available(plan: UpdatePlan) -> bool:
    from .updater import plan_status
    from .versions import VersionStatus

    return plan_status(plan) is VersionStatus.UPDATE_AVAILABLE


class UpdateScheduler:
    """Background update checker; one instance per running manager.

    Usage::

        scheduler = UpdateScheduler(on_updates_available=notify)
        scheduler.start()
        ...
        scheduler.stop()

    :meth:`check_now` is safe to call from a UI thread (the tray's "Controlla
    aggiornamenti" menu entry does exactly that): it runs the check on the
    calling thread and returns the new state.
    """

    def __init__(
        self,
        *,
        on_updates_available: Callable[[Sequence[UpdatePlan]], None] | None = None,
        on_state_change: Callable[[UpdateState], None] | None = None,
        plan_provider: PlanProvider | None = None,
        self_check_provider: SelfCheckProvider | None = None,
        on_self_update_available: Callable[[SelfUpdateCheck], None] | None = None,
        check_self_update: bool = True,
        config_path: Path | None = None,
        clock: Clock = _utc_now,
        wait: Waiter | None = None,
        first_delay: float = DEFAULT_FIRST_DELAY_SECONDS,
    ) -> None:
        self._on_updates = on_updates_available
        self._on_state = on_state_change
        self._plan_provider = plan_provider
        self._self_check_provider = self_check_provider
        self._on_self_update = on_self_update_available
        self._check_self = check_self_update
        self._config_path = config_path
        self._clock = clock
        self._first_delay = first_delay
        self._stop = threading.Event()
        self._wait: Waiter = wait if wait is not None else self._default_wait
        self._lock = threading.Lock()
        self._state = UpdateState()
        self._notified: set[tuple[str, str]] = set()
        self._self_notified: set[str] = set()
        self._thread: threading.Thread | None = None

    # -- state ---------------------------------------------------------------

    @property
    def state(self) -> UpdateState:
        """A snapshot of the last check."""
        with self._lock:
            return self._state

    def _set_state(self, **changes: object) -> UpdateState:
        with self._lock:
            self._state = replace(self._state, **changes)  # pyright: ignore[reportArgumentType]
            snapshot = self._state
        if self._on_state is not None:
            self._on_state(snapshot)
        return snapshot

    # -- configuration -------------------------------------------------------

    def _config(self) -> manager_config.ManagerConfig:
        return manager_config.load(self._config_path)

    def interval_seconds(self) -> float:
        """The configured interval in seconds, re-read before every sleep."""
        return float(self._config().preferences.check_interval_hours) * 3600.0

    # -- the check itself ----------------------------------------------------

    def _provide(self, config: manager_config.ManagerConfig) -> Sequence[UpdatePlan]:
        if self._plan_provider is not None:
            return self._plan_provider()
        return default_plan_provider(
            include_prerelease=config.preferences.include_prerelease,
            prerelease_for=config.include_prerelease_for,
        )

    def _provide_self(self, config: manager_config.ManagerConfig) -> SelfUpdateCheck | None:
        """The manager's own repo, checked alongside the monitors (section 6.5)."""
        if not self._check_self:
            return None
        include_prerelease = config.include_prerelease_for(MANAGER_REPO)
        if self._self_check_provider is not None:
            return self._self_check_provider(include_prerelease)
        return check_self(
            include_prerelease=include_prerelease,
            skipped_tag=config.skipped_versions.get(MANAGER_REPO),
        )

    def check_now(self) -> UpdateState:
        """Run one check, update the state, notify about new releases.

        Never raises: a network failure, a rate limit or a broken release feed
        become :attr:`UpdateState.error`, because a failed check must not take
        the tray down with it.
        """
        config = self._config()
        self._set_state(checking=True)
        try:
            plans = tuple(self._provide(config))
        except Exception as error:  # noqa: BLE001 - the UI shows the text, whatever it is
            return self._set_state(checking=False, error=f"{type(error).__name__}: {error}", last_check=self._clock())
        outdated = tuple(plan for plan in plans if _is_available(plan))
        available = tuple(plan for plan in outdated if not config.is_skipped(plan.kind.repo, plan.target.tag))
        skipped = tuple(plan for plan in outdated if config.is_skipped(plan.kind.repo, plan.target.tag))
        try:
            own = self._provide_self(config)
        except Exception as error:  # noqa: BLE001 - check_self swallows its own, this is belt and braces
            own = SelfUpdateCheck(
                status=VersionStatus.UNKNOWN,
                current_version=__version__,
                detail=f"{type(error).__name__}: {error}",
                error=str(error),
            )
        now = self._clock()
        state = self._set_state(
            checking=False,
            error=None,
            plans=plans,
            available=available,
            skipped=skipped,
            self_update=own,
            last_check=now,
        )
        self._remember_check(config, plans, now)
        self._notify(config, available)
        self._notify_self(config, own)
        return state

    def _remember_check(
        self,
        config: manager_config.ManagerConfig,
        plans: Sequence[UpdatePlan],
        now: datetime,
    ) -> None:
        """Persist ``last_check`` per repo; a write failure is not fatal."""
        if not plans:
            return
        last_check = dict(config.last_check)
        stamp = now.isoformat()
        for plan in plans:
            last_check[plan.kind.repo] = stamp
        try:
            manager_config.save(replace(config, last_check=last_check), self._config_path)
        except OSError:
            return

    def _notify(self, config: manager_config.ManagerConfig, available: Sequence[UpdatePlan]) -> None:
        if self._on_updates is None or not config.preferences.notify_updates:
            return
        fresh: list[UpdatePlan] = []
        for plan in available:
            key = (plan.kind.repo, plan.target.tag)
            if key not in self._notified:
                self._notified.add(key)
                fresh.append(plan)
        if fresh:
            self._on_updates(fresh)

    def _notify_self(self, config: manager_config.ManagerConfig, own: SelfUpdateCheck | None) -> None:
        """One toast per newly published manager release, never more."""
        if own is None or not own.available or own.tag is None:
            return
        if self._on_self_update is None or not config.preferences.notify_updates:
            return
        if own.tag in self._self_notified:
            return
        self._self_notified.add(own.tag)
        self._on_self_update(own)

    def forget_notifications(self) -> None:
        """Let already-seen releases notify again (after "salta versione", say)."""
        self._notified.clear()
        self._self_notified.clear()

    # -- thread --------------------------------------------------------------

    def _default_wait(self, seconds: float) -> bool:
        return self._stop.wait(seconds)

    def run_loop(self) -> None:
        """The loop itself, runnable on the caller's thread (that is how it is tested)."""
        delay = self._first_delay
        while not self._wait(delay):
            self.check_now()
            delay = self.interval_seconds()

    def start(self) -> None:
        """Start the background thread; calling it twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(target=self.run_loop, name="umm-update-scheduler", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the loop to end and wait for the thread to finish."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
