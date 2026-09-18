"""PyInstaller entry script.

The spec cannot point at ``usage_monitor_manager/__main__.py`` directly:
PyInstaller runs the entry script as a top-level module, so every relative
import inside the package (``from .cli import main``) fails with
``attempted relative import with no known parent package``. This launcher is
imported as an ordinary script instead, and *it* imports the package by its
absolute name, which keeps ``usage_monitor_manager`` a real package at runtime.

It exists only for the frozen build; from a checkout the entry points are the
``umm`` console script and ``python -m usage_monitor_manager``.
"""

from __future__ import annotations

import sys

from usage_monitor_manager.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
