"""Launch profiles: named sets of instances started or stopped together.

PLAN.md section 9.13: "Lavoro" = Claude + Copilot, "Personale" = Claude valeria
+ Codex, one click applies the set. A profile is a name plus a list of instance
ids, stored in ``manager-config.json``.

Applying one is deliberately narrow:

* the listed instances are **started** if they are not already running;
* the instances **not** listed are **stopped** - but only those of the
  ``usage-monitor`` family. A ``github-exe`` entry (PLAN.md section 9.9) is
  never stopped by a profile: those executables are not instances the user
  thinks of as "a monitor I run for a session", and silently killing an agent
  monitor because it was missing from a profile would be exactly the kind of
  surprise this manager exists to avoid.
* an instance whose executable is unknown is reported and skipped rather than
  guessed at.

:func:`plan` computes all of that without touching anything, which is what
``--dry-run`` prints and what the tests assert on; :func:`apply_plan` is the
only function here that starts or stops a process.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import manager_config, process_control
from .errors import NoMatchError
from .i18n import T, t
from .model import Instance, JsonDict

__all__ = [
    "ProfileApplyResult",
    "ProfilePlan",
    "apply_plan",
    "apply_profile",
    "describe",
    "list_profiles",
    "plan",
    "resolve",
]


def list_profiles(path: Path | None = None) -> list[manager_config.Profile]:
    """The configured launch profiles, in declaration order."""
    return list(manager_config.load(path).profiles)


def resolve(name: str, path: Path | None = None) -> manager_config.Profile | None:
    """The profile called ``name`` (case-insensitive), or ``None``."""
    return manager_config.find_profile(manager_config.load(path), name)


@dataclass(frozen=True, slots=True)
class ProfilePlan:
    """What applying a profile would do, as three disjoint sets."""

    name: str
    to_start: tuple[Instance, ...] = ()
    to_stop: tuple[Instance, ...] = ()
    already_running: tuple[Instance, ...] = ()
    unknown_ids: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    """Explanations for the members that cannot be acted on (no executable)."""

    @property
    def is_empty(self) -> bool:
        return not self.to_start and not self.to_stop

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "to_start": [instance.instance_id for instance in self.to_start],
            "to_stop": [instance.instance_id for instance in self.to_stop],
            "already_running": [instance.instance_id for instance in self.already_running],
            "unknown_ids": list(self.unknown_ids),
            "skipped": list(self.skipped),
        }


def plan(
    profile: manager_config.Profile,
    instances: Sequence[Instance],
    *,
    stop_others: bool = True,
) -> ProfilePlan:
    """Split ``instances`` into "start these", "stop those", "leave alone".

    Membership is matched on :attr:`Instance.instance_id`, case-insensitively,
    which is what ``umm list`` prints and what the profile stores.
    """
    wanted = {instance_id.casefold() for instance_id in profile.instance_ids}
    by_id = {instance.instance_id.casefold(): instance for instance in instances}

    to_start: list[Instance] = []
    to_stop: list[Instance] = []
    running: list[Instance] = []
    skipped: list[str] = []

    for instance in instances:
        member = instance.instance_id.casefold() in wanted
        if member:
            if instance.is_running:
                running.append(instance)
            elif instance.exe_path is None:
                skipped.append(f"{instance.instance_id}: executable path unknown, not started")
            else:
                to_start.append(instance)
            continue
        if not stop_others or not instance.is_running:
            continue
        if not instance.kind.has_config_dir:
            skipped.append(f"{instance.instance_id}: {instance.kind.family} kind, left running")
            continue
        to_stop.append(instance)

    unknown = tuple(sorted(instance_id for instance_id in profile.instance_ids if instance_id.casefold() not in by_id))
    return ProfilePlan(
        name=profile.name,
        to_start=tuple(to_start),
        to_stop=tuple(to_stop),
        already_running=tuple(running),
        unknown_ids=unknown,
        skipped=tuple(skipped),
    )


def describe(profile_plan: ProfilePlan) -> list[str]:
    """One line per step, for ``--dry-run`` and for the confirmation prompt."""
    lines = [f"profile {profile_plan.name}:"]
    for instance in profile_plan.to_stop:
        lines.append(process_control.describe_stop(instance))
    for instance in profile_plan.to_start:
        lines.append(process_control.describe_start(instance))
    for instance in profile_plan.already_running:
        lines.append(f"would leave {instance.instance_id} running")
    for note in profile_plan.skipped:
        lines.append(f"would skip {note}")
    for instance_id in profile_plan.unknown_ids:
        lines.append(f"would skip {instance_id}: no such instance")
    if profile_plan.is_empty:
        lines.append("nothing to do: the profile already matches the machine")
    return lines


@dataclass(frozen=True, slots=True)
class ProfileApplyResult:
    """Outcome of :func:`apply_plan`: every stop, then every start."""

    name: str
    stops: tuple[process_control.StopResult, ...] = ()
    starts: tuple[process_control.StartResult, ...] = ()
    unknown_ids: tuple[str, ...] = ()
    skipped: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.stops) and all(result.ok for result in self.starts)

    def to_dict(self) -> JsonDict:
        return {
            "profile": self.name,
            "ok": self.ok,
            "stops": [result.to_dict() for result in self.stops],
            "starts": [result.to_dict() for result in self.starts],
            "unknown_ids": list(self.unknown_ids),
            "skipped": list(self.skipped),
        }


def apply_plan(
    profile_plan: ProfilePlan,
    *,
    gap_seconds: float = process_control.DEFAULT_GAP_SECONDS,
    stop_timeout: float = process_control.DEFAULT_STOP_TIMEOUT,
    start_wait: float = process_control.DEFAULT_START_WAIT,
    on_event: process_control.EventCallback | None = None,
) -> ProfileApplyResult:
    """Stop what must go, then start what must come up, with the usual gap."""
    stops = tuple(
        process_control.stop(instance, timeout=stop_timeout, on_event=on_event) for instance in profile_plan.to_stop
    )
    starts: list[process_control.StartResult] = []
    for index, instance in enumerate(profile_plan.to_start):
        if index and gap_seconds > 0:
            # Same two-second gap process_control.restart uses: two instances of
            # one executable coming up together collide over WebView2.
            time.sleep(gap_seconds)
        starts.append(process_control.start(instance, wait_seconds=start_wait, on_event=on_event))
    return ProfileApplyResult(
        name=profile_plan.name,
        stops=stops,
        starts=tuple(starts),
        unknown_ids=profile_plan.unknown_ids,
        skipped=profile_plan.skipped,
    )


def apply_profile(
    name: str,
    instances: Sequence[Instance] | None = None,
    *,
    path: Path | None = None,
    stop_others: bool = True,
    on_event: process_control.EventCallback | None = None,
) -> ProfileApplyResult:
    """Look the profile up, plan it against the machine, apply it.

    Raises:
        NoMatchError: when no profile carries that name (exit code 3).
    """
    profile = resolve(name, path)
    if profile is None:
        raise NoMatchError(t("cli.no_such_profile", name=name), hint=T["error.hint_profile_list"])
    from .discovery import discover  # noqa: PLC0415 - imported late, like the rest of the CLI paths

    known = list(instances) if instances is not None else discover()
    return apply_plan(plan(profile, known, stop_others=stop_others), on_event=on_event)
