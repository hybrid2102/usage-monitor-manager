"""Tests for the translation catalogs (phase 8).

Two of these tests are the ones that matter, and they are both static:

* the two catalogs must declare **exactly** the same keys, otherwise a
  language silently falls back for some labels and nobody notices;
* every key the sources look up must exist. ``T[...]`` returns the key instead
  of raising, which is right at runtime and useless as a warning, so the
  sources are scanned with a regex and the keys checked against the catalog.

The rest covers the lookup itself: the fallback chain, ``auto``, a broken
catalog, and the placeholders.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from usage_monitor_manager import i18n

SRC = Path(i18n.__file__).resolve().parent
TESTS = Path(__file__).resolve().parent

#: ``T["key"]``, ``T['key']``, ``t("key"``, ``t('key'`` in Python.
_PY_KEY = re.compile(r"""(?:\bT\[|\bt\()\s*["']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["']""")
#: ``t("key"`` / ``t('key'`` / ``t(`key`` in JavaScript.
_JS_KEY = re.compile(r"""\bt\(\s*["'`]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["'`]""")


@pytest.fixture(autouse=True)
def default_language() -> Iterator[None]:
    """Every test starts from the default catalog and leaves it that way."""
    i18n.reset()
    yield
    i18n.reset()


def _catalog_path(code: str) -> Path:
    return i18n.locale_dir() / f"{code}.json"


def _raw(code: str) -> dict[str, str]:
    payload: object = json.loads(_catalog_path(code).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return {str(key): str(value) for key, value in payload.items()}  # pyright: ignore[reportUnknownVariableType]


def _sources() -> Iterator[tuple[Path, str]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")
    for path in sorted(SRC.rglob("*.js")):
        yield path, path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The catalogs themselves
# --------------------------------------------------------------------------- #


def test_every_declared_language_has_a_catalog() -> None:
    assert i18n.LANGUAGES == ("it", "en")
    assert i18n.DEFAULT_LANGUAGE == "it"
    assert i18n.available_languages() == i18n.LANGUAGES
    for code in i18n.LANGUAGES:
        assert _catalog_path(code).is_file()


def test_the_catalogs_declare_the_same_keys() -> None:
    italian = set(_raw("it"))
    english = set(_raw("en"))
    assert italian, "the Italian catalog must not be empty"
    assert sorted(italian - english) == [], "keys missing from en.json"
    assert sorted(english - italian) == [], "keys missing from it.json"


def test_no_translation_is_empty() -> None:
    for code in i18n.LANGUAGES:
        empty = [key for key, value in _raw(code).items() if not value.strip()]
        assert empty == [], f"{code}.json has empty translations"


def test_the_two_catalogs_use_the_same_placeholders() -> None:
    placeholder = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
    italian, english = _raw("it"), _raw("en")
    for key, text in italian.items():
        assert set(placeholder.findall(text)) == set(placeholder.findall(english[key])), (
            f"{key}: the two languages do not fill in the same placeholders"
        )


def test_every_key_used_in_the_sources_exists() -> None:
    known = set(_raw(i18n.DEFAULT_LANGUAGE))
    missing: dict[str, list[str]] = {}
    seen = 0
    for path, text in _sources():
        pattern = _JS_KEY if path.suffix == ".js" else _PY_KEY
        for key in pattern.findall(text):
            seen += 1
            if key not in known:
                missing.setdefault(key, []).append(path.name)
    assert seen > 0, "the scan found no lookup at all: the regex is broken, not the code"
    assert missing == {}, f"keys used but not translated: {missing}"


def test_every_data_i18n_attribute_of_the_page_exists() -> None:
    """The static labels of index.html are keys too, just in the markup."""
    known = set(_raw(i18n.DEFAULT_LANGUAGE))
    html = (SRC / "ui" / "web" / "index.html").read_text(encoding="utf-8")
    keys = re.findall(r'data-i18n(?:-placeholder|-title)?="([^"]+)"', html)
    assert len(keys) > 50, "the page lost its data-i18n attributes"
    assert sorted({key for key in keys if key not in known}) == []


def test_the_page_has_a_translation_hook_for_every_tagged_element() -> None:
    """``applyTranslations`` must cover every attribute the markup uses."""
    script = (SRC / "ui" / "web" / "app.js").read_text(encoding="utf-8")
    for attribute in ("[data-i18n]", "[data-i18n-placeholder]", "[data-i18n-title]"):
        assert attribute in script, f"app.js never fills {attribute}"
    assert "get_translations" in script


def test_the_scan_would_notice_a_missing_key() -> None:
    """Guard for the guard: the regexes must match the shapes we write."""
    assert _PY_KEY.findall('print(T["tray.quit"])') == ["tray.quit"]
    assert _PY_KEY.findall("t('cli.no_match', n=1)") == ["cli.no_match"]
    assert _PY_KEY.findall('t(\n    "notify.update_body",\n    name=x,\n)') == ["notify.update_body"]
    assert _JS_KEY.findall("el.textContent = t('window.tab_manager');") == ["window.tab_manager"]
    assert _JS_KEY.findall("t(`window.saved`)") == ["window.saved"]
    assert _PY_KEY.findall('T["Not.A.Key"]') == []


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


def test_the_default_language_is_active_without_setup() -> None:
    assert i18n.language() == "it"
    assert T_of("tray.quit") == "Esci"


def T_of(key: str) -> str:  # noqa: N802 - reads as "T of key" at the call sites
    return i18n.T[key]


def test_switching_language() -> None:
    assert i18n.set_language("en") == "en"
    assert i18n.language() == "en"
    assert T_of("tray.quit") == "Quit"
    assert i18n.set_language("it") == "it"
    assert T_of("tray.quit") == "Esci"


@pytest.mark.parametrize(
    ("preference", "expected"),
    [("it", "it"), ("en", "en"), ("IT", "it"), ("  en  ", "en"), (None, "it"), ("", "it"), ("de", "it")],
)
def test_resolve_language(preference: str | None, expected: str) -> None:
    assert i18n.resolve_language(preference) == expected


def test_auto_follows_the_system_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.resolve_language("auto") == "en"
    assert i18n.set_language("auto") == "en"


def test_system_language_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n._locale, "getlocale", lambda: ("Japanese_Japan", "932"))  # noqa: SLF001
    monkeypatch.setattr(i18n._locale, "getdefaultlocale", lambda: ("ja_JP", "cp932"))  # noqa: SLF001
    assert i18n.system_language() == "it"


@pytest.mark.parametrize(("value", "expected"), [("it_IT", "it"), ("en-GB", "en"), ("English_United States", "it")])
def test_system_language_reads_the_locale_code(monkeypatch: pytest.MonkeyPatch, value: str, expected: str) -> None:
    monkeypatch.setattr(i18n._locale, "getlocale", lambda: (value, None))  # noqa: SLF001
    monkeypatch.setattr(i18n._locale, "getdefaultlocale", lambda: (None, None))  # noqa: SLF001
    assert i18n.system_language() == expected


def test_an_unknown_key_returns_itself() -> None:
    assert T_of("nope.not.here") == "nope.not.here"
    assert i18n.t("nope.not.here", name="x") == "nope.not.here"


def test_a_key_missing_from_a_language_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    real = i18n.load_catalog

    def partial(code: str) -> dict[str, str]:
        entries = real(code)
        if code == "en":
            entries.pop("tray.quit", None)
        return entries

    monkeypatch.setattr(i18n, "load_catalog", partial)
    i18n.set_language("en")
    assert T_of("tray.quit") == "Esci", "the Italian text is better than the raw key"
    assert T_of("tray.stop") == "Stop", "the rest of the language still applies"


def test_placeholders_are_filled_in() -> None:
    assert i18n.t("tray.update_to", version="1.0.0") == "Aggiorna a 1.0.0…"
    assert i18n.t("tray.tooltip_running", running=3, total=4) == "3/4 in esecuzione"


def test_a_missing_placeholder_leaves_the_text_alone() -> None:
    assert "{version}" in i18n.t("tray.update_to", wrong="x")
    assert i18n.t("tray.quit") == "Esci"


def test_a_broken_catalog_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "it.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "en.json").write_text('["a list, not an object"]', encoding="utf-8")
    monkeypatch.setattr(i18n, "locale_dir", lambda: tmp_path)
    assert i18n.load_catalog("it") == {}
    assert i18n.load_catalog("en") == {}
    assert i18n.load_catalog("missing") == {}
    i18n.set_language("it")
    assert T_of("tray.quit") == "tray.quit", "no catalog: the key is the label"


def test_non_string_entries_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "it.json").write_text('{"a.b": "ok", "a.c": 7, "a.d": null}', encoding="utf-8")
    monkeypatch.setattr(i18n, "locale_dir", lambda: tmp_path)
    assert i18n.load_catalog("it") == {"a.b": "ok"}


def test_the_catalog_is_a_mapping() -> None:
    entries = i18n.catalog()
    assert "tray.quit" in entries
    assert len(entries) == len(_raw("it"))
    assert dict(entries)["tray.quit"] == "Esci"


def test_translations_for_the_javascript_side() -> None:
    payload = i18n.translations()
    assert payload["tray.quit"] == "Esci"
    assert payload == _raw("it")
    assert i18n.translations("en")["tray.quit"] == "Quit"
    assert i18n.language() == "it", "asking for another language does not switch to it"


def test_the_locale_directory_travels_with_the_package() -> None:
    assert i18n.locale_dir() == SRC / "locale"
    assert i18n.locale_dir().is_dir()
