"""Version handling: parse, compare, read from a PE, classify against a release.

The monitors publish tags like ``v1.22.0`` while their executables carry a PE
``ProductVersion`` of ``1.22.0.0``: everything here normalizes to three
components, exactly like the PowerShell updater did (PLAN.md section 1.4).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from .platform import file_version

__all__ = [
    "VersionStatus",
    "compare",
    "display_version",
    "exe_mtime",
    "exe_version",
    "format_version",
    "parse_version",
    "status_for",
]

_NUMBER_RE = re.compile(r"\d+")


class VersionStatus(Enum):
    """Outcome of comparing a local executable with a remote release."""

    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    NEWER_LOCAL = "newer_local"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


def parse_version(text: str | None) -> tuple[int, int, int]:
    """Parse a version string into a 3-tuple, tolerantly.

    ``v1.22.0``, ``1.22.0.0`` (the PE form) and ``1.22`` all become
    ``(1, 22, 0)``; a fourth PE component is dropped, because upstream never
    uses it for anything but padding. Non-numeric junk is ignored, so
    ``0.3.2-beta.1`` is ``(0, 3, 2)``. An unparsable or missing value is
    ``(0, 0, 0)`` - callers that must distinguish "no version" use
    :func:`status_for`, which treats ``None`` as unknown.
    """
    if not text:
        return (0, 0, 0)
    numbers = [int(match.group()) for match in _NUMBER_RE.finditer(text.strip())]
    numbers = (numbers + [0, 0, 0])[:3]
    return (numbers[0], numbers[1], numbers[2])


def format_version(text: str | None) -> str:
    """Normalize a version string to its canonical ``a.b.c`` form."""
    return ".".join(str(part) for part in parse_version(text))


def display_version(pe_version: str | None) -> str | None:
    """Version as a user should read it: three components, no PE padding.

    The executables carry ``1.22.0.0`` while the releases are tagged
    ``v1.22.0``; showing the PE form next to the tag makes every up-to-date
    instance look different from its release. ``None`` in, ``None`` out, so the
    caller keeps its own placeholder ("?" in the CLI table).
    """
    if not pe_version:
        return None
    return format_version(pe_version)


def compare(a: str | None, b: str | None) -> int:
    """Return -1, 0 or 1 comparing two versions normalized to 3 components."""
    left, right = parse_version(a), parse_version(b)
    if left < right:
        return -1
    return 1 if left > right else 0


def exe_version(path: Path | str | None) -> str | None:
    """PE ``ProductVersion`` of an executable, ``None`` when unavailable."""
    if path is None:
        return None
    try:
        return file_version(path)
    except (OSError, NotImplementedError):
        return None


def exe_mtime(path: Path | str | None) -> datetime | None:
    """Last-modified time of an executable as an aware UTC datetime."""
    if path is None:
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return datetime.fromtimestamp(stat.st_mtime, tz=UTC)


def status_for(local: str | None, remote: str | None) -> VersionStatus:
    """Classify a local version against the latest remote one.

    ``UNKNOWN`` when either side is missing (a PE without version info, or a
    release check that did not run); otherwise the usual three outcomes, with
    ``NEWER_LOCAL`` covering local development builds.
    """
    if not local or not remote:
        return VersionStatus.UNKNOWN
    result = compare(local, remote)
    if result == 0:
        return VersionStatus.UP_TO_DATE
    return VersionStatus.UPDATE_AVAILABLE if result < 0 else VersionStatus.NEWER_LOCAL
