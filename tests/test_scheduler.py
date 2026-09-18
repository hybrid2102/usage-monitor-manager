"""Tests for the periodic update check (PLAN.md section 9.1).

The scheduler is driven entirely through its injected ``clock``, ``wait`` and
``plan_provider``: no thread sleeps, no network call, no real config file.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from usage_monitor_manager import manager_config
from usage_monitor_manager.kinds import get_kind
from usage_monitor_manager.manager_config import ManagerConfig, Preferences
from usage_monitor_manager.model import ReleaseInfo, UpdatePlan
from usage_monitor_manager.scheduler import DEFAULT_FIRST_DELAY_SECONDS, UpdateScheduler

START = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)


class FakeClock:
    """A clock that only moves when the (fake) waiter sleeps."""

    def __init__(self) -> None:
        self.now = START
        self.waits: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_waiter(clock: FakeClock, *, rounds: int) -> object:
    """Return a waiter that lets the loop run ``rounds`` times, then stops it."""
    state = {"left": rounds}

    def wait(seconds: float) -> bool:
        clock.waits.append(seconds)
        if state["left"] <= 0:
            return True
        state["left"] -= 1
        clock.advance(seconds)
        return False

    return wait


def plan(kind_key: str, *, local: str | None, tag: str) -> UpdatePlan:
    kind = get_kind(kind_key)
    return UpdatePlan(
        exe_path=Path(rf"C:\Tools\{kind.exe_name}"),
        kind=kind,
        current_version=local,
        target=ReleaseInfo(
            tag=tag,
            version=tag.lstrip("v"),
            published_at=START,
            html_url=f"https://example.invalid/{kind.repo}/releases/{tag}",
            body_markdown="## notes",
        ),
        affected_instances=(),
    )


COPILOT_OUTDATED = plan("copilot", local="0.3.1", tag="v0.3.2")
CODEX_CURRENT = plan("codex", local="0.1.0", tag="v0.1.0")


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "manager-config.json"
    manager_config.save(ManagerConfig(), path)
    return path


def test_check_now_records_available_updates(config_file: Path) -> None:
    clock = FakeClock()
    notified: list[Sequence[UpdatePlan]] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_updates_available=notified.append,
        plan_provider=lambda: [COPILOT_OUTDATED, CODEX_CURRENT],
        config_path=config_file,
        clock=clock,
    )
    state = scheduler.check_now()

    assert state.checking is False
    assert state.error is None
    assert state.last_check == START
    assert len(state.plans) == 2
    assert state.available == (COPILOT_OUTDATED,)
    assert state.update_count == 1
    assert [p.kind.key for p in notified[0]] == ["copilot"]
    assert state.to_dict()["update_count"] == 1


def test_last_check_is_persisted_per_repo(config_file: Path) -> None:
    scheduler = UpdateScheduler(
        check_self_update=False,
        plan_provider=lambda: [COPILOT_OUTDATED, CODEX_CURRENT],
        config_path=config_file,
        clock=FakeClock(),
    )
    scheduler.check_now()
    stored = manager_config.load(config_file).last_check
    assert stored[COPILOT_OUTDATED.kind.repo] == START.isoformat()
    assert stored[CODEX_CURRENT.kind.repo] == START.isoformat()


def test_notification_is_deduped_by_repo_and_tag(config_file: Path) -> None:
    notified: list[Sequence[UpdatePlan]] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_updates_available=notified.append,
        plan_provider=lambda: [COPILOT_OUTDATED],
        config_path=config_file,
        clock=FakeClock(),
    )
    scheduler.check_now()
    scheduler.check_now()
    scheduler.check_now()
    assert len(notified) == 1

    # a newer tag is a new notification
    scheduler._plan_provider = lambda: [plan("copilot", local="0.3.1", tag="v0.3.3")]  # pyright: ignore[reportPrivateUsage]
    scheduler.check_now()
    assert len(notified) == 2
    assert notified[1][0].target.tag == "v0.3.3"


def test_forget_notifications_lets_a_release_notify_again(config_file: Path) -> None:
    notified: list[Sequence[UpdatePlan]] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_updates_available=notified.append,
        plan_provider=lambda: [COPILOT_OUTDATED],
        config_path=config_file,
        clock=FakeClock(),
    )
    scheduler.check_now()
    scheduler.forget_notifications()
    scheduler.check_now()
    assert len(notified) == 2


def test_skipped_version_is_neither_counted_nor_notified(config_file: Path) -> None:
    manager_config.save(
        ManagerConfig(skipped_versions={COPILOT_OUTDATED.kind.repo: "v0.3.2"}),
        config_file,
    )
    notified: list[Sequence[UpdatePlan]] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_updates_available=notified.append,
        plan_provider=lambda: [COPILOT_OUTDATED],
        config_path=config_file,
        clock=FakeClock(),
    )
    state = scheduler.check_now()
    assert state.plans == (COPILOT_OUTDATED,)
    assert state.available == ()
    assert notified == []


def test_notifications_can_be_switched_off(config_file: Path) -> None:
    manager_config.save(ManagerConfig(preferences=Preferences(notify_updates=False)), config_file)
    notified: list[Sequence[UpdatePlan]] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_updates_available=notified.append,
        plan_provider=lambda: [COPILOT_OUTDATED],
        config_path=config_file,
        clock=FakeClock(),
    )
    assert scheduler.check_now().update_count == 1
    assert notified == []


def test_a_failing_check_becomes_an_error_not_an_exception(config_file: Path) -> None:
    def boom() -> Sequence[UpdatePlan]:
        raise RuntimeError("rate limit exceeded")

    scheduler = UpdateScheduler(check_self_update=False, plan_provider=boom, config_path=config_file, clock=FakeClock())
    state = scheduler.check_now()
    assert state.error == "RuntimeError: rate limit exceeded"
    assert state.checking is False
    assert state.last_check == START


def test_loop_waits_30s_then_the_configured_interval(config_file: Path) -> None:
    manager_config.save(ManagerConfig(preferences=Preferences(check_interval_hours=6)), config_file)
    clock = FakeClock()
    checks: list[datetime] = []

    def provider() -> Sequence[UpdatePlan]:
        checks.append(clock.now)
        return [CODEX_CURRENT]

    scheduler = UpdateScheduler(
        check_self_update=False,
        plan_provider=provider,
        config_path=config_file,
        clock=clock,
        wait=make_waiter(clock, rounds=3),  # pyright: ignore[reportArgumentType]
    )
    scheduler.run_loop()

    assert clock.waits[0] == DEFAULT_FIRST_DELAY_SECONDS
    assert clock.waits[1:4] == [6 * 3600.0, 6 * 3600.0, 6 * 3600.0]
    assert len(checks) == 3
    assert checks[0] == START + timedelta(seconds=30)
    assert checks[1] == checks[0] + timedelta(hours=6)


def test_interval_change_takes_effect_on_the_next_sleep(config_file: Path) -> None:
    clock = FakeClock()
    calls = {"n": 0}

    def provider() -> Sequence[UpdatePlan]:
        calls["n"] += 1
        if calls["n"] == 1:
            manager_config.set_preference("check_interval_hours", 1, config_file)
        return []

    scheduler = UpdateScheduler(
        check_self_update=False,
        plan_provider=provider,
        config_path=config_file,
        clock=clock,
        wait=make_waiter(clock, rounds=2),  # pyright: ignore[reportArgumentType]
    )
    scheduler.run_loop()
    assert clock.waits[0] == DEFAULT_FIRST_DELAY_SECONDS
    assert clock.waits[1] == 3600.0


def test_state_change_callback_sees_the_checking_flag(config_file: Path) -> None:
    seen: list[bool] = []
    scheduler = UpdateScheduler(
        check_self_update=False,
        on_state_change=lambda state: seen.append(state.checking),
        plan_provider=lambda: [],
        config_path=config_file,
        clock=FakeClock(),
    )
    scheduler.check_now()
    assert seen == [True, False]


def test_empty_result_does_not_write_the_config(config_file: Path) -> None:
    before = config_file.read_text(encoding="utf-8")
    scheduler = UpdateScheduler(
        check_self_update=False, plan_provider=lambda: [], config_path=config_file, clock=FakeClock()
    )
    scheduler.check_now()
    assert config_file.read_text(encoding="utf-8") == before


def test_start_and_stop_the_real_thread(config_file: Path) -> None:
    """The only test that uses a real thread: it must stop promptly."""
    scheduler = UpdateScheduler(
        check_self_update=False,
        plan_provider=lambda: [],
        config_path=config_file,
        clock=FakeClock(),
        first_delay=30.0,
    )
    scheduler.start()
    assert scheduler.running is True
    scheduler.start()  # idempotent
    scheduler.stop(timeout=5.0)
    assert scheduler.running is False
    assert scheduler.state.last_check is None  # it never got past the first wait


def test_declared_instances_are_untouched_by_the_scheduler(config_file: Path) -> None:
    """Persisting ``last_check`` must not drop the rest of the document."""
    manager_config.save(
        ManagerConfig(
            instances=(
                manager_config.DeclaredInstance(kind="claude", config_dir=Path("C:/x/.claude"), label="Claude"),
            ),
            legacy_import_done=True,
        ),
        config_file,
    )
    scheduler = UpdateScheduler(
        check_self_update=False, plan_provider=lambda: [CODEX_CURRENT], config_path=config_file, clock=FakeClock()
    )
    scheduler.check_now()
    config = manager_config.load(config_file)
    assert config.legacy_import_done is True
    assert config.instances[0].label == "Claude"
