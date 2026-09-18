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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__, legacy, manager_config, platform
from ..model import JsonDict
from ..scheduler import UpdateScheduler
from .window import JsApi

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
    state = "in esecuzione" if running else "non in esecuzione"
    text = f"{label} \u00b7 {short_path(str(row.get('config_dir', '')))}  {version}  {mark} {state}"
    if row.get("update_available"):
        text += f"  \u2191 {row.get('remote_version') or '?'} disponibile"
    return text


def _instance_submenu(row: JsonDict) -> tuple[MenuEntry, ...]:
    instance_id = str(row.get("id", ""))
    running = bool(row.get("is_running"))
    has_exe = bool(row.get("exe_path"))
    entries: list[MenuEntry] = [
        MenuEntry("Riavvia", "restart", (instance_id,), enabled=running),
        MenuEntry("Ferma", "stop", (instance_id,), enabled=running),
        MenuEntry("Avvia", "start", (instance_id,), enabled=not running and has_exe),
        SEPARATOR,
        MenuEntry("Impostazioni\u2026", "settings", (instance_id,)),
        MenuEntry("Apri cartella", "open_folder", (str(row.get("config_dir", "")),)),
    ]
    if row.get("update_available"):
        entries.extend(
            [
                SEPARATOR,
                MenuEntry(
                    f"Aggiorna a {row.get('remote_version') or '?'}\u2026",
                    "update",
                    (str(row.get("exe_path") or ""),),
                    enabled=has_exe,
                ),
            ]
        )
    return tuple(entries)


def _last_check_text(last_check: str | None) -> str:
    if not last_check:
        return "Controlla aggiornamenti (mai)"
    try:
        stamp = datetime.fromisoformat(last_check).astimezone()
    except ValueError:
        return "Controlla aggiornamenti (mai)"
    return f"Controlla aggiornamenti (ultimo: {stamp:%H:%M})"


