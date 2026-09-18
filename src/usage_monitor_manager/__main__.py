"""Process entry point: CLI when arguments are given, tray otherwise.

``python -m usage_monitor_manager`` with no arguments is the tray mode (the
default for the packaged ``UsageMonitorManager.exe``); with arguments it is the
same dispatcher as the ``umm`` console script, which also understands the
``tray`` subcommand explicitly. ``--finish-self-update`` will be handled here
too (Phase 6).

The imports are absolute on purpose. The frozen build reaches :func:`main`
through ``src/umm_launcher.py``, and a relative import here would break the
moment PyInstaller runs a module as a top-level script.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from usage_monitor_manager.cli import main as cli_main

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the CLI, or to the tray when invoked without arguments."""
    args = list(sys.argv[1:] if argv is None else argv)
    return cli_main(args or ["tray"])


if __name__ == "__main__":
    raise SystemExit(main())
