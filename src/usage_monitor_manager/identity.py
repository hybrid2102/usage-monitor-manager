"""Instance identity: ``--config-dir`` parsing and the config-dir suffix.

These two functions replicate, byte for byte in their output, the upstream
monitors' own ``parse_config_dir()`` / ``config_dir_suffix()``. That is what
makes the manager able to predict the monitors' mutex names and
``HKCU\\...\\Run`` entries (e.g. ``UsageMonitorForClaude_578560bbc610`` for
``C:\\Users\\Marco\\.claude-valeria``). Do not "improve" the algorithm.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = ["CONFIG_DIR_FLAG", "config_dir_suffix", "parse_config_dir"]

CONFIG_DIR_FLAG = "--config-dir"


def _normalize(raw: str) -> Path | None:
    value = raw.strip().strip('"').strip("'")
    if not value:
        return None
    value = os.path.expandvars(value)
    value = os.path.expanduser(value)
    value = value.rstrip("\\/")
    if not value:
        return None
    return Path(value)


def parse_config_dir(argv: Sequence[str] | Iterable[str]) -> Path | None:
    """Extract the config dir from a command line.

    Accepts both ``--config-dir=PATH`` and ``--config-dir PATH``. Surrounding
    quotes, ``%VAR%`` / ``$VAR`` references, ``~`` and trailing separators are
    normalized the same way the monitors do. Returns ``None`` when the flag is
    absent or carries an empty value. The last occurrence wins.
    """
    args = list(argv)
    found: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == CONFIG_DIR_FLAG:
            if index + 1 < len(args):
                found = _normalize(args[index + 1]) or found
            index += 2
            continue
        if arg.startswith(f"{CONFIG_DIR_FLAG}="):
            found = _normalize(arg[len(CONFIG_DIR_FLAG) + 1 :]) or found
        index += 1
    return found


def config_dir_suffix(config_dir: Path | str, default_dir: Path | str) -> str:
    """Return ``""`` for the default config dir, else ``"_" + sha1[:12]``.

    The hash is computed over ``os.path.normcase(str(Path(config_dir).resolve()))``
    encoded as UTF-8, exactly as upstream.
    """
    resolved = os.path.normcase(str(Path(config_dir).resolve()))
    default_resolved = os.path.normcase(str(Path(default_dir).resolve()))
    if resolved == default_resolved:
        return ""
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()  # noqa: S324 - parity with upstream
    return f"_{digest[:12]}"
