"""A stand-in for a real usage monitor, used by the integration tests.

It imitates exactly the three behaviours ``process_control`` depends on:

* it accepts ``--config-dir=PATH`` (and the environment variable named by
  ``UMM_DUMMY_ENV_VAR``, default ``UMM_DUMMY_CONFIG_DIR``);
* it holds the single-instance mutex ``<base><suffix>`` where ``base`` comes
  from ``UMM_DUMMY_MUTEX_BASE`` and ``suffix`` is computed with the monitors'
  own algorithm (``""`` for the default dir, else ``"_" + sha1(normcase(
  resolved path))[:12]``), exiting immediately when the name is already taken;
* it then sleeps until it is terminated.

The suffix algorithm is deliberately re-implemented here from the
specification in PLAN.md section 1.1 instead of importing
``usage_monitor_manager.identity``: this file is frozen into a one-file
executable by the test suite, and a stdlib-only script keeps that build small
and fast. If the two implementations ever disagree, the integration tests fail,
which is precisely what they are for.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

DEFAULT_ENV_VAR = "UMM_DUMMY_CONFIG_DIR"
EXIT_ALREADY_RUNNING = 3
EXIT_MISCONFIGURED = 4
ERROR_ALREADY_EXISTS = 183


def parse_config_dir(argv: list[str]) -> str | None:
    """``--config-dir=PATH`` or ``--config-dir PATH``, last occurrence wins."""
    found: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--config-dir" and index + 1 < len(argv):
            found = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--config-dir="):
            found = arg.split("=", 1)[1]
        index += 1
    return found


def config_dir_suffix(config_dir: Path, default_dir: Path) -> str:
    resolved = os.path.normcase(str(config_dir.resolve()))
    if resolved == os.path.normcase(str(default_dir.resolve())):
        return ""
    return "_" + hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]  # noqa: S324


def acquire_mutex(name: str) -> int:
    """Create the named mutex and keep the handle for the process lifetime."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return 0
    return int(handle)


def main(argv: list[str]) -> int:
    base = os.environ.get("UMM_DUMMY_MUTEX_BASE")
    default_raw = os.environ.get("UMM_DUMMY_DEFAULT_DIR")
    if not base or not default_raw:
        return EXIT_MISCONFIGURED
    env_var = os.environ.get("UMM_DUMMY_ENV_VAR", DEFAULT_ENV_VAR)

    default_dir = Path(default_raw)
    raw = parse_config_dir(argv) or os.environ.get(env_var) or str(default_dir)
    config_dir = Path(raw.strip().strip('"'))

    name = f"{base}{config_dir_suffix(config_dir, default_dir)}"
    if not acquire_mutex(name):
        return EXIT_ALREADY_RUNNING

    config_dir.mkdir(parents=True, exist_ok=True)
    marker = config_dir / "dummy-monitor.pid"
    marker.write_text(f"{os.getpid()}\n{name}\n", encoding="utf-8")
    while True:  # terminated from the outside; that is the whole point
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
