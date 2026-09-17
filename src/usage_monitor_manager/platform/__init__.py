"""Platform abstraction layer: pick the implementation for the running OS.

Every other module imports from here (``from .platform import file_version``),
never from ``win32``/``linux`` directly, so that Phase 8 can add a real Linux
backend without touching the core.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .win32 import (
        delete_run_key,
        file_product_name,
        file_version,
        no_window_popen_kwargs,
        parse_run_command,
        read_run_entries,
        read_run_key,
        read_shortcuts,
        write_run_key,
    )
else:  # pragma: no cover - the v1 target is Windows only
    from .linux import (
        delete_run_key,
        file_product_name,
        file_version,
        no_window_popen_kwargs,
        parse_run_command,
        read_run_entries,
        read_run_key,
        read_shortcuts,
        write_run_key,
    )

__all__ = [
    "delete_run_key",
    "file_product_name",
    "file_version",
    "no_window_popen_kwargs",
    "parse_run_command",
    "read_run_entries",
    "read_run_key",
    "read_shortcuts",
    "write_run_key",
]
