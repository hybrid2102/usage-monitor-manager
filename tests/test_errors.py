"""Tests for the error hierarchy and its mapping onto exit codes (phase 8).

The mapping is the contract: the Task Scheduler job of PLAN.md section 9.10
reads the exit code and nothing else, so "this failure means 3, that one means
4" has to be a test and not a convention. The rest checks the three policies
that go with it: no traceback without ``--debug``, a hint printed on its own
line, and an unexpected exception logged with its traceback while the user sees
one sentence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from usage_monitor_manager import cli, diag, errors
from usage_monitor_manager.github import GitHubError, RateLimitedError


@pytest.fixture(autouse=True)
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return local


@pytest.fixture(autouse=True)
def quiet_logging() -> Iterator[None]:
    diag.reset_logging()
    yield
    diag.reset_logging()


# --------------------------------------------------------------------------- #
# The hierarchy
# --------------------------------------------------------------------------- #


def test_the_documented_exit_codes() -> None:
    assert (errors.EXIT_OK, errors.EXIT_FAILED, errors.EXIT_NOT_IMPLEMENTED) == (0, 1, 2)
    assert (errors.EXIT_NO_MATCH, errors.EXIT_NETWORK) == (3, 3)
    assert errors.EXIT_UNVERIFIED == 4
    assert errors.EXIT_UPDATES_AVAILABLE == 10
    # the CLI re-exports them, which is what every caller imports
    assert cli.EXIT_FAILED == errors.EXIT_FAILED
    assert cli.EXIT_NO_MATCH == errors.EXIT_NO_MATCH
    assert cli.EXIT_UNVERIFIED == errors.EXIT_UNVERIFIED
    assert cli.EXIT_UPDATES_AVAILABLE == errors.EXIT_UPDATES_AVAILABLE


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.UmmError("x"), errors.EXIT_FAILED),
        (errors.OperationFailedError("x"), errors.EXIT_FAILED),
        (errors.ConfigurationError("x"), errors.EXIT_FAILED),
        (errors.NotImplementedYetError("x"), errors.EXIT_NOT_IMPLEMENTED),
        (errors.NoMatchError("x"), errors.EXIT_NO_MATCH),
        (errors.NetworkError("x"), errors.EXIT_NETWORK),
        (errors.UnverifiedAssetError("x"), errors.EXIT_UNVERIFIED),
    ],
)
def test_every_class_maps_to_its_code(error: errors.UmmError, code: int) -> None:
    assert error.code == code
    assert errors.exit_code_for(error) == code


def test_an_instance_can_override_the_code() -> None:
    error = errors.NetworkError("x", code=errors.EXIT_FAILED)
    assert error.code == errors.EXIT_FAILED
    assert errors.NetworkError("y").code == errors.EXIT_NETWORK, "the class default is untouched"


def test_a_github_error_maps_to_the_network_code() -> None:
    assert errors.exit_code_for(GitHubError("boom")) == errors.EXIT_NETWORK
    assert errors.exit_code_for(RateLimitedError("rate limit", None)) == errors.EXIT_NETWORK


def test_anything_else_maps_to_one() -> None:
    assert errors.exit_code_for(ValueError("bug")) == errors.EXIT_FAILED
    assert errors.exit_code_for(OSError("bug")) == errors.EXIT_FAILED


def test_the_message_and_the_hint() -> None:
    bare = errors.NoMatchError("nessun profilo Lavoro")
    assert str(bare) == "nessun profilo Lavoro"
    assert bare.message == "nessun profilo Lavoro"
    assert bare.hint is None
    assert bare.lines == ["nessun profilo Lavoro"]

    with_hint = errors.NoMatchError("nessun profilo Lavoro", hint="usa 'umm profile list'")
    assert with_hint.lines == ["nessun profilo Lavoro", "usa 'umm profile list'"]
    assert with_hint.to_dict() == {
        "ok": False,
        "detail": "nessun profilo Lavoro",
        "hint": "usa 'umm profile list'",
        "code": errors.EXIT_NO_MATCH,
    }


def test_they_are_all_exceptions() -> None:
    for klass in (
        errors.OperationFailedError,
        errors.NotImplementedYetError,
        errors.NoMatchError,
        errors.NetworkError,
        errors.UnverifiedAssetError,
        errors.ConfigurationError,
    ):
        assert issubclass(klass, errors.UmmError)
        assert issubclass(klass, Exception)


# --------------------------------------------------------------------------- #
# report_unexpected
# --------------------------------------------------------------------------- #


def test_an_unexpected_exception_is_logged_with_its_traceback() -> None:
    diag.setup_manager_logging()
    try:
        raise ValueError("a bug nobody expected")
    except ValueError as error:
        message = errors.report_unexpected(error, context="test")
    for handler in logging.getLogger(diag.LOGGER_NAME).handlers:
        handler.flush()

    text = diag.manager_log_path().read_text(encoding="utf-8")
    assert "a bug nobody expected" in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError" in text
    assert "umm.test" in text
    assert "manager.log" in message
    assert "Traceback" not in message, "the user gets a sentence, not frames"


def test_report_unexpected_survives_an_unconfigured_logger() -> None:
    assert "manager.log" in errors.report_unexpected(ValueError("x"))


# --------------------------------------------------------------------------- #
# The CLI policy
# --------------------------------------------------------------------------- #


def _explode(error: BaseException) -> None:
    def dispatch(_argv: object = None) -> int:
        raise error

    return dispatch  # pyright: ignore[reportReturnType]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.OperationFailedError("lo stop non e' riuscito"), 1),
        (errors.NoMatchError("nessuna istanza"), 3),
        (errors.NetworkError("GitHub non risponde"), 3),
        (errors.UnverifiedAssetError("asset senza sha256"), 4),
        (errors.NotImplementedYetError("non ancora"), 2),
    ],
)
def test_main_maps_an_umm_error_to_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: errors.UmmError,
    code: int,
) -> None:
    monkeypatch.setattr(cli, "_dispatch", _explode(error))
    assert cli.main(["list"]) == code
    captured = capsys.readouterr()
    assert error.message in captured.err
    assert captured.out == "", "an error never pollutes stdout"
    assert "Traceback" not in captured.err


def test_main_prints_the_hint_on_its_own_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error = errors.NoMatchError("nessun profilo Lavoro", hint="usa 'umm profile list'")
    monkeypatch.setattr(cli, "_dispatch", _explode(error))
    assert cli.main(["profile", "apply", "Lavoro"]) == 3
    assert capsys.readouterr().err.splitlines() == ["nessun profilo Lavoro", "usa 'umm profile list'"]


def test_debug_lets_the_error_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_dispatch", _explode(errors.NoMatchError("x")))
    with pytest.raises(errors.NoMatchError):
        cli.main(["list", "--debug"])


def test_an_unexpected_exception_becomes_one_generic_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    diag.setup_manager_logging()
    monkeypatch.setattr(cli, "_dispatch", _explode(RuntimeError("boom")))
    assert cli.main(["list"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "boom" not in captured.err, "a bug's message is for the log, not for the user"
    assert "manager.log" in captured.err
    assert "--debug" in captured.err
    for handler in logging.getLogger(diag.LOGGER_NAME).handlers:
        handler.flush()
    assert "boom" in diag.manager_log_path().read_text(encoding="utf-8")


def test_debug_lets_an_unexpected_exception_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_dispatch", _explode(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        cli.main(["--debug", "list"])


def test_ctrl_c_is_not_a_crash(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_dispatch", _explode(KeyboardInterrupt()))
    assert cli.main(["list"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_the_debug_flag_is_a_real_option() -> None:
    args = cli.build_parser().parse_args(["--debug", "list"])
    assert args.debug is True
    assert cli.build_parser().parse_args(["list"]).debug is False


# --------------------------------------------------------------------------- #
# The window policy
# --------------------------------------------------------------------------- #


def test_guarded_turns_an_umm_error_into_a_payload() -> None:
    @errors.guarded
    def boom() -> dict[str, object]:
        raise errors.NoMatchError("nessuna istanza", hint="vedi 'umm list'")

    payload = boom()
    assert payload["ok"] is False
    assert payload["detail"] == "nessuna istanza"
    assert payload["hint"] == "vedi 'umm list'"
    assert payload["code"] == errors.EXIT_NO_MATCH


def test_guarded_logs_a_bug_and_answers_generically() -> None:
    diag.setup_manager_logging()

    @errors.guarded
    def boom() -> dict[str, object]:
        raise RuntimeError("window bug")

    payload = boom()
    assert payload["ok"] is False
    assert "manager.log" in str(payload["detail"])
    for handler in logging.getLogger(diag.LOGGER_NAME).handlers:
        handler.flush()
    assert "window bug" in diag.manager_log_path().read_text(encoding="utf-8")


def test_guarded_passes_a_success_through() -> None:
    @errors.guarded
    def fine(value: int) -> dict[str, object]:
        return {"ok": True, "value": value}

    assert fine(7) == {"ok": True, "value": 7}
    assert fine.__name__ == "fine", "the wrapper keeps the identity pywebview exposes"


def test_the_window_api_methods_that_change_things_are_guarded() -> None:
    from usage_monitor_manager.ui.window import JsApi

    for name in (
        "apply_update",
        "rollback",
        "save_settings",
        "set_github_token",
        "apply_self_update",
        "repair_instance",
        "apply_profile",
    ):
        method = getattr(JsApi, name)
        assert getattr(method, "__wrapped__", None) is not None, f"{name} is not @errors.guarded"
