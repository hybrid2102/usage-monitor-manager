"""Suite-wide safety net: no test reaches the network or the real credential store.

Two autouse fixtures, both of which fail loudly rather than silently doing the
real thing:

* every HTTP call of the manager goes through ``github._open`` (that is the
  single seam the module documents). Replacing it with a function that fails
  the test means a new code path that forgets to inject a fake - the manager's
  own self-update check inside ``UpdateScheduler``, for instance - is caught
  the first time it runs instead of quietly burning the anonymous rate limit.
* ``secrets`` gets an empty in-memory store, so nothing reads or writes the
  real Windows Credential Manager. The tests that exercise the real store
  (``test_secrets.py``) opt out explicitly and use their own target name.
"""

from __future__ import annotations

from typing import Any

import pytest

from usage_monitor_manager import github, secrets, watchdog


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail any test that opens a real connection to GitHub."""
    if "allow_network" in request.keywords:  # pragma: no cover - no test uses it yet
        return

    def refuse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("this test tried to reach the network; inject a fake instead")

    monkeypatch.setattr(github, "_open", refuse)


@pytest.fixture(autouse=True)
def no_real_credentials(request: pytest.FixtureRequest) -> Any:
    """Keep the real Credential Manager out of every test but its own."""
    if "real_credentials" in request.keywords:
        yield
        return
    previous = secrets.set_store(secrets.MemoryCredentialStore())
    try:
        yield
    finally:
        secrets.set_store(previous)


@pytest.fixture(autouse=True)
def fresh_watchdog_registry() -> None:
    """Start every test with an empty "expected down" set.

    ``updater.apply`` announces its own stops to a process-wide registry; left
    alone, a test that updates an instance would make a later watchdog test
    believe that same instance is allowed to be missing.
    """
    watchdog.reset_expected()


@pytest.fixture(autouse=True)
def pristine_kind_registry() -> Any:
    """Undo whatever a test merged into the live kind registry.

    ``kinds.KINDS`` is a process-wide dict that ``kinds.reload`` rewrites from
    ``manager-config.json``. A test that adds a custom kind - or any code path
    that reloads the registry from a temporary config file - would otherwise
    decide what the *next* test discovers.
    """
    from usage_monitor_manager import kinds

    saved = dict(kinds.KINDS)
    try:
        yield
    finally:
        kinds.KINDS.clear()
        kinds.KINDS.update(saved)


@pytest.fixture
def english_cli(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run the CLI against the English catalog.

    ``main()`` activates ``preferences.language`` before it prints anything,
    which for these tests would mean the Italian default. The CLI test modules
    assert the English wording (it is the one a script or a CI log shows), so
    they pin the language here and neutralize that call; the Italian catalog is
    covered by the parity and key-scan tests of ``test_i18n.py``, and by the
    tests that read the Italian messages on purpose (``test_cli_config.py``).
    """
    from usage_monitor_manager import i18n

    i18n.set_language("en")
    monkeypatch.setattr(i18n, "set_language", lambda _preference: i18n.language())
    try:
        yield
    finally:
        i18n.reset()
