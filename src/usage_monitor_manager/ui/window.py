"""pywebview window: dashboard, changelog and the Manager page.

Threading arrangement (verified on Windows 11, and the reason this file says so
out loud): **pywebview owns the main thread, pystray runs in a background
thread.** ``webview.start()`` must run on the main thread - it pumps the
WebView2/Edge Chromium message loop and refuses to start anywhere else - while
``pystray`` on Windows creates *its own* hidden window and message loop inside
``Icon.run()``, which works from any thread as long as that thread lives as
long as the icon. The monitors do the opposite (pystray on the main thread,
webview in a thread) because their popup is short-lived and recreated per
click; that arrangement is **not** available here, because the manager's window
is created once and hidden on close, so the loop that owns it must be the
process's main loop.

Concretely, :func:`usage_monitor_manager.ui.tray.run_tray` starts the pystray
icon with ``Icon.run_detached``-like semantics (a daemon thread calling
``icon.run()``) and then calls :func:`run_main_loop` here on the main thread.

The window is created lazily on first open and only *hidden* when the user
closes it, so re-opening is instant and the page keeps its state.

:class:`JsApi` is the whole bridge: every method is callable from JavaScript as
``window.pywebview.api.<name>()`` and every one of them is a thin client of the
core modules (``discovery``, ``process_control``, ``updater``, ``github``,
``manager_config``, ``legacy``). It holds no UI state, which is what lets the
tests drive it with a faked core and no webview at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .. import __version__, manager_config
from ..model import Instance, JsonDict

__all__ = [
    "PAGES",
    "WINDOW_HEIGHT",
    "WINDOW_WIDTH",
    "JsApi",
    "ManagerWindow",
    "destroy_windows",
    "open_window",
    "run_main_loop",
    "web_dir",
]

WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 780
PAGES = ("dashboard", "updates", "manager")

BACKGROUND = "#1e1e1e"
"""Same background as the monitors' popup, so the family looks like a family."""

EventSink = Callable[[JsonDict], None]
"""Where :class:`JsApi` pushes progress events (the window, or a test's list)."""


