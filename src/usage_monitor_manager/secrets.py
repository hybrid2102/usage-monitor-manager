"""GitHub token storage: Windows Credential Manager with env fallback.

Phase 0: stub. Implemented in phase 6 of PLAN.md section 7.
"""

from __future__ import annotations

__all__ = ["delete_token", "read_token", "write_token"]


def read_token() -> str | None:
    """Credential Manager, then ``GITHUB_TOKEN``/``GH_TOKEN``, then anonymous."""
    raise NotImplementedError


def write_token(token: str) -> None:
    """Store the token via ``CredWriteW``."""
    raise NotImplementedError


def delete_token() -> None:
    """Remove the stored credential."""
    raise NotImplementedError
