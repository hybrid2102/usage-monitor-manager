"""Integrity checks: sha256 (API digest or ``.sha256`` sidecar), Authenticode.

Implements the ``integrity.py`` part of PLAN.md section 6.3. The split of
responsibilities is deliberate:

* the **sha256** is the check that can *block* an update. Either GitHub's
  ``assets[].digest`` or the ``<asset>.exe.sha256`` sidecar gives an expected
  value; a mismatch aborts and the downloaded file is thrown away.
* the **Authenticode** state is *information only*. The monitors ship unsigned
  PyInstaller builds (PLAN.md section 1.1), so refusing an unsigned executable
  would refuse every update there is. It is reported, logged, never enforced.

Sidecar files come in the two shapes the family produces: the ``sha256sum``
format ``<hex>  <name>`` and a bare hex digest on its own line.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .model import JsonDict
from .platform import authenticode_status

__all__ = [
    "DigestSource",
    "IntegrityResult",
    "authenticode_status",
    "normalize_digest",
    "parse_sidecar",
    "sha256_file",
    "verify_digest",
]

_CHUNK = 1024 * 1024
_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")

DigestSource = Literal["digest", "sidecar", "none"]
"""Where the expected sha256 came from: the API, a sidecar file, or nowhere."""


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    """What is known about a downloaded file before it replaces anything."""

    hash_verified: bool
    source: DigestSource
    signature: str = "unavailable"
    expected: str | None = None
    actual: str | None = None

    @property
    def unverifiable(self) -> bool:
        """True when no expected digest was available at all."""
        return self.source == "none"

    def summary(self) -> str:
        """One line for a log or a progress view."""
        if self.source == "none":
            return f"no sha256 published: integrity unverifiable (signature: {self.signature})"
        origin = "GitHub API digest" if self.source == "digest" else ".sha256 sidecar"
        verdict = "matches" if self.hash_verified else "DOES NOT MATCH"
        return f"sha256 {verdict} the {origin} (signature: {self.signature})"

    def to_dict(self) -> JsonDict:
        return {
            "hash_verified": self.hash_verified,
            "source": self.source,
            "signature": self.signature,
            "expected": self.expected,
            "actual": self.actual,
        }


def sha256_file(path: Path | str) -> str:
    """Lowercase hex sha256 of a file, read in 1 MiB chunks (the exes are ~15 MB)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def normalize_digest(value: str | None) -> str | None:
    """Reduce ``sha256:<hex>``, ``SHA256 = <hex>`` or a bare hex to lowercase hex.

    Returns ``None`` when the value holds no 64-character hex digest, so a
    caller can treat "no usable digest" and "no digest at all" the same way.
    """
    if not value:
        return None
    match = _HEX64.search(value.strip())
    return match.group(1).lower() if match else None


def parse_sidecar(text: str) -> str | None:
    """Extract the digest from a ``.sha256`` sidecar's contents.

    Accepts the ``sha256sum`` layout (``<hex>  <name>``, with the ``*name``
    binary marker as well) and a file containing nothing but the hex digest.
    Comment lines and blank lines are skipped. ``None`` when nothing usable is
    found.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        digest = normalize_digest(stripped)
        if digest:
            return digest
    return None


def verify_digest(path: Path | str, expected_hex: str | None) -> bool:
    """True when the file's sha256 equals ``expected_hex``.

    ``expected_hex`` may carry the ``sha256:`` prefix GitHub uses. A missing or
    unparsable expectation is *not* a match: "unverifiable" is a decision for
    the caller (see :class:`IntegrityResult`), never a silent pass.
    """
    expected = normalize_digest(expected_hex)
    if expected is None:
        return False
    try:
        actual = sha256_file(path)
    except OSError:
        return False
    return actual == expected