def web_dir() -> Path:
    """The directory holding ``index.html``, ``app.css`` and ``app.js``.

    Frozen, PyInstaller unpacks the ``ui/web`` datas next to ``sys._MEIPASS``;
    from a checkout it is simply the package directory.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        packaged = Path(cast(str, base)) / "usage_monitor_manager" / "ui" / "web"
        if packaged.is_dir():
            return packaged
        return Path(cast(str, base)) / "ui" / "web"
    return Path(__file__).resolve().parent / "web"


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _uptime_seconds(instance: Instance) -> float | None:
    if instance.processes is None or instance.processes.started_at is None:
        return None
    return max(0.0, (datetime.now(tz=UTC) - instance.processes.started_at).total_seconds())


# --------------------------------------------------------------------------- #
# The JavaScript-facing API
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Cache:
    """The last discovery result, so a row action does not rescan the machine."""

    instances: tuple[Instance, ...] = ()
    fetched_at: datetime | None = None


class JsApi:
    """Everything the window (and the tray) can ask the core to do.

    All the heavy lifting is delegated; the only logic here is shaping the
    answers into JSON-ready dictionaries and forwarding progress events.

    Long operations (``restart``, ``apply_update``, ``check_updates``) block the
    calling thread on purpose: pywebview already calls ``js_api`` methods on a
    worker thread, and the tray wraps them in its own thread. Nothing here
    touches a UI object.
    """

    def __init__(
        self,
        *,
        emit: EventSink | None = None,
        scheduler: Any | None = None,
        config_path: Path | None = None,
        registry_root: Any | None = None,
    ) -> None:
        self._emit_sink = emit
        self._scheduler = scheduler
        self._config_path = config_path
        self._registry_root = registry_root
        self._cache = _Cache()
        self._lock = threading.Lock()

    # -- plumbing ------------------------------------------------------------

    def set_event_sink(self, emit: EventSink | None) -> None:
        """Point progress events at a (new) window, or nowhere."""
        self._emit_sink = emit

    def _emit(self, payload: JsonDict) -> None:
        sink = self._emit_sink
        if sink is None:
            return
        try:
            sink(payload)
        except Exception:  # noqa: BLE001 - a dead window must not fail an update
            return

    def _config(self) -> manager_config.ManagerConfig:
        return manager_config.load(self._config_path)

    # -- instances -----------------------------------------------------------

    def _discover(self, *, refresh: bool = True) -> tuple[Instance, ...]:
        from ..discovery import discover

        if not refresh and self._cache.instances:
            return self._cache.instances
        with self._lock:
            instances = tuple(discover())
            self._cache = _Cache(instances=instances, fetched_at=datetime.now(tz=UTC))
        return instances

    def _by_id(self, ids: Sequence[str]) -> list[Instance]:
        wanted = {str(value).strip().casefold() for value in ids}
        instances = self._cache.instances or self._discover()
        return [i for i in instances if i.instance_id.casefold() in wanted]

    def _update_state(self) -> JsonDict:
        if self._scheduler is None:
            return {"last_check": None, "available": [], "update_count": 0, "error": None, "checking": False}
        return dict(self._scheduler.state.to_dict())

    def _available_tags(self) -> dict[str, JsonDict]:
        """``{normcase(exe path): {tag, version, repo}}`` for the dashboard."""
        result: dict[str, JsonDict] = {}
        if self._scheduler is None:
            return result
        for plan in self._scheduler.state.available:
            result[os.path.normcase(str(plan.exe_path))] = {
                "tag": plan.target.tag,
                "version": plan.target.version,
                "repo": plan.kind.repo,
            }
        return result

    def instance_row(self, instance: Instance, *, labels: manager_config.ManagerConfig | None = None) -> JsonDict:
        """One dashboard row: the table of PLAN.md section 6.6."""
        from ..versions import display_version

        config = labels if labels is not None else self._config()
        remote = self._available_tags().get(os.path.normcase(str(instance.exe_path or "")))
        row = instance.to_dict()
        row.update(
            {
                "kind_display": instance.kind.display_name,
                "label": instance.label or config.label_for(instance.kind.key, instance.config_dir),
                "local_version": display_version(instance.exe_version),
                "remote_version": None if remote is None else remote["version"],
                "update_available": remote is not None,
                "uptime_seconds": _uptime_seconds(instance),
                "autostart_run_name": f"{instance.kind.run_key_base}{instance.instance_suffix}",
                "exe_dir": None if instance.exe_path is None else str(instance.exe_path.parent),
            }
        )
        return row

    def list_instances(self, refresh: bool = True) -> list[JsonDict]:
        """Every known instance, as the dashboard table wants it."""
        config = self._config()
        return [self.instance_row(instance, labels=config) for instance in self._discover(refresh=refresh)]

    # -- process control -----------------------------------------------------

    def _process_event(self, action: str) -> Callable[[Any], None]:
        def report(event: Any) -> None:
            self._emit({"type": "process_event", "action": action, "event": event.to_dict()})

        return report

    def restart(self, ids: Sequence[str]) -> list[JsonDict]:
        """Restart the given instances (stop them all, then start them all)."""
        from .. import process_control

        targets = self._by_id(ids)
        if not targets:
            return []
        result = process_control.restart(targets, on_event=self._process_event("restart"))
        self._discover()
        return [result.to_dict()]

    def stop(self, ids: Sequence[str]) -> list[JsonDict]:
        from .. import process_control

        results = [
            process_control.stop(instance, on_event=self._process_event("stop")).to_dict()
            for instance in self._by_id(ids)
        ]
        self._discover()
        return results

    def start(self, ids: Sequence[str]) -> list[JsonDict]:
        from .. import process_control

        results = [
            process_control.start(instance, on_event=self._process_event("start")).to_dict()
            for instance in self._by_id(ids)
        ]
        self._discover()
        return results

    def restart_all(self) -> list[JsonDict]:
        return self.restart([i.instance_id for i in self._discover() if i.is_running])

    def stop_all(self) -> list[JsonDict]:
        return self.stop([i.instance_id for i in self._discover() if i.is_running])

    def start_missing(self) -> list[JsonDict]:
        return self.start([i.instance_id for i in self._discover() if not i.is_running])

    # -- updates -------------------------------------------------------------

    def check_updates(self) -> JsonDict:
        """Run an update check now and return the new state."""
        if self._scheduler is None:
            return self._update_state()
        state = self._scheduler.check_now()
        self._emit({"type": "update_state", "state": state.to_dict()})
        return dict(state.to_dict())

    def get_update_state(self) -> JsonDict:
        return self._update_state()

    def get_changelog(self, exe_path: str) -> JsonDict:
        """The release notes (raw markdown) for one executable's pending update."""
        if self._scheduler is None:
            return {}
        wanted = os.path.normcase(str(exe_path))
        for plan in self._scheduler.state.plans:
            if os.path.normcase(str(plan.exe_path)) == wanted:
                return {
                    "exe_path": str(plan.exe_path),
                    "kind": plan.kind.key,
                    "kind_display": plan.kind.display_name,
                    "repo": plan.kind.repo,
                    "current_version": plan.current_version,
                    "tag": plan.target.tag,
                    "version": plan.target.version,
                    "published_at": _iso(plan.target.published_at),
                    "html_url": plan.target.html_url,
                    "body_markdown": plan.target.body_markdown,
                    "is_prerelease": plan.target.is_prerelease,
                }
        return {}

    def _plan_for(self, exe_path: str) -> Any | None:
        if self._scheduler is None:
            return None
        wanted = os.path.normcase(str(exe_path))
        for plan in self._scheduler.state.plans:
            if os.path.normcase(str(plan.exe_path)) == wanted:
                return plan
        return None

    def apply_update(self, exe_path: str) -> JsonDict:
        """Download, verify, swap and restart, streaming every step to the page.

        The progress events are exactly the ``UpdateEvent``s the CLI prints, so
        the page's log and ``umm update`` tell the same story.
        """
        from .. import updater

        plan = self._plan_for(exe_path)
        if plan is None:
            return {"ok": False, "detail": f"nessun aggiornamento pianificato per {exe_path}"}

        def report(event: Any) -> None:
            self._emit({"type": "update_event", "exe_path": str(exe_path), "event": event.to_dict()})

        result = updater.apply(plan, on_event=report)
        self._discover()
        if self._scheduler is not None:
            self._scheduler.check_now()
        return dict(result.to_dict())

    def rollback(self, exe_path: str) -> JsonDict:
        """Restore the ``.bak`` left by the last update of that executable."""
        from .. import updater

        def report(event: Any) -> None:
            self._emit({"type": "update_event", "exe_path": str(exe_path), "event": event.to_dict()})

        result = updater.rollback(Path(exe_path), instances=self._discover(), on_event=report)
        self._discover()
        return dict(result.to_dict())

    # -- configuration -------------------------------------------------------

    def get_config(self) -> JsonDict:
        config = self._config()
        payload = config.to_dict()
        payload["config_path"] = str(self._config_path or manager_config.config_path())
        return payload

    def set_preference(self, key: str, value: object) -> JsonDict:
        """Change one preference; the answer is the whole new config."""
        try:
            updated = manager_config.set_preference(key, value, self._config_path)
        except (KeyError, OSError) as error:
            return {"ok": False, "detail": str(error)}
        payload = updated.to_dict()
        payload["ok"] = True
        return payload

    def set_manager_autostart(self, enabled: bool) -> JsonDict:
        """Toggle the manager's own Run entry *and* remember it as a preference."""
        from ..platform import set_manager_autostart as write

        try:
            command = write(bool(enabled), None, self._registry_root)
        except OSError as error:
            return {"ok": False, "detail": str(error), "enabled": not enabled}
        manager_config.set_preference("autostart_manager", bool(enabled), self._config_path)
        return {"ok": True, "enabled": command is not None, "command": command}

    def set_instance_autostart(self, instance_id: str, enabled: bool) -> JsonDict:
        """Toggle one instance's Run entry (PLAN.md section 9.8)."""
        from .. import process_control

        targets = self._by_id([instance_id])
        if not targets:
            return {"ok": False, "detail": f"istanza {instance_id!r} sconosciuta"}
        try:
            command = process_control.set_instance_autostart(targets[0], bool(enabled), self._registry_root)
        except (OSError, ValueError) as error:
            return {"ok": False, "detail": str(error)}
        self._discover()
        return {"ok": True, "enabled": command is not None, "command": command}

    def get_settings_raw(self, instance_id: str) -> JsonDict:
        """The instance's ``usage-monitor-settings.json`` as text, read-only.

        The editing form is Phase 5; until then the window shows the active
        file verbatim, plus the shadowed candidates that explain why an edit
        somewhere else had no effect.
        """
        targets = self._by_id([instance_id])
        if not targets:
            return {"ok": False, "detail": f"istanza {instance_id!r} sconosciuta"}
        instance = targets[0]
        text: str | None = None
        error: str | None = None
        if instance.settings_path is not None:
            try:
                text = instance.settings_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as read_error:
                error = str(read_error)
        return {
            "ok": True,
            "instance_id": instance.instance_id,
            "label": instance.label,
            "path": None if instance.settings_path is None else str(instance.settings_path),
            "exists": instance.settings_exists,
            "shadowed": [str(path) for path in instance.shadow_settings_paths],
            "text": text,
            "error": error,
            "read_only": True,
        }

    # -- manager page --------------------------------------------------------

    def get_manager_info(self) -> JsonDict:
        from .. import github
        from ..platform import get_manager_autostart

        rate = github.rate_limit_state()
        token = github.resolve_token()
        try:
            autostart = get_manager_autostart(self._registry_root)
        except OSError:
            autostart = None
        return {
            "version": __version__,
            "autostart": autostart is not None,
            "autostart_command": autostart,
            "github_token_present": token is not None,
            "rate_limit": None if rate is None else rate.to_dict(),
            "config_path": str(self._config_path or manager_config.config_path()),
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
        }

    def import_legacy(self, dry_run: bool = True) -> JsonDict:
        from ..legacy import import_legacy as run

        report = run(dry_run=bool(dry_run), config_path=self._config_path)
        payload = report.to_dict()
        payload["lines"] = report.lines()
        return payload

    def open_folder(self, path: str) -> JsonDict:
        """Reveal a folder (or a file's folder) in Explorer."""
        target = Path(path)
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            return {"ok": False, "detail": f"{target} non esiste"}
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606 - a directory the user picked
            else:  # pragma: no cover - the v1 target is Windows
                subprocess.Popen(["xdg-open", str(target)])  # noqa: S603, S607
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        return {"ok": True, "path": str(target)}


