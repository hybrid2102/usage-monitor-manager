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

from .. import __version__, i18n, manager_config
from ..i18n import T, t
from ..model import Instance, JsonDict

__all__ = [
    "PAGES",
    "WINDOW_HEIGHT",
    "WINDOW_WIDTH",
    "JsApi",
    "ManagerWindow",
    "destroy_windows",
    "open_window",
    "toggle_window",
    "run_main_loop",
    "web_dir",
]

WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 780
PAGES = ("dashboard", "updates", "settings", "manager")

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
        watchdog: Any | None = None,
    ) -> None:
        self._emit_sink = emit
        self._scheduler = scheduler
        self._config_path = config_path
        self._registry_root = registry_root
        self._watchdog = watchdog
        self._on_quit: Callable[[], None] | None = None
        self._cache = _Cache()
        self._lock = threading.Lock()

    # -- plumbing ------------------------------------------------------------

    def set_watchdog(self, watchdog: Any | None) -> None:
        """Let the page read the watchdog the tray owns (never a second one)."""
        self._watchdog = watchdog

    def set_quit_callback(self, on_quit: Callable[[], None] | None) -> None:
        """How :meth:`apply_self_update` asks the tray to come down."""
        self._on_quit = on_quit

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
            return {"ok": False, "detail": t("window.no_planned_update", path=exe_path)}

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

    def get_translations(self) -> JsonDict:
        """The whole active catalog, so the page labels itself in one call.

        The page asks for this once at load and keeps it: a round trip per
        label would be absurd over the pywebview bridge. ``language`` is the
        code that was actually resolved (``auto`` already applied).
        """
        return {"language": i18n.language(), "strings": i18n.translations()}

    def set_preference(self, key: str, value: object) -> JsonDict:
        """Change one preference; the answer is the whole new config.

        Changing ``language`` activates the new catalog immediately, so the
        tray menu (rebuilt on every open) and a reloaded page are already in
        the new language.
        """
        try:
            updated = manager_config.set_preference(key, value, self._config_path)
        except (KeyError, OSError) as error:
            return {"ok": False, "detail": str(error)}
        if key == "language":
            i18n.set_language(updated.preferences.language)
        payload = updated.to_dict()
        payload["ok"] = True
        payload["language"] = i18n.language()
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
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        try:
            command = process_control.set_instance_autostart(targets[0], bool(enabled), self._registry_root)
        except (OSError, ValueError) as error:
            return {"ok": False, "detail": str(error)}
        self._discover()
        return {"ok": True, "enabled": command is not None, "command": command}

    # -- settings editor (phase 5) ------------------------------------------

    def _instance(self, instance_id: str) -> Instance | None:
        found = self._by_id([instance_id])
        return found[0] if found else None

    def _settings_payload(self, instance: Instance) -> JsonDict:
        """Everything the Impostazioni page needs for one instance."""
        from ..history import versions_payload
        from ..settings import store
        from ..settings.schema import schema_for

        suggested = store.suggested_path(instance)
        path = instance.settings_path or suggested
        loaded = store.load(path)
        report = store.validate(instance.kind.key, loaded.data)
        return {
            "ok": True,
            "instance_id": instance.instance_id,
            "kind": instance.kind.key,
            "kind_display": instance.kind.display_name,
            "label": instance.label or self._config().label_for(instance.kind.key, instance.config_dir),
            "config_dir": str(instance.config_dir),
            "path": str(path),
            "suggested_path": str(suggested),
            "exists": loaded.exists,
            "shadowed": [str(shadow) for shadow in instance.shadow_settings_paths],
            "data": loaded.data,
            "text": loaded.text,
            "error": loaded.error,
            "validation": report.to_dict(),
            "schema": schema_for(instance.kind.key).to_dict(),
            "history": versions_payload(instance),
            "is_running": instance.is_running,
        }

    def get_settings(self, instance_id: str) -> JsonDict:
        """The instance's settings, its schema, its history and its validation."""
        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        return self._settings_payload(instance)

    def get_schema(self, kind: str) -> JsonDict:
        """The declarative schema of one kind, for the form builder."""
        from ..settings.schema import schema_for

        try:
            return schema_for(str(kind)).to_dict()
        except KeyError as error:
            return {"ok": False, "detail": str(error)}

    def validate_draft(self, kind: str, data: JsonDict) -> JsonDict:
        """Validate a draft with the monitor's own rules (live, before saving)."""
        from ..settings import store

        try:
            return store.validate(str(kind), dict(data)).to_dict()
        except KeyError as error:
            return {"ok": False, "detail": str(error)}

    def save_settings(self, instance_id: str, data: JsonDict, restart: bool = False) -> JsonDict:
        """Validate, write (``.bak`` + history) and optionally restart.

        Invalid values stop the save: the point of the editor is that the user
        never discovers a mistake from the monitor's MessageBox.
        """
        from ..settings import store

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        report = store.validate(instance.kind.key, dict(data))
        if report.errors:
            return {
                "ok": False,
                "detail": T["window.settings_invalid"],
                "validation": report.to_dict(),
            }
        target = instance.settings_path or store.suggested_path(instance)
        try:
            store.save(target, report.normalized, instance=instance)
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        payload = self._settings_payload(instance)
        payload["saved_to"] = str(target)
        payload["detail"] = t("window.saved_to", path=target)
        if restart:
            payload["restart"] = self.restart([instance.instance_id])
        return payload

    def create_settings(self, instance_id: str) -> JsonDict:
        """Create an empty settings file at the monitor's preferred location."""
        from ..settings import store

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        target = store.suggested_path(instance)
        if target.is_file():
            return {"ok": False, "detail": t("window.already_exists", path=target)}
        try:
            store.create_default(target)
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        self._discover()
        instance = self._instance(instance_id) or instance
        payload = self._settings_payload(instance)
        payload["detail"] = t("window.created", path=target)
        return payload

    # -- history -------------------------------------------------------------

    def list_history(self, instance_id: str) -> JsonDict:
        """The archived versions of one instance, newest first."""
        from ..history import versions_payload

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        return {"ok": True, "versions": versions_payload(instance)}

    def get_history_version(self, instance_id: str, stamp: str) -> JsonDict:
        """One archived version plus its diff against the file on disk."""
        from ..history import diff_payload, read_version
        from ..settings import store

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        data = read_version(instance, str(stamp))
        if data is None:
            return {"ok": False, "detail": t("window.version_not_found", stamp=stamp)}
        current = store.load(instance.settings_path or store.suggested_path(instance)).data
        return {"ok": True, "stamp": stamp, "data": data, "diff": diff_payload(data, current)}

    def restore_history(self, instance_id: str, stamp: str) -> JsonDict:
        """Put an archived version back in place (itself archived first)."""
        from ..history import restore

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        try:
            data = restore(instance, str(stamp))
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        if data is None:
            return {"ok": False, "detail": t("window.version_not_found", stamp=stamp)}
        payload = self._settings_payload(instance)
        payload["detail"] = t("window.version_restored", stamp=stamp)
        return payload

    def _file_dialog(self, *, save: bool, filename: str = "") -> str | None:
        """Ask pywebview for a path; ``None`` when there is no window."""
        try:
            import webview
        except ImportError:  # pragma: no cover - the tests pass explicit paths
            return None
        windows = list(getattr(webview, "windows", []) or [])
        if not windows:
            return None
        dialog_type = webview.SAVE_DIALOG if save else webview.OPEN_DIALOG
        result = windows[0].create_file_dialog(
            dialog_type,
            save_filename=filename,
            file_types=("JSON (*.json)", "Tutti i file (*.*)"),
        )
        if not result:
            return None
        if isinstance(result, str):
            return result
        return str(result[0])

    def export_settings(self, instance_id: str, dest: str | None = None) -> JsonDict:
        """Write the instance's settings to a portable profile file."""
        from ..history import export

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        target = dest or self._file_dialog(save=True, filename=f"{instance.instance_id}-settings.json")
        if not target:
            return {"ok": False, "detail": T["window.export_cancelled"]}
        try:
            written = export(instance, Path(target))
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        return {"ok": True, "path": str(written), "detail": t("window.exported_to", path=written)}

    def import_settings(self, instance_id: str, src: str | None = None) -> JsonDict:
        """Validate a profile file and, when valid, install it."""
        from ..history import import_

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        source = src or self._file_dialog(save=False)
        if not source:
            return {"ok": False, "detail": T["window.import_cancelled"]}
        result = import_(instance, Path(source))
        payload = result.to_dict()
        if result.ok:
            payload.update(self._settings_payload(instance))
            payload["detail"] = result.detail
        return payload

    # -- copy, presets, quick action -----------------------------------------

    def copy_settings_from(
        self,
        instance_id: str,
        source_id: str,
        sections: Sequence[str],
        data: JsonDict | None = None,
    ) -> JsonDict:
        """Merge whole sections of another instance into the current draft.

        Nothing is written: the merged draft goes back to the form, so the
        user still sees the diff and presses Salva.
        """
        from ..settings import store

        instance = self._instance(instance_id)
        source = self._instance(source_id)
        if instance is None or source is None:
            return {"ok": False, "detail": T["window.unknown_instance"]}
        if source.kind.key != instance.kind.key:
            return {"ok": False, "detail": T["window.copy_same_kind_only"]}
        source_data = store.load(source.settings_path or store.suggested_path(source)).data
        fallback = store.load(instance.settings_path or store.suggested_path(instance)).data
        current = dict(data) if data is not None else fallback
        merged = store.copy_sections(source_data, current, list(sections), instance.kind.key)
        return {
            "ok": True,
            "data": merged,
            "validation": store.validate(instance.kind.key, merged).to_dict(),
            "detail": t("window.sections_copied", count=len(list(sections)), source=source.instance_id),
        }

    def apply_preset(self, instance_id: str, primary: str, data: JsonDict | None = None) -> JsonDict:
        """Derive ``bar_fg`` and the icon colours from one brand colour."""
        from ..settings import store
        from ..settings.presets import BRAND_PRESETS, apply_palette, palette_from_primary

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        fallback = store.load(instance.settings_path or store.suggested_path(instance)).data
        current = dict(data) if data is not None else fallback
        try:
            merged = apply_palette(current, str(primary), instance.kind.key)
            palette = palette_from_primary(str(primary))
        except ValueError as error:
            return {"ok": False, "detail": str(error)}
        return {
            "ok": True,
            "data": merged,
            "palette": palette,
            "presets": [{"name": name, "color": color} for name, color in BRAND_PRESETS],
            "validation": store.validate(instance.kind.key, merged).to_dict(),
        }

    def get_presets(self) -> list[JsonDict]:
        """The brand preset list, for the colour swatches."""
        from ..settings.presets import BRAND_PRESETS

        return [{"name": name, "color": color} for name, color in BRAND_PRESETS]

    def test_quick_action(self, instance_id: str, command: Sequence[str] | str | None = None) -> JsonDict:
        """Run the quick action once and capture its output.

        Only ever reached from the page's confirmation dialog: it launches
        whatever the user configured, which is why the UI asks first and the
        command actually run is echoed back in the result.
        """
        from ..platform import no_window_popen_kwargs
        from ..settings import store

        instance = self._instance(instance_id)
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        if command is None:
            data = store.load(instance.settings_path or store.suggested_path(instance)).data
            raw: Any = data.get("quick_action_command") or data.get("on_double_click_command") or []
        else:
            raw = command
        argv = [raw] if isinstance(raw, str) else [str(item) for item in raw]
        argv = [item for item in argv if str(item).strip()]
        if not argv:
            return {"ok": False, "detail": T["window.no_quick_action"]}
        single = len(argv) == 1
        try:
            completed = subprocess.run(  # noqa: S602 - the command the user configured, run on request
                argv[0] if single else argv,
                shell=single,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=str(instance.config_dir) if instance.config_dir.is_dir() else None,
                **no_window_popen_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"ok": False, "detail": str(error), "command": argv}
        return {
            "ok": completed.returncode == 0,
            "command": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "detail": t("window.exit_code", code=completed.returncode),
        }

    # -- manager page --------------------------------------------------------

    def get_manager_info(self) -> JsonDict:
        from .. import github, secrets, self_update
        from ..platform import get_manager_autostart

        rate = github.rate_limit_state()
        try:
            autostart = get_manager_autostart(self._registry_root)
        except OSError:
            autostart = None
        state = self._scheduler.state.self_update if self._scheduler is not None else None
        return {
            "version": __version__,
            "autostart": autostart is not None,
            "autostart_command": autostart,
            "github_token_present": github.resolve_token() is not None,
            "github_token_stored": secrets.has_token(),
            "github_token_source": secrets.token_source(),
            "credential_target": secrets.DEFAULT_TARGET,
            "rate_limit": None if rate is None else rate.to_dict(),
            "config_path": str(self._config_path or manager_config.config_path()),
            "frozen": self_update.is_frozen(),
            "executable": sys.executable,
            "repo": self_update.MANAGER_REPO,
            "self_update": None if state is None else state.to_dict(),
        }

    # -- GitHub token (PLAN.md section 6.5-bis) -------------------------------

    def set_github_token(self, token: str) -> JsonDict:
        """Store the token; the value is never sent back to the page."""
        from .. import secrets

        try:
            secrets.set_token(str(token))
        except ValueError:
            return {"ok": False, "detail": T["window.token_empty"], "present": secrets.has_token()}
        except secrets.CredentialError as error:
            return {"ok": False, "detail": str(error), "present": secrets.has_token()}
        return {"ok": True, "detail": T["window.token_saved"], "present": True}

    def clear_github_token(self) -> JsonDict:
        from .. import secrets

        removed = secrets.delete_token()
        return {
            "ok": True,
            "removed": removed,
            "present": False,
            "detail": T["window.token_removed"] if removed else T["window.token_absent"],
        }

    def get_rate_limit(self) -> JsonDict:
        """The rate limit seen in the last API response (section 6.5-bis)."""
        from .. import github, secrets

        rate = github.rate_limit_state()
        return {
            "rate_limit": None if rate is None else rate.to_dict(),
            "token_present": secrets.has_token(),
            "token_source": secrets.token_source(),
        }

    # -- self-update (PLAN.md section 6.5) ------------------------------------

    def check_self_update(self) -> JsonDict:
        from .. import self_update

        config = self._config()
        state = self_update.check_self(
            include_prerelease=config.include_prerelease_for(self_update.MANAGER_REPO),
            skipped_tag=config.skipped_versions.get(self_update.MANAGER_REPO),
        )
        self._emit({"type": "self_update_state", "state": state.to_dict()})
        return dict(state.to_dict())

    def apply_self_update(self) -> JsonDict:
        """Replace the manager and hand over to the new process.

        The window cannot survive this: once the successor is spawned the tray
        must quit, which is what ``on_exit`` does through the quit callback the
        tray installed.
        """
        from .. import self_update

        def report(event: Any) -> None:
            self._emit({"type": "self_update_event", "event": event.to_dict()})

        result = self_update.apply_self(report, on_exit=self._on_quit)
        return dict(result.to_dict())

    # -- skip / prerelease (PLAN.md section 9.5) ------------------------------

    def skip_version(self, key: str) -> JsonDict:
        """Skip the pending release of one kind, or of the manager itself."""
        from .. import manager_config as config_module
        from ..kinds import get_kind
        from ..self_update import MANAGER_REPO

        if key == "manager":
            state = self._scheduler.state.self_update if self._scheduler is not None else None
            tag = None if state is None else state.tag
            repo = MANAGER_REPO
        else:
            try:
                repo = get_kind(key).repo
            except KeyError as error:
                return {"ok": False, "detail": str(error)}
            tag = next(
                (
                    plan.target.tag
                    for plan in (self._scheduler.state.plans if self._scheduler is not None else ())
                    if plan.kind.key == key
                ),
                None,
            )
        if not tag:
            return {"ok": False, "detail": t("window.no_known_release", kind=key)}
        config_module.skip_version(repo, tag, self._config_path)
        if self._scheduler is not None:
            self._scheduler.check_now()
        return {"ok": True, "repo": repo, "tag": tag, "detail": t("window.version_skipped", tag=tag)}

    def unskip_version(self, key: str) -> JsonDict:
        from .. import manager_config as config_module
        from ..kinds import get_kind
        from ..self_update import MANAGER_REPO

        try:
            repo = MANAGER_REPO if key == "manager" else get_kind(key).repo
        except KeyError as error:
            return {"ok": False, "detail": str(error)}
        config_module.unskip_version(repo, self._config_path)
        if self._scheduler is not None:
            self._scheduler.forget_notifications()
            self._scheduler.check_now()
        return {"ok": True, "repo": repo, "detail": T["window.version_unskipped"]}

    def set_repo_prerelease(self, key: str, value: object) -> JsonDict:
        """Per-repo prerelease override; ``None`` falls back to the global flag."""
        from .. import manager_config as config_module
        from ..kinds import get_kind
        from ..self_update import MANAGER_REPO

        try:
            repo = MANAGER_REPO if key == "manager" else get_kind(key).repo
        except KeyError as error:
            return {"ok": False, "detail": str(error)}
        flag = None if value is None else bool(value)
        config_module.set_prerelease_repo(repo, flag, self._config_path)
        return {"ok": True, "repo": repo, "include_prerelease": flag}

    # -- archive and rollback (PLAN.md section 9.4) ---------------------------

    def list_archived(self, exe_path: str) -> list[JsonDict]:
        """The builds kept in ``.umm-archive`` for one executable, newest first."""
        from .. import archive

        return [item.to_dict() for item in archive.list_archived(Path(exe_path))]

    def restore_archived(self, exe_path: str, version: str | None = None) -> JsonDict:
        """Put an archived build back (the "Ripristina" button)."""
        from .. import archive

        def report(event: Any) -> None:
            self._emit({"type": "update_event", "exe_path": str(exe_path), "event": event.to_dict()})

        result = archive.restore(
            Path(exe_path),
            version,
            instances=self._discover(),
            on_event=report,
        )
        self._discover()
        return dict(result.to_dict())

    def prune_archive(self, exe_path: str, keep: int | None = None) -> JsonDict:
        from .. import archive

        limit = self._config().preferences.archive_keep if keep is None else int(keep)
        removed = archive.prune(Path(exe_path), keep=max(0, limit))
        return {"ok": True, "keep": limit, "removed": [item.to_dict() for item in removed]}

    # -- watchdog (PLAN.md section 9.2) ---------------------------------------

    def get_watchdog_state(self) -> JsonDict:
        """The findings of the last watchdog pass; empty when it is not running."""
        return {} if self._watchdog is None else dict(self._watchdog.state.to_dict())

    def repair_instance(self, instance_id: str) -> JsonDict:
        """Act on the worst anomaly of one instance ("Ripara")."""
        if self._watchdog is None:
            return {"ok": False, "detail": T["window.watchdog_off"]}
        events = self._watchdog.state.for_instance(instance_id)
        if not events:
            return {"ok": False, "detail": t("window.no_anomaly", instance=instance_id)}
        worst = max(events, key=lambda event: event.severity)
        outcome = self._watchdog.repair(worst)
        self._discover()
        return dict(outcome)

    def import_legacy(self, dry_run: bool = True) -> JsonDict:
        from ..legacy import import_legacy as run

        report = run(dry_run=bool(dry_run), config_path=self._config_path)
        payload = report.to_dict()
        payload["lines"] = report.lines()
        return payload

    # -- kind registry (PLAN.md section 9.9) ---------------------------------

    def list_kinds(self) -> list[JsonDict]:
        """Every registry entry, built-in and user-defined."""
        from .. import kinds

        kinds.reload(self._config(), self._config_path)
        return [kind.to_dict() for kind in kinds.all_kinds()]

    def add_kind(self, data: JsonDict) -> JsonDict:
        """Add a user-defined ``github-exe`` kind from the Manager-page form."""
        from .. import kinds

        entry = manager_config.CustomKind(
            key=str(data.get("key", "")).strip().casefold(),
            display_name=str(data.get("display_name", "")).strip(),
            exe_name=str(data.get("exe_name", "")).strip(),
            repo=str(data.get("repo", "")).strip(),
            asset_pattern=str(data.get("asset_pattern", "") or "").strip(),
            sha_sidecar=bool(data.get("sha_sidecar")),
            pe_product_name=(str(data.get("pe_product_name") or "").strip() or None),
        )
        problems = kinds.add_custom_kind(entry, self._config_path)
        return {"ok": not problems, "problems": problems, "kinds": self.list_kinds()}

    def remove_kind(self, key: str) -> JsonDict:
        """Remove a user-defined kind; built-ins are never removed."""
        from .. import kinds

        removed = kinds.remove_custom_kind(str(key), self._config_path)
        return {"ok": removed, "removed": removed, "kinds": self.list_kinds()}

    # -- launch profiles (PLAN.md section 9.13) ------------------------------

    def list_profiles(self) -> list[JsonDict]:
        """The configured launch profiles, for the tray submenu and the editor."""
        return [profile.to_dict() for profile in self._config().profiles]

    def save_profile(self, name: str, instance_ids: Sequence[str]) -> JsonDict:
        """Create or replace one profile."""
        label = str(name).strip()
        if not label:
            return {"ok": False, "detail": T["window.profile_name_empty"]}
        ids = tuple(dict.fromkeys(str(item).strip() for item in instance_ids if str(item).strip()))
        try:
            manager_config.upsert_profile(manager_config.Profile(name=label, instance_ids=ids), self._config_path)
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        return {"ok": True, "profiles": self.list_profiles()}

    def delete_profile(self, name: str) -> JsonDict:
        try:
            removed = manager_config.remove_profile(str(name), self._config_path)
        except OSError as error:
            return {"ok": False, "detail": str(error)}
        return {"ok": removed, "profiles": self.list_profiles()}

    def profile_plan(self, name: str) -> JsonDict:
        """What applying the profile would do, without doing it."""
        from .. import profiles

        profile = manager_config.find_profile(self._config(), str(name))
        if profile is None:
            return {"ok": False, "detail": t("window.unknown_profile", name=name)}
        plan = profiles.plan(profile, self._discover())
        return {"ok": True, **plan.to_dict(), "steps": profiles.describe(plan)}

    def apply_profile(self, name: str) -> JsonDict:
        """Start the profile's instances and stop the other monitors."""
        from .. import profiles

        profile = manager_config.find_profile(self._config(), str(name))
        if profile is None:
            return {"ok": False, "detail": t("window.unknown_profile", name=name)}
        plan = profiles.plan(profile, self._discover())
        result = profiles.apply_plan(plan, on_event=self._process_event("profile"))
        self._discover()
        return dict(result.to_dict())

    # -- diagnostics (PLAN.md section 9.7) -----------------------------------

    def diag_state(self, lines: int = 200) -> JsonDict:
        """The current diagnostic session and the tail of its log."""
        from .. import diag

        session = diag.read_state()
        return {
            "session": None if session is None else session.to_dict(),
            "lines": [] if session is None else diag.tail(session.log_path, max(1, int(lines))),
            "logs": [] if session is None else [str(path) for path in diag.list_logs(session.instance_id)],
            "manager_log": str(diag.manager_log_path()),
        }

    def diag_start(self, instance_id: str) -> JsonDict:
        from .. import diag

        instance = self._instance(str(instance_id))
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        result = diag.start_diagnostics(instance, on_event=self._process_event("diag"))
        self._discover()
        return dict(result.to_dict())

    def diag_stop(self, instance_id: str) -> JsonDict:
        from .. import diag

        instance = self._instance(str(instance_id))
        if instance is None:
            return {"ok": False, "detail": t("window.unknown_instance_id", instance=instance_id)}
        result = diag.stop_diagnostics(instance, on_event=self._process_event("diag"))
        self._discover()
        return dict(result.to_dict())

    def diag_log(self, path: str = "", lines: int = 200) -> JsonDict:
        """The tail of one log file (the viewer's "aggiorna" and "segui")."""
        from .. import diag

        target = Path(path) if path else diag.manager_log_path()
        return {"path": str(target), "lines": diag.tail(target, max(1, int(lines)))}

    def diag_report(self, instance_id: str = "", lines: int = 200) -> JsonDict:
        """The "Copia diagnostica" block: versions, paths, log tail, status."""
        from .. import diag
        from ..status_file import build_status

        instance = self._instance(str(instance_id)) if instance_id else None
        session = diag.read_state()
        log_lines: list[str] = []
        if session is not None:
            log_lines = diag.tail(session.log_path, max(1, int(lines)))
        elif instance is not None:
            logs = diag.list_logs(instance.instance_id)
            if logs:
                log_lines = diag.tail(logs[-1], max(1, int(lines)))
        text = diag.collect_report(
            build_status(check=False),
            instance=instance,
            log_lines=log_lines,
            manager_version=__version__,
        )
        return {"ok": True, "text": text}

    def get_manager_log(self, lines: int = 50) -> JsonDict:
        """The tail of ``manager.log`` shown on the Manager page."""
        from .. import diag

        return {"path": str(diag.manager_log_path()), "lines": diag.tail_manager_log(max(1, int(lines)))}

    # -- status file and hotkey ----------------------------------------------

    def set_status_file(self, path: str | None) -> JsonDict:
        """Point ``preferences.status_file`` somewhere (or nowhere) and write it."""
        from ..status_file import build_status, write_status_file

        value = (str(path).strip() or None) if path is not None else None
        result = self.set_preference("status_file", value)
        if not result.get("ok") or value is None:
            return result
        try:
            written = write_status_file(build_status(check=False), value)
        except OSError as error:
            return {**result, "ok": False, "detail": str(error)}
        return {**result, "written": str(written)}

    def set_hotkey(self, combo: str | None) -> JsonDict:
        """Validate and store the global hotkey (PLAN.md section 9.14).

        Only the *parsing* happens here: registering it belongs to the tray,
        which owns the listener and reports a conflict as a notification. The
        page is told that a restart of the tray is what makes it live.
        """
        from .. import platform as platform_layer

        value = (str(combo).strip() or None) if combo is not None else None
        if value is not None:
            try:
                value = platform_layer.normalize_hotkey(value)
            except platform_layer.HotkeyError as error:
                return {"ok": False, "detail": str(error)}
        result = self.set_preference("hotkey", value)
        return {**result, "hotkey": value, "restart_required": True}

    def open_folder(self, path: str) -> JsonDict:
        """Reveal a folder (or a file's folder) in Explorer."""
        target = Path(path)
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            return {"ok": False, "detail": t("window.path_missing", path=target)}
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
        self._visible = False
        self.quitting = False
        """Set by :meth:`destroy` so the ``closing`` handler stops vetoing."""

    @property
    def exists(self) -> bool:
        return self._window is not None

    @property
    def visible(self) -> bool:
        """Whether the window is currently shown.

        Tracked here rather than asked of pywebview: the toggle of the global
        hotkey (PLAN.md section 9.14) runs on a thread that must not block on
        the UI, and the window is only ever shown and hidden through this
        class.
        """
        return self._visible

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
                self._visible = False
                window.hide()
                return False

            window.events.closing += on_closing
            return window

    def show(self, page: str = "dashboard", focus: str | None = None) -> None:
        """Create (if needed), navigate to ``page``, and bring the window up.

        ``focus`` is the instance id the page should select - the tray's
        "Impostazioni…" entry opens the editor already on the right instance.
        """
        window = self.create(page)
        try:
            window.show()
            self._visible = True
            if page in PAGES:
                window.evaluate_js(f"window.umm && window.umm.goto({json.dumps(page)}, {json.dumps(focus)})")
        except Exception:  # noqa: BLE001 - a window that will not show is not fatal
            return

    def hide(self) -> None:
        window = self._window
        self._visible = False
        if window is not None:
            try:
                window.hide()
            except Exception:  # noqa: BLE001
                return

    def toggle(self, page: str = "dashboard") -> bool:
        """Show the window if it is hidden, hide it if it is up.

        Returns the new visibility. This is what the global hotkey calls.
        """
        if self._visible:
            self.hide()
            return False
        self.show(page)
        return True

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


def open_window(page: str = "dashboard", api: JsApi | None = None, focus: str | None = None) -> ManagerWindow:
    """Open (or focus) the manager window on the given page.

    The first call needs an ``api``; later calls reuse the process-wide window.
    """
    global _window_singleton  # noqa: PLW0603 - one window per process, by design
    if _window_singleton is None:
        _window_singleton = ManagerWindow(api if api is not None else JsApi())
    _window_singleton.show(page, focus)
    return _window_singleton


def toggle_window(api: JsApi | None = None) -> bool:
    """Toggle the process-wide manager window; returns the new visibility."""
    global _window_singleton  # noqa: PLW0603 - see open_window
    if _window_singleton is None:
        _window_singleton = ManagerWindow(api if api is not None else JsApi())
    return _window_singleton.toggle()


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
