"""GitHub token storage: Windows Credential Manager with an environment fallback.

Implements PLAN.md section 6.5-bis. The decision recorded there is that the
token is *optional* (the manager works anonymously, at 60 requests/hour) and
that when it exists it lives in the **Windows Credential Manager**, never in
``manager-config.json`` and never in a file next to the executable.

Three properties matter and are the reason this module exists at all:

* **no ``keyring`` dependency.** ``advapi32.CredReadW`` / ``CredWriteW`` /
  ``CredDeleteW`` through ``ctypes`` is some sixty lines and keeps the frozen
  build small, the same trade-off ``platform/win32.py`` already makes for the
  version resource and the mutex.
* **the token never travels through argv.** ``umm token set`` reads it from
  stdin or copies it out of an environment variable; the UI field is masked
  and the value is never echoed back to the page. What a caller can ask for is
  *whether* a token exists (:func:`has_token`), not what it is.
* **everything is injectable.** The target name is a parameter and the whole
  backend is replaceable (:func:`set_store`), so the tests use the target
  ``UsageMonitorManagerTest/github`` and a non-Windows machine gets an
  in-memory store instead of an import error.

Resolution order, as PLAN.md prescribes: Credential Manager -> ``GITHUB_TOKEN``
-> ``GH_TOKEN`` -> anonymous. :func:`github.resolve_token` delegates here.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol

__all__ = [
    "CRED_PERSIST_LOCAL_MACHINE",
    "CRED_TYPE_GENERIC",
    "DEFAULT_TARGET",
    "ENV_NAMES",
    "CredentialError",
    "CredentialStore",
    "MemoryCredentialStore",
    "WindowsCredentialStore",
    "default_store",
    "delete_token",
    "get_token",
    "has_token",
    "resolve_token",
    "set_store",
    "set_token",
    "token_source",
]

DEFAULT_TARGET = "UsageMonitorManager/github"
"""Credential Manager target name (PLAN.md section 6.5-bis)."""

ENV_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")
"""Environment variables consulted, in order, when no credential is stored."""

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2

_ERROR_NOT_FOUND = 1168
_USER_NAME = "github"


class CredentialError(RuntimeError):
    """The credential store refused an operation, with the Win32 message."""


class CredentialStore(Protocol):
    """The three operations the manager needs from a secret store."""

    def read(self, target: str) -> str | None:
        """The stored secret, or ``None`` when the target does not exist."""
        ...

    def write(self, target: str, secret: str) -> None:
        """Store (or replace) the secret under ``target``."""
        ...

    def delete(self, target: str) -> bool:
        """Remove the secret; ``False`` when there was nothing to remove."""
        ...


# --------------------------------------------------------------------------- #
# Windows implementation
# --------------------------------------------------------------------------- #


def _decode(raw: bytes) -> str | None:
    """Decode a credential blob: UTF-16LE as we write it, UTF-8 as others do."""
    for encoding in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        cleaned = text.replace("\x00", "").strip()
        if cleaned:
            return cleaned
    return None


class WindowsCredentialStore:
    """``advapi32`` generic credentials, one target name per secret.

    The ``CREDENTIALW`` structure is declared lazily inside the methods:
    importing ``ctypes.wintypes`` on a non-Windows interpreter raises, and this
    module is imported by ``github.py``, i.e. by everything.
    """

    def _advapi32(self) -> Any:
        """The DLL handle, loaded with ``use_last_error`` so the code survives."""
        import ctypes

        return ctypes.WinDLL("advapi32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]

    def _structure(self) -> Any:
        import ctypes
        from ctypes import wintypes

        class _Credential(ctypes.Structure):
            _fields_ = (  # noqa: RUF012 - ctypes wants the class attribute
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            )

        return _Credential

    def _fail(self, operation: str, target: str) -> CredentialError:
        code = int(self._ctypes().get_last_error())
        return CredentialError(f"{operation} failed for {target!r}: Windows error {code} ({os.strerror(code)})")

    def _ctypes(self) -> Any:
        """``ctypes`` as an untyped module.

        Every call below builds a structure the type stubs cannot describe (a
        pointer to a locally declared ``ctypes.Structure``), so pinning the
        module to ``Any`` once is cleaner than a ``pyright: ignore`` on each
        line. The Win32 contract is documented in the calls themselves.
        """
        import ctypes

        return ctypes

    def read(self, target: str) -> str | None:
        api = self._ctypes()
        advapi32 = self._advapi32()
        pointer: Any = api.POINTER(self._structure())()
        api.set_last_error(0)
        if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, api.byref(pointer)):
            if api.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise self._fail("CredReadW", target)
        try:
            entry: Any = pointer.contents
            size = int(entry.CredentialBlobSize)
            if size <= 0:
                return None
            raw: bytes = api.string_at(entry.CredentialBlob, size)
        finally:
            advapi32.CredFree(pointer)
        return _decode(raw)

    def write(self, target: str, secret: str) -> None:
        api = self._ctypes()
        advapi32 = self._advapi32()
        blob = secret.encode("utf-16-le")
        buffer: Any = api.create_string_buffer(blob, max(len(blob), 1))
        entry: Any = self._structure()()
        entry.Type = CRED_TYPE_GENERIC
        entry.TargetName = target
        entry.CredentialBlobSize = len(blob)
        entry.CredentialBlob = api.cast(buffer, api.POINTER(api.c_char))
        entry.Persist = CRED_PERSIST_LOCAL_MACHINE
        entry.UserName = _USER_NAME
        api.set_last_error(0)
        if not advapi32.CredWriteW(api.byref(entry), 0):
            raise self._fail("CredWriteW", target)

    def delete(self, target: str) -> bool:
        api = self._ctypes()
        advapi32 = self._advapi32()
        api.set_last_error(0)
        if advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            return True
        if api.get_last_error() == _ERROR_NOT_FOUND:
            return False
        raise self._fail("CredDeleteW", target)


class MemoryCredentialStore:
    """A dictionary pretending to be the Credential Manager.

    Used on non-Windows (where the manager cannot run anyway, but the tests
    must) and by any test that wants the resolution order without touching the
    real store.
    """

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(initial or {})

    def read(self, target: str) -> str | None:
        return self._data.get(target)

    def write(self, target: str, secret: str) -> None:
        self._data[target] = secret

    def delete(self, target: str) -> bool:
        return self._data.pop(target, None) is not None


_store: CredentialStore | None = None


def default_store() -> CredentialStore:
    """The store in use: the real one on Windows, an in-memory one elsewhere."""
    global _store
    if _store is None:
        _store = WindowsCredentialStore() if sys.platform == "win32" else MemoryCredentialStore()
    return _store


def set_store(store: CredentialStore | None) -> CredentialStore | None:
    """Replace the backend (tests); ``None`` restores the default. Returns the old one."""
    global _store
    previous = _store
    _store = store
    return previous


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def get_token(*, target: str = DEFAULT_TARGET) -> str | None:
    """The stored token, or ``None``.

    A store that is unavailable (no Credential Manager, access denied) is
    reported as "no token": the manager must keep working anonymously, so this
    never raises.
    """
    try:
        secret = default_store().read(target)
    except (CredentialError, OSError, AttributeError, ValueError):
        return None
    if secret is None:
        return None
    cleaned = secret.strip()
    return cleaned or None


def set_token(token: str, *, target: str = DEFAULT_TARGET) -> None:
    """Store ``token``, replacing whatever was there.

    Raises:
        ValueError: the token is empty or only whitespace.
        CredentialError: the Credential Manager refused the write.
    """
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("the token is empty")
    default_store().write(target, cleaned)


def delete_token(*, target: str = DEFAULT_TARGET) -> bool:
    """Remove the stored token; ``False`` when there was none."""
    try:
        return default_store().delete(target)
    except (CredentialError, OSError, AttributeError, ValueError):
        return False


def has_token(*, target: str = DEFAULT_TARGET) -> bool:
    """Whether a token is stored, without handing the value out."""
    return get_token(target=target) is not None


def resolve_token(token: str | None = None, *, target: str = DEFAULT_TARGET) -> str | None:
    """Credential Manager -> ``GITHUB_TOKEN`` -> ``GH_TOKEN`` -> anonymous.

    An explicit ``token`` argument wins over all of them: it is the caller
    saying "use this one", which is what ``updater.apply(token=...)`` does.
    """
    if token and token.strip():
        return token.strip()
    stored = get_token(target=target)
    if stored:
        return stored
    for name in ENV_NAMES:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def token_source(*, target: str = DEFAULT_TARGET) -> str:
    """Where the token in force comes from: ``credential_manager``, an env name, or ``none``."""
    if get_token(target=target):
        return "credential_manager"
    for name in ENV_NAMES:
        value = os.environ.get(name)
        if value and value.strip():
            return name
    return "none"
