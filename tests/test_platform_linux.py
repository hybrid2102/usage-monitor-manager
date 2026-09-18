"""The Linux layer is a stub, and must stay an *honest* one (PLAN.md 9.12).

The test that matters is the last one: every primitive the dispatcher exposes
either raises ``NotImplementedError`` with the documented sentence, or is one
of the three whose trivial answer is genuinely correct on POSIX. A stub that
returned an empty dict instead would make the discovery find nothing and say
nothing, which is the failure mode this file exists to prevent.

The module is imported directly (not through the dispatcher), so these run on
Windows too.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from usage_monitor_manager.platform import linux

MESSAGE = "Linux: previsto, non implementato"

#: The primitives whose POSIX answer is the trivial one, with what it must be.
HONEST_ANSWERS = {
    "attach_parent_console": True,
    "no_window_popen_kwargs": {},
    "authenticode_status": "unavailable",
}


def test_the_message_says_planned_not_missing() -> None:
    with pytest.raises(NotImplementedError, match=MESSAGE) as caught:
        linux.read_run_entries()
    assert "9.12" in str(caught.value)
    assert "architecture.md" in str(caught.value)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("file_version", (Path("x"),)),
        ("file_product_name", (Path("x"),)),
        ("read_run_entries", ()),
        ("read_run_key", ("UsageMonitorForClaude",)),
        ("write_run_key", ("UsageMonitorForClaude", "cmd")),
        ("delete_run_key", ("UsageMonitorForClaude",)),
        ("manager_autostart_command", ()),
        ("get_manager_autostart", ()),
        ("set_manager_autostart", (True,)),
        ("sync_manager_autostart_path", ()),
        ("parse_run_command", ('"exe" --flag',)),
        ("read_shortcuts", ([Path("x.lnk")],)),
        ("mutex_is_held", ("name",)),
        ("hold_mutex", ("name",)),
    ],
)
def test_every_unimplemented_primitive_raises(name: str, args: tuple[object, ...]) -> None:
    with pytest.raises(NotImplementedError, match=MESSAGE):
        getattr(linux, name)(*args)


@pytest.mark.parametrize(("name", "expected"), sorted(HONEST_ANSWERS.items()))
def test_the_three_trivial_answers(name: str, expected: object) -> None:
    function = getattr(linux, name)
    result = function(Path("x")) if name == "authenticode_status" else function()
    assert result == expected


def test_the_hotkey_stub_refuses_instead_of_pretending() -> None:
    with pytest.raises(linux.HotkeyError):
        linux.parse_hotkey("ctrl+alt+u")
    with pytest.raises(linux.HotkeyError):
        linux.normalize_hotkey("ctrl+alt+u")
    assert linux.HOTKEY_MODIFIERS == {}

    listener = linux.HotkeyListener("ctrl+alt+u", lambda: None)
    assert listener.start() is False
    assert listener.registered is False
    assert listener.error is not None
    listener.stop()  # must not raise


def test_the_mutex_holder_holds_nothing() -> None:
    holder = linux.MutexHolder("name", 0)
    with holder as entered:
        assert entered is holder
    holder.release()  # must not raise


def test_the_signatures_match_the_windows_layer() -> None:
    """Same names, same parameters: the dispatcher must be able to swap them."""
    win32 = pytest.importorskip("usage_monitor_manager.platform.win32")
    for name in linux.__all__:
        assert hasattr(win32, name), f"{name} exists only in the Linux stub"
        theirs, ours = getattr(win32, name), getattr(linux, name)
        if not (inspect.isfunction(theirs) and inspect.isfunction(ours)):
            continue
        assert list(inspect.signature(ours).parameters) == list(inspect.signature(theirs).parameters), (
            f"{name}: the stub and the Windows layer disagree on the parameters"
        )


def test_nothing_in_the_stub_answers_plausibly_by_accident() -> None:
    """Every public callable either raises or is a documented trivial answer."""
    for name in linux.__all__:
        member = getattr(linux, name)
        if not inspect.isfunction(member) or name in HONEST_ANSWERS:
            continue
        source = inspect.getsource(member)
        assert "_unsupported()" in source, f"{name} returns something instead of refusing"
