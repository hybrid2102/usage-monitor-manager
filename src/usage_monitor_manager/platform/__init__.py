"""Platform abstraction layer: pick the implementation for the running OS.

Every other module imports from here (``from .platform import file_version``),
never from ``win32``/``linux`` directly, so that Phase 8 can add a real Linux
backend without touching the core.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .win32 import (
        DETACHED_PROCESS,
        MANAGER_RUN_NAME,
        MutexHolder,
        RegistryRoot,
        authenticode_status,
        delete_run_key,
        file_product_name,
        file_version,
        get_manager_autostart,
        hold_mutex,
        manager_autostart_command,
        mutex_is_held,
        no_window_popen_kwargs,
        parse_run_command,
        read_run_entries,
        read_run_key,
        read_shortcuts,
        set_manager_autostart,
        sync_manager_autostart_path,
        write_run_key,
    )
else:  # pragma: no cover - the v1 target is Windows only
    from .linux import (
        DETACHED_PROCESS,
        MANAGER_RUN_NAME,
        MutexHolder,
        RegistryRoot,
        authenticode_status,
        delete_run_key,
        file_product_name,
        file_version,
        get_manager_autostart,
        hold_mutex,
        manager_autostart_command,
        mutex_is_held,
        no_window_popen_kwargs,
        parse_run_command,
        read_run_entries,
        read_run_key,
        read_shortcuts,
        set_manager_autostart,
        sync_manager_autostart_path,
        write_run_key,
    )

__all__ = [
    "DETACHED_PROCESS",
    "MANAGER_RUN_NAME",
    "MutexHolder",
    "RegistryRoot",
    "authenticode_status",
    "delete_run_key",
    "file_product_name",
    "file_version",
    "get_manager_autostart",
    "hold_mutex",
    "manager_autostart_command",
    "mutex_is_held",
    "no_window_popen_kwargs",
    "parse_run_command",
    "read_run_entries",
    "read_run_key",
    "read_shortcuts",
    "set_manager_autostart",
    "sync_manager_autostart_path",
    "write_run_key",
]