# --------------------------------------------------------------------------- #
# The window itself
# --------------------------------------------------------------------------- #


class ManagerWindow:
    """Lazily created, hidden on close, never destroyed until the app exits."""

    def __init__(self, api: JsApi) -> None:
        self._api = api
        self._window: Any | None = None
        self._lock = threading.Lock()
        self.quitting = False
        """Set by :meth:`destroy` so the ``closing`` handler stops vetoing."""

    @property
    def exists(self) -> bool:
        return self._window is not None

    def _emit(self, payload: JsonDict) -> None:
        """Push one event into the page; ignored while no page is loaded."""
        window = self._window
        if window is None:
            return
        script = f"window.umm && window.umm.onEvent({json.dumps(payload)})"
        try:
            window.evaluate_js(script)
        except Exception:  # noqa: BLE001 - the page may be reloading or gone
            return

    def create(self, page: str = "dashboard") -> Any:
        """Create the window once; afterwards just return it."""
        import webview

        with self._lock:
            if self._window is not None:
                return self._window
            index = web_dir() / "index.html"
            # pywebview's signature is only partially typed; the window object
            # itself is used dynamically here.
            window: Any = webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "Usage Monitor Manager",
                url=f"{index.as_uri()}#{page}",
                width=WINDOW_WIDTH,
                height=WINDOW_HEIGHT,
                min_size=(900, 600),
                background_color=BACKGROUND,
                js_api=self._api,
                hidden=True,
            )
            if window is None:  # pragma: no cover - pywebview refused to build it
                raise RuntimeError("impossibile creare la finestra del manager")
            self._window = window
            self._api.set_event_sink(self._emit)

            def on_closing() -> bool:
                """Hide instead of closing, so re-opening is instant.

                Returning ``False`` vetoes the close; during shutdown
                :meth:`destroy` sets :attr:`quitting` first so the veto lifts
                and pywebview's loop can actually end.
                """
                if self.quitting:
                    return True
                window.hide()
                return False

            window.events.closing += on_closing
            return window

    def show(self, page: str = "dashboard") -> None:
        """Create (if needed), navigate to ``page``, and bring the window up."""
        window = self.create(page)
        try:
            window.show()
            if page in PAGES:
                window.evaluate_js(f"window.umm && window.umm.goto({json.dumps(page)})")
        except Exception:  # noqa: BLE001 - a window that will not show is not fatal
            return

    def hide(self) -> None:
        window = self._window
        if window is not None:
            try:
                window.hide()
            except Exception:  # noqa: BLE001
                return

    def destroy(self) -> None:
        """Really close the window, ending the pywebview loop."""
        self.quitting = True
        window = self._window
        self._window = None
        self._api.set_event_sink(None)
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001 - already gone
                return


