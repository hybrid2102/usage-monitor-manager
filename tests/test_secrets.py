"""Tests for the GitHub token store (PLAN.md section 6.5-bis).

Two layers, deliberately separated:

* the **resolution order** and the public API run against an in-memory store,
  so they work on any machine and never touch the user's credentials;
* one test exercises the **real Windows Credential Manager**, because a ctypes
  binding that is only ever tested against a fake proves nothing. It uses its
  own target name (``UsageMonitorManagerTest/github``), never the real one, and
  deletes the credential afterwards - including when the assertions fail.
"""

from __future__ import annotations

import sys

import pytest

from usage_monitor_manager import github, secrets

TEST_TARGET = "UsageMonitorManagerTest/github"
"""Never ``secrets.DEFAULT_TARGET``: the user's own token must stay untouched."""


@pytest.fixture
def store() -> secrets.MemoryCredentialStore:
    memory = secrets.MemoryCredentialStore()
    previous = secrets.set_store(memory)
    try:
        yield memory
    finally:
        secrets.set_store(previous)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in secrets.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def test_set_get_delete_round_trip(store: secrets.MemoryCredentialStore) -> None:
    assert secrets.has_token() is False
    secrets.set_token("  ghp_secret  ")
    assert secrets.get_token() == "ghp_secret"  # stored trimmed
    assert secrets.has_token() is True
    assert secrets.delete_token() is True
    assert secrets.get_token() is None
    assert secrets.delete_token() is False  # deleting twice is not an error


def test_an_empty_token_is_refused(store: secrets.MemoryCredentialStore) -> None:
    with pytest.raises(ValueError):
        secrets.set_token("   ")
    assert secrets.has_token() is False


def test_the_target_name_is_injectable(store: secrets.MemoryCredentialStore) -> None:
    secrets.set_token("one", target=TEST_TARGET)
    assert secrets.get_token(target=TEST_TARGET) == "one"
    assert secrets.get_token() is None  # the default target is a different slot


def test_a_broken_store_reads_as_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class Exploding:
        def read(self, target: str) -> str | None:
            raise secrets.CredentialError("no credential manager here")

        def write(self, target: str, secret: str) -> None:  # pragma: no cover - not reached
            raise secrets.CredentialError("nope")

        def delete(self, target: str) -> bool:
            raise OSError("nope")

    previous = secrets.set_store(Exploding())
    try:
        assert secrets.get_token() is None
        assert secrets.has_token() is False
        assert secrets.delete_token() is False
    finally:
        secrets.set_store(previous)


# --------------------------------------------------------------------------- #
# Resolution order
# --------------------------------------------------------------------------- #


def test_resolution_order_credential_manager_then_env(
    store: secrets.MemoryCredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_env(monkeypatch)
    assert secrets.resolve_token() is None
    assert secrets.token_source() == "none"

    monkeypatch.setenv("GH_TOKEN", "from_gh")
    assert secrets.resolve_token() == "from_gh"
    assert secrets.token_source() == "GH_TOKEN"

    monkeypatch.setenv("GITHUB_TOKEN", "from_github")
    assert secrets.resolve_token() == "from_github"  # GITHUB_TOKEN wins over GH_TOKEN
    assert secrets.token_source() == "GITHUB_TOKEN"

    secrets.set_token("from_credential_manager")
    assert secrets.resolve_token() == "from_credential_manager"  # the store wins over both
    assert secrets.token_source() == "credential_manager"

    assert secrets.resolve_token("explicit") == "explicit"  # an argument wins over everything


def test_github_delegates_to_secrets(store: secrets.MemoryCredentialStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """``github.resolve_token`` is the name the update engine uses."""
    _clear_env(monkeypatch)
    secrets.set_token("stored")
    assert github.resolve_token() == "stored"
    assert github.resolve_token("argument") == "argument"


# --------------------------------------------------------------------------- #
# ctypes layer
# --------------------------------------------------------------------------- #


class _FakeAdvapi32:
    """Just enough of advapi32 to drive :class:`WindowsCredentialStore`."""

    def __init__(self, *, found: bool = True) -> None:
        self.found = found
        self.calls: list[str] = []

    def CredReadW(self, target: str, kind: int, flags: int, out: object) -> int:  # noqa: N802
        self.calls.append(f"read:{target}:{kind}")
        return 1 if self.found else 0

    def CredWriteW(self, credential: object, flags: int) -> int:  # noqa: N802
        self.calls.append("write")
        return 1

    def CredDeleteW(self, target: str, kind: int, flags: int) -> int:  # noqa: N802
        self.calls.append(f"delete:{target}")
        return 1 if self.found else 0

    def CredFree(self, pointer: object) -> None:  # noqa: N802
        self.calls.append("free")


def test_delete_reports_not_found_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ERROR_NOT_FOUND`` is "there was nothing", not a failure."""
    api = _FakeAdvapi32(found=False)
    store = secrets.WindowsCredentialStore()
    monkeypatch.setattr(store, "_advapi32", lambda: api)
    monkeypatch.setattr(store, "_ctypes", lambda: _FakeCtypes(secrets._ERROR_NOT_FOUND))
    assert store.delete(TEST_TARGET) is False
    assert api.calls == [f"delete:{TEST_TARGET}"]


def test_a_win32_failure_becomes_a_credential_error(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _FakeAdvapi32(found=False)
    store = secrets.WindowsCredentialStore()
    monkeypatch.setattr(store, "_advapi32", lambda: api)
    monkeypatch.setattr(store, "_ctypes", lambda: _FakeCtypes(5))  # ERROR_ACCESS_DENIED
    with pytest.raises(secrets.CredentialError) as error:
        store.delete(TEST_TARGET)
    assert "CredDeleteW" in str(error.value)


class _FakeCtypes:
    """``set_last_error``/``get_last_error`` with a canned code."""

    def __init__(self, code: int) -> None:
        self.code = code

    def set_last_error(self, value: int) -> None:
        return None

    def get_last_error(self) -> int:
        return self.code


def test_blob_decoding_accepts_utf16_and_utf8() -> None:
    assert secrets._decode("ghp_x".encode("utf-16-le")) == "ghp_x"
    assert secrets._decode(b"ghp_y") == "ghp_y"
    assert secrets._decode(b"") is None


# --------------------------------------------------------------------------- #
# The real Credential Manager
# --------------------------------------------------------------------------- #


@pytest.mark.real_credentials
@pytest.mark.skipif(sys.platform != "win32", reason="the Credential Manager is a Windows API")
def test_real_credential_manager_round_trip() -> None:
    """Write, read back and delete a credential under the *test* target name.

    The ``finally`` runs whatever happens, so a failing assertion cannot leave
    a credential behind; the final ``get_token`` proves the deletion took.
    """
    secrets.set_store(None)  # the real WindowsCredentialStore
    store = secrets.default_store()
    assert isinstance(store, secrets.WindowsCredentialStore)
    try:
        secrets.set_token("umm-test-token", target=TEST_TARGET)
        assert secrets.get_token(target=TEST_TARGET) == "umm-test-token"
        assert secrets.has_token(target=TEST_TARGET) is True
    finally:
        removed = secrets.delete_token(target=TEST_TARGET)
    assert removed is True
    assert secrets.get_token(target=TEST_TARGET) is None  # CredReadW now fails with ERROR_NOT_FOUND
