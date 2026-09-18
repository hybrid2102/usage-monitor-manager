"""pystray tray icon: dynamic menu, quick actions, notifications.

The menu of PLAN.md section 6.6, rebuilt every time it opens: ``pystray.Menu``
accepts a single callable returning a generator of items and re-evaluates it on
every display, which is exactly the "dynamic menu" the plan asks for. Nothing
in the menu blocks: every action that can take more than a moment (restart,
update, check) is handed to a daemon worker thread, so the shell never sees a
hung menu.

The icon is drawn with Pillow (no ``.ico`` needed at runtime) and carries a
badge: nothing when everything is current, the number of available updates, or
a red dot when the last check failed. Notifications go through
``Icon.notify``, which on Windows is a balloon/toast - no extra dependency.

Threading: pystray runs here in a background thread and pywebview owns the main
thread; :mod:`usage_monitor_manager.ui.window` explains why in detail.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .. import __version__, diag, errors, i18n, legacy, manager_config, platform
from ..i18n import T, t
from ..model import JsonDict
from ..scheduler import UpdateScheduler
from ..watchdog import Watchdog
from .window import JsApi

_log = diag.get_logger("tray")

__all__ = [
    "ICON_SIZE",
    "MUTEX_NAME",
    "MenuEntry",
    "TrayApp",
    "badge_text",
    "build_menu_spec",
    "draw_glyph",
    "make_image",
    "menu_labels",
    "acquire_mutex",
    "run_tray",
    "short_path",
]

MUTEX_NAME = "UsageMonitorManager_SingleInstance"
"""The manager's own singleton (PLAN.md section 6.5)."""

ICON_SIZE = 64

COLOR_BG = (30, 30, 30, 255)
"""``#1e1e1e``: the monitors' popup background."""
COLOR_FG = (204, 204, 204, 255)
"""``#cccccc``."""
COLOR_BAR = (74, 158, 255, 255)
"""``#4a9eff``: the monitors' bar colour."""
COLOR_BADGE = (217, 119, 87, 255)
"""Claude's brand orange, used for the update badge."""
COLOR_ERROR = (224, 80, 80, 255)

RUNNING_MARK = "\u25cf"
STOPPED_MARK = "\u25cb"


# --------------------------------------------------------------------------- #
# Icon
# --------------------------------------------------------------------------- #


