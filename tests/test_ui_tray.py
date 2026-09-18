"""Tests for the tray: menu labels, badge, dispatch - no icon is ever run.

The menu is asserted on the pure :class:`MenuEntry` tree, which is exactly the
labels of PLAN.md section 6.6. ``TrayApp`` is driven with a stub API and a stub
scheduler, and its worker threads are made synchronous so a click's effect is
observable without sleeping. Nothing here starts a process or touches the
registry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import pytest

from usage_monitor_manager import __version__
from usage_monitor_manager.model import JsonDict
from usage_monitor_manager.scheduler import UpdateState
from usage_monitor_manager.ui import tray

CLAUDE_ROW: JsonDict = {
    "id": "claude",
    "kind": "claude",
    "kind_display": "Claude",
    "label": "Claude",
    "config_dir": r"C:\Users\Marco\.claude",
    "local_version": "1.22.0",
    "is_running": True,
    "exe_path": r"C:\Tools\UsageMonitorForClaude.exe",
    "update_available": False,
    "processes": {"bootloader_pid": 1, "app_pid": 2},
}
VALERIA_ROW: JsonDict = {
    **CLAUDE_ROW,
    "id": "claude_578560bbc610",
    "label": "Claude Valeria",
    "config_dir": r"C:\Users\Marco\.claude-valeria",
}
COPILOT_ROW: JsonDict = {
    "id": "copilot",
    "kind": "copilot",
    "kind_display": "Copilot",
    "label": None,
    "config_dir": r"C:\Users\Marco\.copilot",
    "local_version": "0.3.1",
    "is_running": True,
    "exe_path": r"C:\Tools\UsageMonitorForCopilot.exe",
    "update_available": True,
    "remote_version": "0.3.2",
}
CODEX_ROW: JsonDict = {
    "id": "codex",
    "kind": "codex",
    "kind_display": "Codex",
    "label": None,
    "config_dir": r"C:\Users\Marco\.codex",
    "local_version": "0.1.0",
    "is_running": False,
    "exe_path": r"C:\Tools\UsageMonitorForCodex.exe",
    "update_available": False,
}
ALL_ROWS = [CLAUDE_ROW, VALERIA_ROW, COPILOT_ROW, CODEX_ROW]


@pytest.fixture(autouse=True)
def fake_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """``short_path`` must produce ``~/...`` regardless of who runs the suite."""
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\Users\Marco")))


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def test_short_path_collapses_the_home_directory() -> None:
    assert tray.short_path(r"C:\Users\Marco\.claude-valeria") == "~/.claude-valeria"
    assert tray.short_path(r"C:\Users\Marco") == "~"
    assert tray.short_path(r"D:\elsewhere\.codex") == r"D:\elsewhere\.codex"


def test_menu_reproduces_the_plan() -> None:
    last_check = "2026-09-18T12:40:00+00:00"
    expected_time = f"{datetime.fromisoformat(last_check).astimezone():%H:%M}"
    state = {"last_check": last_check, "checking": False}
    labels = tray.menu_labels(tray.build_menu_spec(ALL_ROWS, state, version="1.0.0"))

    assert labels[0].startswith("Claude \u00b7 ~/.claude  1.22.0  \u25cf in esecuzione")
    assert labels[1:7] == [
        "  Riavvia",
        "  Ferma",
        "  Avvia [disabled]",
        "  ---",
        "  Impostazioni\u2026",
        "  Apri cartella",
    ]
    assert "Claude Valeria \u00b7 ~/.claude-valeria" in labels[7]
    copilot_line = next(line for line in labels if line.startswith("Copilot"))
    assert copilot_line.endswith("\u2191 0.3.2 disponibile")
    assert "  Aggiorna a 0.3.2\u2026" in labels
    codex_line = next(line for line in labels if line.startswith("Codex"))
    assert "\u25cb non in esecuzione" in codex_line

    tail = labels[labels.index("Riavvia tutte") :]
    assert tail == [
        "Riavvia tutte",
        "Ferma tutte",
        "Avvia mancanti",
        "---",
        f"Controlla aggiornamenti (ultimo: {expected_time})",
        "Apri Manager\u2026",
        "---",
        "Manager 1.0.0 [disabled]",
        "Esci",
    ]


def test_version_defaults_to_the_package_version() -> None:
    labels = tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW]))
    assert f"Manager {__version__} [disabled]" in labels


def test_update_entry_only_appears_for_an_outdated_executable() -> None:
    labels = tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW]))
    assert not any("Aggiorna" in line for line in labels)


def test_bulk_entries_follow_the_run_state() -> None:
    running_only = tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW]))
    assert "Riavvia tutte" in running_only
    assert "Avvia mancanti [disabled]" in running_only

    stopped_only = tray.menu_labels(tray.build_menu_spec([CODEX_ROW]))
    assert "Riavvia tutte [disabled]" in stopped_only
    assert "Ferma tutte [disabled]" in stopped_only
    assert "Avvia mancanti" in stopped_only


def test_empty_machine_has_a_placeholder() -> None:
    labels = tray.menu_labels(tray.build_menu_spec([]))
    assert labels[0] == "Nessuna istanza trovata [disabled]"
    assert "Esci" in labels


def test_never_checked_and_checking_states() -> None:
    assert "Controlla aggiornamenti (mai)" in tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW], {}))
    busy = tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW], {"checking": True, "last_check": None}))
    assert "Controlla aggiornamenti (mai) [disabled]" in busy
    bad = tray.menu_labels(tray.build_menu_spec([CLAUDE_ROW], {"last_check": "not-a-date"}))
    assert "Controlla aggiornamenti (mai)" in bad


def test_instance_without_an_executable_cannot_be_started() -> None:
    row = {**CODEX_ROW, "exe_path": None}
    labels = tray.menu_labels(tray.build_menu_spec([row]))
    assert "  Avvia [disabled]" in labels
    assert "Avvia mancanti [disabled]" in labels


def test_open_manager_is_the_default_entry() -> None:
    spec = tray.build_menu_spec(ALL_ROWS)
    default = [entry for entry in spec if entry.default]
    assert [entry.action for entry in default] == ["open_window"]


def test_actions_carry_the_ids_and_paths() -> None:
    spec = tray.build_menu_spec([COPILOT_ROW])
    submenu = {entry.action: entry.args for entry in spec[0].submenu if entry.action}
    assert submenu["restart"] == ("copilot",)
    assert submenu["open_folder"] == (r"C:\Users\Marco\.copilot",)
    assert submenu["update"] == (r"C:\Tools\UsageMonitorForCopilot.exe",)


# --------------------------------------------------------------------------- #
# Badge and icon
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("count", "error", "expected"),
    [
        (0, False, None),
        (-1, False, None),
        (1, False, "1"),
        (9, False, "9"),
        (10, False, "9+"),
        (12, False, "9+"),
        (0, True, "!"),
        (3, True, "!"),
    ],
)
def test_badge_text(count: int, error: bool, expected: str | None) -> None:
    assert tray.badge_text(count, error) == expected


def test_icon_is_drawn_at_the_requested_size() -> None:
    plain = tray.make_image(0, False, size=32)
    assert plain.size == (32, 32)
    assert plain.mode == "RGBA"
    badged = tray.make_image(2, False, size=32)
    assert badged.size == (32, 32)
    assert plain.tobytes() != badged.tobytes()  # the badge really is drawn
    assert tray.make_image(0, True, size=32).tobytes() != badged.tobytes()


def test_glyph_alone_has_no_badge() -> None:
    assert tray.draw_glyph(16).size == (16, 16)


# --------------------------------------------------------------------------- #
# TrayApp
# --------------------------------------------------------------------------- #


class StubApi:
    """Records what the tray asked the core to do."""

    def __init__(self, rows: Sequence[JsonDict]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def list_instances(self, refresh: bool = True) -> list[JsonDict]:
        self.calls.append(("list_instances", (refresh,)))
        return list(self._rows)

    def _record(self, name: str) -> Callable[..., list[JsonDict]]:
        def method(*args: object) -> list[JsonDict]:
            self.calls.append((name, args))
            return []

        return method

    def __getattr__(self, name: str) -> Callable[..., list[JsonDict]]:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._record(name)


class StubScheduler:
    def __init__(self) -> None:
        self.state = UpdateState()
        self.stopped = False
        self.checks = 0

    def check_now(self) -> UpdateState:
        self.checks += 1
        return self.state

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> tray.TrayApp:
    """A TrayApp whose "worker threads" run inline, so clicks are observable."""
    api = StubApi(ALL_ROWS)
    scheduler = StubScheduler()
    instance = tray.TrayApp(api=api, scheduler=scheduler, owns_scheduler=True)  # pyright: ignore[reportArgumentType]
    monkeypatch.setattr(
        tray.TrayApp,
        "_in_worker",
        lambda self, name, work: work(),  # pyright: ignore[reportUnknownLambdaType]
    )
    return instance


def test_spec_is_built_from_the_api_rows(app: tray.TrayApp) -> None:
    labels = tray.menu_labels(app.spec())
    assert any(line.startswith("Claude \u00b7 ~/.claude") for line in labels)
    assert "Esci" in labels


def test_rows_survive_a_failing_discovery(monkeypatch: pytest.MonkeyPatch, app: tray.TrayApp) -> None:
    app.rows()

    def boom(refresh: bool = True) -> list[JsonDict]:
        raise RuntimeError("psutil esplode")

    monkeypatch.setattr(app.api, "list_instances", boom)
    assert len(app.rows()) == len(ALL_ROWS)  # the previous scan is kept
    assert tray.menu_labels(app.spec())[-1] == "Esci"


@pytest.mark.parametrize(
    ("action", "args", "expected"),
    [
        ("restart", ("claude",), ("restart", (["claude"],))),
        ("stop", ("copilot",), ("stop", (["copilot"],))),
        ("start", ("codex",), ("start", (["codex"],))),
        ("restart_all", (), ("restart_all", ())),
        ("stop_all", (), ("stop_all", ())),
        ("start_missing", (), ("start_missing", ())),
        ("open_folder", (r"C:\x",), ("open_folder", (r"C:\x",))),
    ],
)
def test_dispatch_forwards_to_the_api(
    app: tray.TrayApp,
    action: str,
    args: tuple[object, ...],
    expected: tuple[str, tuple[object, ...]],
) -> None:
    app.dispatch(action, args)
    api: StubApi = app.api  # pyright: ignore[reportAssignmentType]
    assert expected in api.calls


def test_dispatch_check_runs_a_check(app: tray.TrayApp) -> None:
    app.dispatch("check")
    api: StubApi = app.api  # pyright: ignore[reportAssignmentType]
    assert ("check_updates", ()) in api.calls


def test_dispatch_opens_the_window_for_settings_and_updates(
    app: tray.TrayApp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        tray.TrayApp,
        "open_window",
        lambda self, page="dashboard", **params: opened.append((page, params)),  # pyright: ignore[reportUnknownLambdaType]
    )
    app.dispatch("open_window")
    app.dispatch("settings", ("claude",))
    app.dispatch("update", (r"C:\Tools\UsageMonitorForCopilot.exe",))
    assert opened == [
        ("dashboard", {}),
        ("settings", {"instance_id": "claude"}),
        ("updates", {"exe_path": r"C:\Tools\UsageMonitorForCopilot.exe"}),
    ]


def test_unknown_action_is_ignored(app: tray.TrayApp) -> None:
    app.dispatch("teleport", ("somewhere",))
    api: StubApi = app.api  # pyright: ignore[reportAssignmentType]
    assert all(name != "teleport" for name, _ in api.calls)


def test_tooltip_summarises_the_state(app: tray.TrayApp) -> None:
    app.rows()
    assert "3/4 in esecuzione" in app.tooltip()

    app.scheduler.state = UpdateState(available=(object(),))  # pyright: ignore[reportAttributeAccessIssue, reportArgumentType]
    assert "1 aggiornamenti disponibili" in app.tooltip()

    app.scheduler.state = UpdateState(error="boom")  # pyright: ignore[reportAttributeAccessIssue]
    assert "ultimo controllo non riuscito" in app.tooltip()


def test_notify_is_a_no_op_without_an_icon(app: tray.TrayApp) -> None:
    app.notify("niente icona")  # must not raise
    app.refresh_icon()


def test_on_updates_available_notifies_once_per_plan(app: tray.TrayApp) -> None:
    sent: list[tuple[str, str]] = []

    class FakeIcon:
        def notify(self, message: str, title: str) -> None:
            sent.append((title, message))

        def update_menu(self) -> None:
            return

        icon: Any = None
        title: str = ""

    app._icon = FakeIcon()  # noqa: SLF001 - the icon is a private detail on purpose

    class FakeTarget:
        version = "0.3.2"
        tag = "v0.3.2"

    class FakeKind:
        display_name = "Copilot"

    class FakePlan:
        kind = FakeKind()
        target = FakeTarget()
        current_version = "0.3.1"

    app.on_updates_available([FakePlan()])
    assert sent == [("Aggiornamento disponibile", "Copilot 0.3.2 disponibile (hai la 0.3.1)")]


def test_quit_stops_the_scheduler_and_the_windows(app: tray.TrayApp, monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    monkeypatch.setattr("usage_monitor_manager.ui.window.destroy_windows", lambda: destroyed.append(True))
    app.quit()
    app.quit()  # idempotent
    scheduler: StubScheduler = app.scheduler  # pyright: ignore[reportAssignmentType]
    assert scheduler.stopped is True
    assert destroyed == [True]


# --------------------------------------------------------------------------- #
# pystray conversion (menus can be built without running an icon)
# --------------------------------------------------------------------------- #


def test_menu_converts_to_pystray_items(app: tray.TrayApp) -> None:
    import pystray

    items = list(app.menu_items())
    assert items
    texts = [str(item.text) for item in items if item is not pystray.Menu.SEPARATOR]
    assert any(text.startswith("Claude \u00b7 ~/.claude") for text in texts)
    assert "Esci" in texts

    menu = pystray.Menu(app.menu_items)
    rendered = str(menu)
    assert "Esci" in rendered
    assert "Apri Manager" in rendered


def test_disabled_items_are_disabled_in_pystray(app: tray.TrayApp) -> None:
    import pystray

    items = [i for i in app.menu_items() if i is not pystray.Menu.SEPARATOR]
    by_text = {str(item.text): item for item in items}
    assert by_text[f"Manager {__version__}"].enabled is False


def test_clicking_a_pystray_item_dispatches(app: tray.TrayApp, monkeypatch: pytest.MonkeyPatch) -> None:
    import pystray

    seen: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        tray.TrayApp,
        "dispatch",
        lambda self, action, args=(): seen.append((action, args)),  # pyright: ignore[reportUnknownLambdaType]
    )
    items = [i for i in app.menu_items() if i is not pystray.Menu.SEPARATOR]
    restart_all = next(item for item in items if str(item.text) == "Riavvia tutte")
    restart_all(None)
    assert seen == [("restart_all", ())]