def build_menu_spec(
    rows: Sequence[JsonDict],
    update_state: JsonDict | None = None,
    version: str = __version__,
) -> tuple[MenuEntry, ...]:
    """The whole menu of PLAN.md section 6.6 as a tree of :class:`MenuEntry`.

    ``rows`` are the dictionaries :meth:`JsApi.list_instances` returns.
    """
    state = update_state or {}
    entries: list[MenuEntry] = []
    if not rows:
        entries.append(MenuEntry("Nessuna istanza trovata", None, enabled=False))
    for row in rows:
        entries.append(
            MenuEntry(_instance_text(row), None, submenu=_instance_submenu(row), extra={"id": row.get("id")})
        )

    any_running = any(row.get("is_running") for row in rows)
    any_stopped = any(not row.get("is_running") and row.get("exe_path") for row in rows)
    entries.extend(
        [
            SEPARATOR,
            MenuEntry("Riavvia tutte", "restart_all", enabled=any_running),
            MenuEntry("Ferma tutte", "stop_all", enabled=any_running),
            MenuEntry("Avvia mancanti", "start_missing", enabled=any_stopped),
            SEPARATOR,
            MenuEntry(
                _last_check_text(state.get("last_check")),
                "check",
                enabled=not bool(state.get("checking")),
            ),
            MenuEntry("Apri Manager\u2026", "open_window", default=True),
            SEPARATOR,
            MenuEntry(f"Manager {version}", None, enabled=False),
            MenuEntry("Esci", "quit"),
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

    # -- data ----------------------------------------------------------------

    @property
    def api(self) -> JsApi:
        return self._api

    @property
    def scheduler(self) -> UpdateScheduler:
        return self._scheduler

    def rows(self, *, refresh: bool = True) -> list[JsonDict]:
        """The instance rows the menu is built from."""
        try:
            self._rows = self._api.list_instances(refresh=refresh)
        except Exception:  # noqa: BLE001 - a failed scan must not kill the menu
            pass
        return self._rows

    def spec(self) -> tuple[MenuEntry, ...]:
        return build_menu_spec(self.rows(), self._scheduler.state.to_dict())

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
            except Exception as error:  # noqa: BLE001 - reported as a notification
                self.notify(f"{name} non riuscito: {error}", "Usage Monitor Manager")
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

    def open_window(self, page: str = "dashboard", **params: object) -> None:
        """Show the manager window; the window itself lives on the main thread."""
        from .window import open_window

        def work() -> None:
            focus = params.get("instance_id") or params.get("exe_path")
            window = open_window(page, self._api, str(focus) if focus else None)
            if focus:
                window.show(page, str(focus))

        self._in_worker("apri manager", work)

    # -- icon state ----------------------------------------------------------

    def notify(self, message: str, title: str = "Usage Monitor Manager") -> None:
        """A Windows balloon through pystray; silently ignored when unavailable."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title)
        except Exception:  # noqa: BLE001 - notifications are never load-bearing
            return

    def refresh_icon(self) -> None:
        """Redraw the badge and let the shell know the menu changed."""
        icon = self._icon
        if icon is None:
            return
        state = self._scheduler.state
        try:
            icon.icon = make_image(state.update_count, state.error is not None)
            icon.title = self.tooltip()
            icon.update_menu()
        except Exception:  # noqa: BLE001 - a shell hiccup is not fatal
            return

    def tooltip(self) -> str:
        state = self._scheduler.state
        running = sum(1 for row in self._rows if row.get("is_running"))
        parts = [f"Usage Monitor Manager {__version__}", f"{running}/{len(self._rows)} in esecuzione"]
        if state.error is not None:
            parts.append("ultimo controllo non riuscito")
        elif state.update_count:
            parts.append(f"{state.update_count} aggiornamenti disponibili")
        return " \u2014 ".join(parts)

    def on_updates_available(self, plans: Sequence[Any]) -> None:
        """Scheduler callback: one toast per newly published release."""
        for plan in plans:
            self.notify(
                f"{plan.kind.display_name} {plan.target.version} disponibile (hai la {plan.current_version or '?'})",
                "Aggiornamento disponibile",
            )
        self.refresh_icon()

    # -- lifecycle -----------------------------------------------------------

    def build_icon(self) -> Any:
        import pystray  # pyright: ignore[reportMissingTypeStubs]

        icon = pystray.Icon(
            "usage-monitor-manager",
            icon=make_image(),
            title=f"Usage Monitor Manager {__version__}",
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
            messages.append(f"autostart non sincronizzato: {error}")
    report = legacy.run_once(config_path)
    if report is not None and report.added:
        names = ", ".join(entry.label or entry.kind for entry in report.added)
        messages.append(f"importate {len(report.added)} istanze dalla configurazione precedente: {names}")
    return messages


def run_tray(config_path: Path | None = None) -> int:
    """Run the tray icon (and the window loop) until the user quits.

    Returns 0 on a clean exit, 1 when another manager already holds the
    single-instance mutex.
    """
    holder = platform.hold_mutex(MUTEX_NAME)
    if holder is None:
        print("Usage Monitor Manager e' gia' in esecuzione (icona nella tray).")
        return 1

    messages = _startup_housekeeping(config_path)

    app: TrayApp | None = None
    scheduler = UpdateScheduler(
        config_path=config_path,
        on_updates_available=lambda plans: app.on_updates_available(plans) if app is not None else None,
        on_state_change=lambda _state: app.refresh_icon() if app is not None else None,
    )
    app = TrayApp(scheduler=scheduler, config_path=config_path, owns_scheduler=True)
    icon = app.build_icon()

    threading.Thread(target=icon.run, name="umm-tray-icon", daemon=True).start()
    scheduler.start()
    for message in messages:
        app.notify(message)

    from .window import run_main_loop

    try:
        run_main_loop(app.api)
    finally:
        scheduler.stop(timeout=2.0)
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
        holder.release()
    return 0
