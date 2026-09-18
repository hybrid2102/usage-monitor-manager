"""Translation catalogs (PLAN.md section 7, phase 8).

Same pattern as the monitors: one flat JSON file per language under
``locale/``, keys shaped ``area.what`` (``tray.quit``, ``cli.no_instances``),
and placeholders in ``{name}`` form. Nothing is compiled, nothing is generated:
a catalog is a file a human can read and a translator can diff.

Usage from Python::

    from .i18n import T, t

    print(T["tray.quit"])
    print(t("tray.update_manager_to", version="1.0.0"))

:data:`T` is a mapping that **never raises**: a key with no translation returns
the key itself, so a catalog that fell behind the code shows
``tray.something`` in the UI instead of taking the window down. The
corresponding test scans every ``T[...]`` and ``t("...")`` in the sources, so a
missing key is a red test long before it is a visible one.

The language comes from ``preferences.language``: ``it`` (the default), ``en``,
or ``auto`` to follow the system locale. The window's JavaScript gets the whole
catalog in one call - :meth:`JsApi.get_translations` - and does its own
substitution, so the page has no round trip per label.
"""

from __future__ import annotations

import json
import locale as _locale
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "T",
    "Catalog",
    "available_languages",
    "catalog",
    "language",
    "load_catalog",
    "locale_dir",
    "resolve_language",
    "set_language",
    "system_language",
    "t",
    "translations",
]

LANGUAGES: Final[tuple[str, ...]] = ("it", "en")
"""The catalogs that ship with the manager, in preference order."""

DEFAULT_LANGUAGE: Final[str] = "it"
"""What an unset, unknown or unreadable preference falls back to."""

_DIR_NAME = "locale"


def locale_dir() -> Path:
    """The directory holding the catalogs, frozen build included.

    Under PyInstaller the package is unpacked next to the bundled modules, so
    ``__file__`` points at the right place either way; the spec lists
    ``locale`` in its ``datas``.
    """
    return Path(__file__).resolve().parent / _DIR_NAME


def available_languages() -> tuple[str, ...]:
    """The languages a catalog actually exists for, in :data:`LANGUAGES` order."""
    directory = locale_dir()
    return tuple(code for code in LANGUAGES if (directory / f"{code}.json").is_file())


def load_catalog(code: str) -> dict[str, str]:
    """Read one catalog from disk; an unreadable or invalid file yields ``{}``.

    A broken catalog must not stop the manager from starting: every lookup
    then falls back to the default language and, failing that, to the key.
    """
    path = locale_dir() / f"{code}.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries: dict[str, str] = {}
    for key, value in raw.items():  # pyright: ignore[reportUnknownVariableType]
        if isinstance(key, str) and isinstance(value, str):
            entries[key] = value
    return entries


def system_language() -> str:
    """The language Windows is set to, reduced to a code we have, or the default."""
    candidates: list[str] = []
    try:
        current, _ = _locale.getlocale()
    except ValueError:  # pragma: no cover - malformed locale
        current = None
    if current:
        candidates.append(current)
    try:
        default = _locale.getdefaultlocale()[0]  # noqa: PLW1505 - deprecated but the only Windows answer
    except (AttributeError, ValueError):  # pragma: no cover - platform dependent
        default = None
    if default:
        candidates.append(default)
    for candidate in candidates:
        code = candidate.replace("-", "_").split("_")[0].casefold()
        if code in LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def resolve_language(preference: str | None) -> str:
    """Turn ``preferences.language`` into one of :data:`LANGUAGES`.

    ``auto`` follows the system locale; anything unknown (including ``None``)
    is the default. The result is always a language we have a catalog for.
    """
    value = (preference or "").strip().casefold()
    if value == "auto":
        value = system_language()
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


class Catalog(Mapping[str, str]):
    """One language, with the default language as its fallback.

    A :class:`Mapping`, so ``key in catalog`` and iteration work, but
    ``catalog[key]`` returns the key instead of raising: a label is never worth
    an exception.
    """

    def __init__(self, code: str) -> None:
        self.code = resolve_language(code)
        self._entries = load_catalog(self.code)
        self._fallback = load_catalog(DEFAULT_LANGUAGE) if self.code != DEFAULT_LANGUAGE else self._entries

    def __getitem__(self, key: str) -> str:
        found = self._entries.get(key)
        if found is not None:
            return found
        return self._fallback.get(key, key)

    def __iter__(self) -> Iterator[str]:
        return iter({**self._fallback, **self._entries})

    def __len__(self) -> int:
        return len({**self._fallback, **self._entries})

    def format(self, key: str, /, **values: object) -> str:
        """``self[key]`` with ``{placeholders}`` filled in.

        A placeholder the caller did not pass leaves the text unformatted
        rather than raising: a wrong label beats a crash in a tray callback.
        """
        text = self[key]
        if not values:
            return text
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError):
            return text

    def as_dict(self) -> dict[str, str]:
        """A plain dict of everything, for :meth:`JsApi.get_translations`."""
        return {**self._fallback, **self._entries}


_active: Catalog | None = None


def catalog() -> Catalog:
    """The active catalog, loading the default language on first use."""
    global _active  # noqa: PLW0603 - one active language per process
    if _active is None:
        _active = Catalog(DEFAULT_LANGUAGE)
    return _active


def set_language(preference: str | None) -> str:
    """Activate the language ``preference`` asks for; returns what was chosen.

    Called once by the CLI, the tray and the window with
    ``preferences.language``; changing the preference in the UI calls it again,
    and the tray menu and the page are rebuilt from the new catalog.
    """
    global _active  # noqa: PLW0603 - the setter of the above
    _active = Catalog(resolve_language(preference))
    return _active.code


def language() -> str:
    """The active language code."""
    return catalog().code


class _Proxy(Mapping[str, str]):
    """``T`` - the active catalog, resolved at every lookup.

    Modules do ``from .i18n import T`` at import time, which is before the
    language is known; the indirection means they still see the catalog that
    ``set_language`` installed later.
    """

    def __getitem__(self, key: str) -> str:
        return catalog()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(catalog())

    def __len__(self) -> int:
        return len(catalog())


T: Final[_Proxy] = _Proxy()
"""The active catalog: ``T["tray.quit"]``."""


def t(key: str, /, **values: object) -> str:
    """``T[key]`` with ``{placeholders}`` filled in."""
    return catalog().format(key, **values)


def translations(preference: str | None = None) -> dict[str, str]:
    """The whole catalog as a dict, for the page's own ``t()``.

    Without an argument it returns the active language; with one it loads that
    language without changing the active one (the settings page previewing a
    switch).
    """
    return (catalog() if preference is None else Catalog(resolve_language(preference))).as_dict()


def reset() -> None:
    """Forget the active catalog (tests, and a language change in the UI)."""
    global _active  # noqa: PLW0603 - see set_language
    _active = None
