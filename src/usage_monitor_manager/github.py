"""GitHub releases client: ETag cache, rate limit handling, asset selection.

Phase 0: stub. Implemented in phase 3 of PLAN.md section 7.
"""

from __future__ import annotations

from pathlib import Path

from .model import MonitorKind, ReleaseInfo

__all__ = ["download_asset", "latest_release"]


def latest_release(kind: MonitorKind, *, include_prerelease: bool = False) -> ReleaseInfo | None:
    """Fetch the latest release for a kind's repo, honouring the ETag cache."""
    raise NotImplementedError


def download_asset(release: ReleaseInfo, destination: Path) -> Path:
    """Download the release asset next to its target, reporting progress."""
    raise NotImplementedError
