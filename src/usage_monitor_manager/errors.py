"""One error hierarchy for the whole manager (PLAN.md section 7, phase 8).

Every layer above the core raises - or re-raises - an :class:`UmmError`, which
carries three things and nothing else:

* ``code``: the process exit code the CLI must end with. It is part of the
  error, not of the call site, because the same failure has to mean the same
  exit code whether it happened during ``umm update`` or ``umm rollback`` (the
  codes are the ones documented in the README and used by the Task Scheduler
  job of PLAN.md section 9.10);
* ``message``: one line, for a user. No module names, no exception classes;
* ``hint``: the sentence that says what to do about it, or ``None``. A hint is
  not decoration: "the executable is held by OneDrive" is useless without
  "pause the sync or close the file and try again".

The three consumers treat them the same way and differ only in how they show
them:

* the **CLI** prints ``message``, then ``hint`` on its own line, and exits with
  ``code``. No traceback unless ``--debug``;
* the **tray** shows a notification with ``message``;
* the **window** answers the page ``{"ok": false, "detail": message,
  "hint": hint}``.

Anything that is *not* an :class:`UmmError` is a bug, and is treated as one:
:func:`report_unexpected` writes it to ``manager.log`` with its traceback and
hands back one generic sentence for the user, because an unexpected traceback
in a tray balloon helps nobody.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from .i18n import T
from .model import JsonDict

__all__ = [
    "EXIT_FAILED",
    "EXIT_NETWORK",
    "EXIT_NOT_IMPLEMENTED",
    "EXIT_NO_MATCH",
    "EXIT_OK",
    "EXIT_UNVERIFIED",
    "EXIT_UPDATES_AVAILABLE",
    "ConfigurationError",
    "NetworkError",
    "NoMatchError",
    "NotImplementedYetError",
    "OperationFailedError",
    "UmmError",
    "UnverifiedAssetError",
    "exit_code_for",
    "guarded",
    "report_unexpected",
]

EXIT_OK = 0
"""Everything the user asked for happened."""
EXIT_FAILED = 1
"""At least one operation failed."""
EXIT_NOT_IMPLEMENTED = 2
"""The command exists but does nothing yet (or no command was given)."""
EXIT_NO_MATCH = 3
"""No instance, profile or executable matched what was asked for."""
EXIT_NETWORK = 3
"""GitHub could not be reached, or refused the request (rate limit)."""
EXIT_UNVERIFIED = 4
"""An update was skipped because its asset has no published sha256."""
EXIT_UPDATES_AVAILABLE = 10
"""``umm check`` only: there is something to update."""


class UmmError(Exception):
    """Base class: a failure the user is meant to read, not debug.

    Subclasses set :attr:`code`; instances may override it, which is what makes
    "this particular network failure must exit 1, not 3" expressible without a
    new class.
    """

    code: int = EXIT_FAILED

    def __init__(self, message: str, *, hint: str | None = None, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code

    def __str__(self) -> str:
        return self.message

    @property
    def lines(self) -> list[str]:
        """``message``, then ``hint`` when there is one: what the CLI prints."""
        return [self.message] if self.hint is None else [self.message, self.hint]

    def to_dict(self) -> JsonDict:
        """What the window hands to the page."""
        return {"ok": False, "detail": self.message, "hint": self.hint, "code": self.code}


class OperationFailedError(UmmError):
    """A stop, start, update or write that did not succeed. Exit 1."""

    code = EXIT_FAILED


class NotImplementedYetError(UmmError):
    """A command that exists but does nothing yet. Exit 2."""

    code = EXIT_NOT_IMPLEMENTED


class NoMatchError(UmmError):
    """Nothing matched the selectors: no instance, no profile, no kind. Exit 3."""

    code = EXIT_NO_MATCH


class NetworkError(UmmError):
    """GitHub is unreachable or refused the request. Exit 3."""

    code = EXIT_NETWORK


class UnverifiedAssetError(UmmError):
    """The release publishes no sha256 and ``--yes`` was not told to trust it.

    Exit 4, which is what the scheduled job of PLAN.md section 9.10 looks for:
    "nothing was installed, and on purpose".
    """

    code = EXIT_UNVERIFIED


class ConfigurationError(UmmError):
    """A settings file, a profile or a kind entry that cannot be used. Exit 1."""

    code = EXIT_FAILED


def exit_code_for(error: BaseException) -> int:
    """The exit code an exception maps to.

    An :class:`UmmError` knows its own; a ``GitHubError`` (raised by the client,
    which has no reason to import this module) maps to the network code; every
    other exception is a bug and maps to :data:`EXIT_FAILED`.
    """
    if isinstance(error, UmmError):
        return error.code
    from .github import GitHubError  # noqa: PLC0415 - avoids a cycle at import time

    if isinstance(error, GitHubError):
        return EXIT_NETWORK
    return EXIT_FAILED


def report_unexpected(error: BaseException, *, context: str = "") -> str:
    """Log an unexpected exception with its traceback; return one line for the user.

    The traceback goes to ``manager.log`` (``diag.get_logger``), never to the
    user: a balloon or a JSON answer full of frames is noise. The returned
    sentence names the log file, which is the only thing worth telling them.
    """
    from . import diag  # noqa: PLC0415 - avoids a cycle at import time

    logger = diag.get_logger(context or "error")
    logger.error("unexpected %s: %s", type(error).__name__, error, exc_info=error)
    return T["error.unexpected"]


def guarded[**P](func: Callable[P, JsonDict]) -> Callable[P, JsonDict]:
    """Wrap a window API method so the page always gets an answer, never a throw.

    An :class:`UmmError` becomes its own ``{"ok": false, "detail", "hint"}``
    payload; anything else is a bug, so it is logged with its traceback and the
    page is told the one generic sentence. Without this the pywebview bridge
    would reject the promise and the page would only know that "something"
    failed.
    """

    @functools.wraps(func)
    def inner(*args: P.args, **kwargs: P.kwargs) -> JsonDict:
        try:
            return func(*args, **kwargs)
        except UmmError as error:
            return error.to_dict()
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            return {"ok": False, "detail": report_unexpected(error, context="window"), "hint": None}

    return inner