def draw_glyph(size: int = ICON_SIZE) -> Any:
    """The manager's glyph: three stacked usage bars on a dark rounded square.

    Shared with ``tools/generate_icon.py`` so the ``.ico`` in the build and the
    image in the tray are the same drawing.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(2, size // 8)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=COLOR_BG)

    margin = max(2, size // 8)
    bar_height = max(2, size // 9)
    gap = max(1, size // 12)
    top = margin + gap
    widths = (0.85, 0.6, 0.35)
    usable = size - 2 * margin
    for index, fraction in enumerate(widths):
        y0 = top + index * (bar_height + gap)
        y1 = y0 + bar_height
        draw.rounded_rectangle(
            (margin, y0, margin + int(usable * fraction), y1),
            radius=bar_height // 2,
            fill=COLOR_BAR if index == 0 else COLOR_FG,
        )
    return image


def badge_text(update_count: int, error: bool) -> str | None:
    """What the badge should read: ``None``, ``"3"``, ``"9+"`` or ``"!"``."""
    if error:
        return "!"
    if update_count <= 0:
        return None
    return str(update_count) if update_count < 10 else "9+"


def make_image(update_count: int = 0, error: bool = False, size: int = ICON_SIZE) -> Any:
    """The glyph plus the badge overlay for the current state."""
    from PIL import ImageDraw, ImageFont

    image = draw_glyph(size)
    text = badge_text(update_count, error)
    if text is None:
        return image

    draw = ImageDraw.Draw(image)
    diameter = int(size * 0.55)
    x0 = size - diameter
    y0 = size - diameter
    draw.ellipse((x0, y0, size - 1, size - 1), fill=COLOR_ERROR if error else COLOR_BADGE)
    try:
        font = ImageFont.truetype("segoeuib.ttf", int(diameter * 0.66))
    except OSError:  # pragma: no cover - a Windows without Segoe UI Bold
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (x0 + (diameter - (box[2] - box[0])) / 2 - box[0], y0 + (diameter - (box[3] - box[1])) / 2 - box[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return image


# --------------------------------------------------------------------------- #
# Menu specification (pure data: this is what the tests assert on)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """One menu line, before pystray is involved.

    Keeping the menu a plain tree means the labels of PLAN.md section 6.6 can
    be asserted from a list of row dictionaries, with no icon running and no
    pystray import.
    """

    text: str = ""
    action: str | None = None
    args: tuple[object, ...] = ()
    submenu: tuple[MenuEntry, ...] = ()
    enabled: bool = True
    separator: bool = False
    default: bool = False
    extra: JsonDict = field(default_factory=lambda: {})


SEPARATOR = MenuEntry(separator=True)


def short_path(path: str | Path) -> str:
    """``C:\\Users\\Marco\\.claude`` as ``~/.claude``, for a menu-sized label."""
    text = str(path)
    try:
        home = str(Path.home())
    except (OSError, RuntimeError):  # pragma: no cover - no home directory
        return text
    if os.path.normcase(text).startswith(os.path.normcase(home)):
        tail = text[len(home) :].replace("\\", "/").lstrip("/")
        return f"~/{tail}" if tail else "~"
    return text


def _instance_text(row: JsonDict) -> str:
    """``Claude Valeria - ~/.claude-valeria  1.22.0  * in esecuzione``."""
    label = row.get("label") or row.get("kind_display") or row.get("kind") or "?"
    version = row.get("local_version") or "?"
    running = bool(row.get("is_running"))
    mark = RUNNING_MARK if running else STOPPED_MARK
    state = T["state.running"] if running else T["state.stopped"]
    text = f"{label} \u00b7 {short_path(str(row.get('config_dir', '')))}  {version}  {mark} {state}"
    if row.get("update_available"):
        text += "  " + t("tray.instance_update_suffix", version=row.get("remote_version") or "?")
    return text


def _instance_submenu(row: JsonDict) -> tuple[MenuEntry, ...]:
    instance_id = str(row.get("id", ""))
    running = bool(row.get("is_running"))
    has_exe = bool(row.get("exe_path"))
    entries: list[MenuEntry] = [
        MenuEntry(T["tray.restart"], "restart", (instance_id,), enabled=running),
        MenuEntry(T["tray.stop"], "stop", (instance_id,), enabled=running),
        MenuEntry(T["tray.start"], "start", (instance_id,), enabled=not running and has_exe),
        SEPARATOR,
        MenuEntry(T["tray.settings"], "settings", (instance_id,)),
        MenuEntry(T["tray.open_folder"], "open_folder", (str(row.get("config_dir", "")),)),
    ]
    if row.get("update_available"):
        entries.extend(
            [
                SEPARATOR,
                MenuEntry(
                    t("tray.update_to", version=row.get("remote_version") or "?"),
                    "update",
                    (str(row.get("exe_path") or ""),),
                    enabled=has_exe,
                ),
                MenuEntry(T["tray.skip_version"], "skip_version", (str(row.get("kind") or ""),)),
            ]
        )
    if row.get("watchdog"):
        entries.extend(
            [
                SEPARATOR,
                MenuEntry(t("tray.repair", finding=row.get("watchdog")), "repair", (instance_id,)),
            ]
        )
    return tuple(entries)


def _self_update_state(state: JsonDict) -> JsonDict:
    """The ``self_update`` sub-document of an update state, or an empty one."""
    own = state.get("self_update")
    if not isinstance(own, dict):
        return {}
    return {str(key): value for key, value in cast("dict[object, object]", own).items()}


def _manager_text(state: JsonDict, version: str) -> str:
    """``Manager 0.7.0 (aggiornato)`` / ``(0.8.0 disponibile)`` / ``(nessuna release)``."""
    own = _self_update_state(state)
    if not own:
        return t("tray.manager", version=version)
    if own.get("available"):
        return t("tray.manager_available", version=version, target=own.get("target_version") or "?")
    status = str(own.get("status") or "")
    if status == "unknown":
        return t("tray.manager_no_release", version=version)
    if own.get("skipped"):
        return t("tray.manager_skipped", version=version, target=own.get("target_version") or "?")
    return t("tray.manager_up_to_date", version=version)


def _last_check_text(last_check: str | None) -> str:
    if not last_check:
        return T["tray.check_never"]
    try:
        stamp = datetime.fromisoformat(last_check).astimezone()
    except ValueError:
        return T["tray.check_never"]
    return t("tray.check_last", time=f"{stamp:%H:%M}")


def _profile_submenu(profiles: Sequence[JsonDict]) -> tuple[MenuEntry, ...]:
    """The "Profili" submenu of PLAN.md section 9.13, one entry per profile."""
    if not profiles:
        return (MenuEntry(T["tray.no_profile"], None, enabled=False),)
    entries: list[MenuEntry] = []
    for profile in profiles:
        name = str(profile.get("name", ""))
        members = profile.get("instance_ids")
        count = len(cast("list[object]", members)) if isinstance(members, list) else 0
        entries.append(MenuEntry(t("tray.profile_entry", name=name, count=count), "apply_profile", (name,)))
    return tuple(entries)


def build_menu_spec(
    rows: Sequence[JsonDict],
    update_state: JsonDict | None = None,
    version: str = __version__,
    profiles: Sequence[JsonDict] = (),
) -> tuple[MenuEntry, ...]:
    """The whole menu of PLAN.md section 6.6 as a tree of :class:`MenuEntry`.

    ``rows`` are the dictionaries :meth:`JsApi.list_instances` returns,
    ``profiles`` the ones :meth:`JsApi.list_profiles` returns.
    """
    state = update_state or {}
    entries: list[MenuEntry] = []
    if not rows:
        entries.append(MenuEntry(T["tray.no_instances"], None, enabled=False))
    for row in rows:
        entries.append(
            MenuEntry(_instance_text(row), None, submenu=_instance_submenu(row), extra={"id": row.get("id")})
        )

    any_running = any(row.get("is_running") for row in rows)
    any_stopped = any(not row.get("is_running") and row.get("exe_path") for row in rows)
    entries.extend(
        [
            SEPARATOR,
            MenuEntry(T["tray.restart_all"], "restart_all", enabled=any_running),
            MenuEntry(T["tray.stop_all"], "stop_all", enabled=any_running),
            MenuEntry(T["tray.start_missing"], "start_missing", enabled=any_stopped),
            MenuEntry(T["tray.profiles"], None, submenu=_profile_submenu(profiles)),
            SEPARATOR,
            MenuEntry(
                _last_check_text(state.get("last_check")),
                "check",
                enabled=not bool(state.get("checking")),
            ),
            MenuEntry(T["tray.open_manager"], "open_window", default=True),
            SEPARATOR,
            MenuEntry(_manager_text(state, version), None, enabled=False),
        ]
    )
    own = _self_update_state(state)
    if own.get("available"):
        entries.extend(
            [
                MenuEntry(
                    t("tray.update_manager_to", version=own.get("target_version") or "?"),
                    "self_update",
                ),
                MenuEntry(T["tray.skip_version"], "skip_version", ("manager",)),
            ]
        )
    entries.extend(
        [
            MenuEntry(T["tray.quit"], "quit"),
        ]
    )
    return tuple(entries)


def menu_labels(entries: Sequence[MenuEntry], *, indent: int = 0) -> list[str]:
    """Flatten a spec to its visible labels, submenus indented (for tests)."""
    out: list[str] = []
    for entry in entries:
        if entry.separator:
            out.append("  " * indent + "---")
            continue
        out.append("  " * indent + entry.text + ("" if entry.enabled else " [disabled]"))
        out.extend(menu_labels(entry.submenu, indent=indent + 1))
    return out


# --------------------------------------------------------------------------- #
# The application
# --------------------------------------------------------------------------- #


class TrayApp:
    """Glues the icon, the scheduler, the window and the core API together."""

    def __init__(
        self,
        api: JsApi | None = None,
        scheduler: UpdateScheduler | None = None,
        *,
        config_path: Path | None = None,
        owns_scheduler: bool | None = None,
        watchdog: Watchdog | None = None,
    ) -> None:
        """``owns_scheduler`` decides whether :meth:`quit` stops the scheduler.

        Default: a scheduler we built ourselves is ours to stop, an injected
        one belongs to the caller - except when the caller says otherwise,
        which is what :func:`run_tray` does (it builds the scheduler only so it
        can wire the notification callbacks back to this app).
        """
        self._config_path = config_path
        self._scheduler = scheduler if scheduler is not None else UpdateScheduler(config_path=config_path)
        self._scheduler_owned = (scheduler is None) if owns_scheduler is None else owns_scheduler
        self._api = api if api is not None else JsApi(scheduler=self._scheduler, config_path=config_path)
        self._icon: Any | None = None
        self._rows: list[JsonDict] = []
        self._quitting = threading.Event()
        self._watchdog = watchdog if watchdog is not None else Watchdog(config_path=config_path)
        self._watchdog_owned = watchdog is None
        self._wire_watchdog()
        self._api.set_watchdog(self._watchdog)
        self._api.set_quit_callback(self.quit)

    # -- data ----------------------------------------------------------------

    @property
    def api(self) -> JsApi:
        return self._api

    @property
    def scheduler(self) -> UpdateScheduler:
        return self._scheduler

    @property
    def watchdog(self) -> Watchdog:
        return self._watchdog

    def _wire_watchdog(self) -> None:
        """Route the watchdog findings to a notification and the badge."""
        self._watchdog.set_event_callback(self.on_watchdog_events)

    def on_watchdog_events(self, events: Sequence[Any]) -> None:
        """One toast per anomaly (PLAN.md section 9.2), then redraw the icon."""
        self.write_status_file()
        for event in events:
            _log.info("watchdog %s: %s", event.type, event.detail)
            if event.type == "crashed":
                key = "watchdog.crashed_restarted" if event.repaired else "watchdog.crashed"
            elif event.type == "duplicate":
                key = "watchdog.duplicate"
            else:
                key = "watchdog.orphan"
            self.notify(t(key, instance=event.instance_id), T["watchdog.title"])
        self.refresh_icon()

    def rows(self, *, refresh: bool = True) -> list[JsonDict]:
        """The instance rows the menu is built from, watchdog status included."""
        try:
            self._rows = self._api.list_instances(refresh=refresh)
        except Exception:  # noqa: BLE001 - a failed scan must not kill the menu
            pass
        findings = self._watchdog.state
        for row in self._rows:
            row["watchdog"] = findings.status_of(str(row.get("id", "")))
        self.write_status_file()
        return self._rows

    def write_status_file(self) -> Path | None:
        """Rewrite ``status.json`` when the user asked for one (section 9.11).

        Called after every discovery, watchdog and scheduler cycle. A failure
        is logged and swallowed: a status file nobody can write is not a reason
        to stop managing instances.
        """
        path = manager_config.load(self._config_path).preferences.status_file
        if not path:
            return None
        from ..status_file import build_status, write_status_file

        try:
            return write_status_file(build_status(check=False), path)
        except Exception as error:  # noqa: BLE001 - never load-bearing
            _log.warning("status file not written: %s", error)
            return None

    def spec(self) -> tuple[MenuEntry, ...]:
        return build_menu_spec(self.rows(), self._scheduler.state.to_dict(), profiles=self.profiles())

    def profiles(self) -> list[JsonDict]:
        """The launch profiles, for the "Profili" submenu; never raises."""
        try:
            return self._api.list_profiles()
        except Exception:  # noqa: BLE001 - a broken config must not kill the menu
            return []

    # -- pystray glue --------------------------------------------------------

    def _to_pystray(self, entries: Sequence[MenuEntry]) -> list[Any]:
        import pystray  # pyright: ignore[reportMissingTypeStubs]

        items: list[Any] = []
        for entry in entries:
            if entry.separator:
                items.append(pystray.Menu.SEPARATOR)
                continue
            if entry.submenu:
                items.append(pystray.MenuItem(entry.text, pystray.Menu(*self._to_pystray(entry.submenu))))
                continue
            action = None if entry.action is None else self._bind(entry.action, entry.args)
            items.append(
                pystray.MenuItem(
                    entry.text,
                    action,
                    enabled=entry.enabled and action is not None,
                    default=entry.default,
                )
            )
        return items

    def _bind(self, action: str, args: tuple[object, ...]) -> Callable[..., None]:
        def invoke(*_ignored: object) -> None:
            self.dispatch(action, args)

        return invoke

    def menu_items(self) -> Iterator[Any]:
        """Generator handed to ``pystray.Menu``: re-run on every menu open."""
        yield from self._to_pystray(self.spec())

    # -- actions -------------------------------------------------------------

    def _in_worker(self, name: str, work: Callable[[], object]) -> None:
        """Run a long operation off the menu thread and refresh afterwards."""

        def target() -> None:
            try:
                work()
            except errors.UmmError as error:
                _log.warning("%s failed: %s", name, error.message)
                self.notify(t("tray.action_failed", action=name, error=error.message), T["app.name"])
            except Exception as error:  # noqa: BLE001 - logged with its traceback, shown as one line
                self.notify(errors.report_unexpected(error, context="tray"), T["app.name"])
            finally:
                self.refresh_icon()

        threading.Thread(target=target, name=f"umm-tray-{name}", daemon=True).start()

    def dispatch(self, action: str, args: tuple[object, ...] = ()) -> None:
        """Route one menu click. Only ``quit`` runs on the calling thread."""
        api = self._api
        if action == "quit":
            self.quit()
            return
        if action == "open_window":
            self.open_window("dashboard")
            return
        if action == "settings":
            self.open_window("settings", instance_id=str(args[0]) if args else None)
            return
        if action == "update":
            self.open_window("updates", exe_path=str(args[0]) if args else None)
            return
        if action == "open_folder":
            self._in_worker("apri cartella", lambda: api.open_folder(str(args[0])))
            return
        if action in ("restart", "stop", "start"):
            ids = [str(args[0])]
            method = getattr(api, action)
            self._in_worker(action, lambda: method(ids))
            return
        if action in ("restart_all", "stop_all", "start_missing"):
            method = getattr(api, action)
            self._in_worker(action, method)
            return
        if action == "check":
            self._in_worker("controllo aggiornamenti", api.check_updates)
            return
        if action == "self_update":
            self.open_window("manager")
            return
        if action == "skip_version":
            key = str(args[0]) if args else ""
            self._in_worker("salta versione", lambda: api.skip_version(key))
            return
        if action == "repair":
            instance_id = str(args[0]) if args else ""
            self._in_worker("ripara", lambda: api.repair_instance(instance_id))
            return
        if action == "apply_profile":
            name = str(args[0]) if args else ""
            _log.info("tray: applying profile %s", name)
            self._in_worker(T["tray.action_profile"], lambda: api.apply_profile(name))
            return

    def open_window(self, page: str = "dashboard", **params: object) -> None:
        """Show the manager window; the window itself lives on the main thread."""
        from .window import open_window

        def work() -> None:
            focus = params.get("instance_id") or params.get("exe_path")
            window = open_window(page, self._api, str(focus) if focus else None)
            if focus:
                window.show(page, str(focus))

        self._in_worker(T["tray.action_open_manager"], work)

    # -- icon state ----------------------------------------------------------

    def notify(self, message: str, title: str | None = None) -> None:
        """A Windows balloon through pystray; silently ignored when unavailable."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title if title is not None else T["app.name"])
        except Exception:  # noqa: BLE001 - notifications are never load-bearing
            return

    def toggle_window(self) -> None:
        """What the global hotkey does: show the window, or hide it if shown.

        Runs on the hotkey thread, so the actual window work goes to the
        worker like every other menu action.
        """
        from .window import toggle_window  # noqa: PLC0415

        def work() -> None:
            toggle_window(self._api)

        _log.info("hotkey: toggling the manager window")
        self._in_worker(T["tray.action_hotkey"], work)

    def refresh_icon(self) -> None:
        """Redraw the badge and let the shell know the menu changed."""
        icon = self._icon
        if icon is None:
            return
        state = self._scheduler.state
        anomalies = self._watchdog.state.count
        try:
            icon.icon = make_image(state.update_count, state.error is not None or anomalies > 0)
            icon.title = self.tooltip()
            icon.update_menu()
        except Exception:  # noqa: BLE001 - a shell hiccup is not fatal
            return

    def tooltip(self) -> str:
        state = self._scheduler.state
        running = sum(1 for row in self._rows if row.get("is_running"))
        parts = [
            f"{T['app.name']} {__version__}",
            t("tray.tooltip_running", running=running, total=len(self._rows)),
        ]
        if state.error is not None:
            parts.append(T["tray.tooltip_check_failed"])
        elif state.update_count:
            parts.append(t("tray.tooltip_updates", count=state.update_count))
        anomalies = self._watchdog.state.events
        if anomalies:
            kinds = ", ".join(sorted({event.type for event in anomalies}))
            parts.append(t("tray.tooltip_anomalies", count=len(anomalies), kinds=kinds))
        return " \u2014 ".join(parts)

    def on_updates_available(self, plans: Sequence[Any]) -> None:
        """Scheduler callback: one toast per newly published release."""
        self.write_status_file()
        for plan in plans:
            _log.info("update available: %s %s", plan.kind.display_name, plan.target.version)
            self.notify(
                t(
                    "notify.update_body",
                    name=plan.kind.display_name,
                    version=plan.target.version,
                    current=plan.current_version or "?",
                ),
                T["notify.update_title"],
            )
        self.refresh_icon()

    def on_self_update_available(self, check: Any) -> None:
        """Scheduler callback: the manager itself has a newer release."""
        self.notify(
            t("notify.manager_update_body", version=check.target_version, current=check.current_version),
            T["notify.manager_update_title"],
        )
        self.refresh_icon()

    # -- lifecycle -----------------------------------------------------------

    def build_icon(self) -> Any:
        import pystray  # pyright: ignore[reportMissingTypeStubs]

        icon = pystray.Icon(
            "usage-monitor-manager",
            icon=make_image(),
            title=f"{T['app.name']} {__version__}",
            menu=pystray.Menu(self.menu_items),
        )
        self._icon = icon
        return icon

    def quit(self) -> None:
        """Stop the scheduler, the icon and the webview loop, in that order."""
        if self._quitting.is_set():
            return
        self._quitting.set()
        if self._scheduler_owned:
            self._scheduler.stop(timeout=2.0)
        if self._watchdog_owned:
            self._watchdog.stop(timeout=2.0)
        icon = self._icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
        from .window import destroy_windows

        destroy_windows()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _startup_housekeeping(config_path: Path | None) -> list[str]:
    """Autostart path sync and the one-shot legacy import; returns messages."""
    messages: list[str] = []
    config = manager_config.load(config_path)
    if config.preferences.autostart_manager:
        try:
            platform.sync_manager_autostart_path()
        except OSError as error:  # pragma: no cover - registry refusal
            messages.append(t("app.autostart_not_synced", error=error))
    report = legacy.run_once(config_path)
    if report is not None and report.added:
        names = ", ".join(entry.label or entry.kind for entry in report.added)
        messages.append(t("app.legacy_imported", count=len(report.added), names=names))
    return messages