_window_singleton: ManagerWindow | None = None


def open_window(page: str = "dashboard", api: JsApi | None = None) -> ManagerWindow:
    """Open (or focus) the manager window on the given page.

    The first call needs an ``api``; later calls reuse the process-wide window.
    """
    global _window_singleton  # noqa: PLW0603 - one window per process, by design
    if _window_singleton is None:
        _window_singleton = ManagerWindow(api if api is not None else JsApi())
    _window_singleton.show(page)
    return _window_singleton


def destroy_windows() -> None:
    """Tear the window down so ``webview.start()`` returns (tray "Esci")."""
    global _window_singleton  # noqa: PLW0603 - see open_window
    window = _window_singleton
    if window is not None:
        window.destroy()
    _window_singleton = None


def run_main_loop(api: JsApi, *, create_hidden: bool = True) -> None:
    """Own the main thread with pywebview's event loop.

    ``create_hidden`` builds the window up front (hidden), which makes the
    first "Apri Manager…" instant; ``webview.start()`` then blocks until the
    process ends. See the module docstring for why this has to be the main
    thread.
    """
    global _window_singleton  # noqa: PLW0603 - see open_window
    import webview

    if _window_singleton is None:
        _window_singleton = ManagerWindow(api)
    if create_hidden:
        _window_singleton.create()
    webview.start(private_mode=False)