def acquire_mutex(timeout: float = 0.0, *, poll: float = 0.5) -> platform.MutexHolder | None:
    """Take the single-instance mutex, waiting up to ``timeout`` seconds.

    The wait exists for exactly one caller: the process started with
    ``--post-update``, which is racing the manager it just replaced. Everybody
    else passes ``timeout=0`` and fails immediately, which is the singleton
    behaviour of PLAN.md section 6.5.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        holder = platform.hold_mutex(MUTEX_NAME)
        if holder is not None or time.monotonic() >= deadline:
            return holder
        time.sleep(poll)


def start_hotkey(app: TrayApp, config_path: Path | None = None) -> Any | None:
    """Register ``preferences.hotkey``, if any; returns the live listener.

    A refused combination (another application already owns it) is surfaced as
    a notification and logged - the manager keeps running without it, which is
    the only sane outcome for a convenience binding.
    """
    combo = manager_config.load(config_path).preferences.hotkey
    if not combo:
        return None
    try:
        listener = platform.HotkeyListener(combo, app.toggle_window)
    except platform.HotkeyError as error:
        _log.warning("hotkey %s not registered: %s", combo, error)
        app.notify(t("hotkey.invalid", error=error))
        return None
    if not listener.start():
        _log.warning("hotkey %s not registered: %s", combo, listener.error)
        app.notify(t("hotkey.not_registered", combo=combo, error=listener.error))
        return None
    _log.info("hotkey %s registered", combo)
    return listener


def run_tray(
    config_path: Path | None = None,
    *,
    wait_for_mutex: float = 0.0,
    messages: Sequence[str] = (),
) -> int:
    """Run the tray icon (and the window loop) until the user quits.

    Returns 0 on a clean exit, 1 when another manager already holds the
    single-instance mutex. ``messages`` are shown as notifications once the
    icon exists, which is how ``--post-update`` says "Manager aggiornato alla
    vX.Y.Z".
    """
    i18n.set_language(manager_config.load(config_path).preferences.language)
    holder = acquire_mutex(wait_for_mutex)
    if holder is None:
        print(T["app.already_running"])
        return 1

    notes = [*messages, *_startup_housekeeping(config_path)]

    app: TrayApp | None = None
    scheduler = UpdateScheduler(
        config_path=config_path,
        on_updates_available=lambda plans: app.on_updates_available(plans) if app is not None else None,
        on_self_update_available=lambda check: app.on_self_update_available(check) if app is not None else None,
        on_state_change=lambda _state: app.refresh_icon() if app is not None else None,
    )
    app = TrayApp(scheduler=scheduler, config_path=config_path, owns_scheduler=True)
    icon = app.build_icon()

    threading.Thread(target=icon.run, name="umm-tray-icon", daemon=True).start()
    hotkey = start_hotkey(app, config_path)
    scheduler.start()
    if manager_config.load(config_path).preferences.watchdog_enabled:
        app.watchdog.start()
    for message in notes:
        app.notify(message)

    from .window import run_main_loop

    try:
        run_main_loop(app.api)
    finally:
        if hotkey is not None:
            hotkey.stop()
        scheduler.stop(timeout=2.0)
        app.watchdog.stop(timeout=2.0)
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
        holder.release()
    return 0
