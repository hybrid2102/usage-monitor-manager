"""Tests for the global hotkey of PLAN.md section 9.14.

Parsing, formatting and normalization are pure and get a table each. The
listener is driven end to end - registration, one delivered ``WM_HOTKEY``, the
"already registered" conflict, and the clean unregistration - against a fake
``user32``: no combination is ever grabbed on the machine running the suite,
which is the whole reason the Windows entry points are injectable.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only platform layer")

from usage_monitor_manager.platform import win32  # noqa: E402 - after the platform skip

VK_U = 0x55


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "modifiers", "virtual_key"),
    [
        ("ctrl+alt+u", win32.MOD_CONTROL | win32.MOD_ALT, VK_U),
        ("CTRL+ALT+U", win32.MOD_CONTROL | win32.MOD_ALT, VK_U),
        ("  ctrl + alt + u ", win32.MOD_CONTROL | win32.MOD_ALT, VK_U),
        ("ctrl-alt-u", win32.MOD_CONTROL | win32.MOD_ALT, VK_U),
        ("u+alt+ctrl", win32.MOD_CONTROL | win32.MOD_ALT, VK_U),
        ("control+u", win32.MOD_CONTROL, VK_U),
        ("win+u", win32.MOD_WIN, VK_U),
        ("super+u", win32.MOD_WIN, VK_U),
        ("meta+u", win32.MOD_WIN, VK_U),
        ("cmd+u", win32.MOD_WIN, VK_U),
        ("shift+ctrl+7", win32.MOD_SHIFT | win32.MOD_CONTROL, ord("7")),
        ("ctrl+f1", win32.MOD_CONTROL, 0x70),
        ("ctrl+f24", win32.MOD_CONTROL, 0x87),
        ("alt+space", win32.MOD_ALT, 0x20),
        ("alt+esc", win32.MOD_ALT, 0x1B),
        ("alt+escape", win32.MOD_ALT, 0x1B),
        ("ctrl+pgup", win32.MOD_CONTROL, 0x21),
        ("ctrl+delete", win32.MOD_CONTROL, 0x2E),
        ("ctrl+del", win32.MOD_CONTROL, 0x2E),
        ("ctrl+shift+enter", win32.MOD_CONTROL | win32.MOD_SHIFT, 0x0D),
    ],
)
def test_parse_hotkey(text: str, modifiers: int, virtual_key: int) -> None:
    assert win32.parse_hotkey(text) == (modifiers, virtual_key)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "empty hotkey"),
        ("   ", "empty hotkey"),
        ("+", "empty hotkey"),
        ("ctrl", "no key, only modifiers"),
        ("ctrl+alt", "no key, only modifiers"),
        ("u", "needs at least one modifier"),
        ("f5", "needs at least one modifier"),
        ("ctrl+u+i", "names two keys"),
        ("ctrl+f25", "unknown key"),
        ("ctrl+f0", "unknown key"),
        ("ctrl+nope", "unknown key"),
    ],
)
def test_parse_hotkey_refuses(text: str, needle: str) -> None:
    with pytest.raises(win32.HotkeyError, match=needle):
        win32.parse_hotkey(text)


def test_a_bare_key_is_refused_because_windows_would_swallow_it() -> None:
    """``RegisterHotKey`` with no modifier steals the key system-wide."""
    with pytest.raises(win32.HotkeyError):
        win32.parse_hotkey("u")


# --------------------------------------------------------------------------- #
# Formatting and normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("modifiers", "virtual_key", "text"),
    [
        (win32.MOD_CONTROL | win32.MOD_ALT, VK_U, "ctrl+alt+u"),
        (win32.MOD_ALT | win32.MOD_CONTROL, VK_U, "ctrl+alt+u"),
        (win32.MOD_WIN | win32.MOD_SHIFT, VK_U, "shift+win+u"),
        (win32.MOD_CONTROL, 0x70, "ctrl+f1"),
        (win32.MOD_CONTROL, 0x87, "ctrl+f24"),
        (win32.MOD_ALT, 0x20, "alt+space"),
        (win32.MOD_ALT, 0x1B, "alt+esc"),
        (win32.MOD_CONTROL, 0x2E, "ctrl+delete"),
        (win32.MOD_CONTROL, ord("7"), "ctrl+7"),
    ],
)
def test_format_hotkey_is_canonical(modifiers: int, virtual_key: int, text: str) -> None:
    assert win32.format_hotkey(modifiers, virtual_key) == text


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("CTRL+ALT+U", "ctrl+alt+u"),
        ("alt + ctrl + u", "ctrl+alt+u"),
        ("u-ALT-ctrl", "ctrl+alt+u"),
        ("super+shift+U", "shift+win+u"),
        ("control+ESCAPE", "ctrl+esc"),
        ("ctrl+PgUp", "ctrl+pageup"),
    ],
)
def test_normalize_hotkey_gives_one_spelling(text: str, normalized: str) -> None:
    assert win32.normalize_hotkey(text) == normalized
    assert win32.normalize_hotkey(normalized) == normalized, "normalization is idempotent"


def test_normalize_hotkey_propagates_the_error() -> None:
    with pytest.raises(win32.HotkeyError, match="unknown key"):
        win32.normalize_hotkey("ctrl+nope")


# --------------------------------------------------------------------------- #
# The listener, against a fake user32
# --------------------------------------------------------------------------- #


class FakeUser32:
    """Just enough of ``user32`` to drive the listener's whole lifecycle."""

    def __init__(self, *, register_ok: bool = True) -> None:
        self.register_ok = register_ok
        self.registered: list[tuple[int, int, int]] = []
        self.unregistered: list[int] = []
        self.pending: list[int] = []
        self.posted: list[tuple[int, int]] = []
        self.quit = threading.Event()
        self.ready = threading.Event()

    def RegisterHotKey(self, window: object, hotkey_id: int, modifiers: int, virtual_key: int) -> int:  # noqa: N802
        if not self.register_ok:
            return 0
        self.registered.append((hotkey_id, modifiers, virtual_key))
        return 1

    def UnregisterHotKey(self, window: object, hotkey_id: int) -> int:  # noqa: N802
        self.unregistered.append(hotkey_id)
        return 1

    def GetMessageW(self, message_ref: Any, window: object, low: int, high: int) -> int:  # noqa: N802
        self.ready.set()
        while True:
            if self.pending:
                message_ref._obj.message = self.pending.pop(0)  # noqa: SLF001 - byref of our own MSG
                return 1
            if self.quit.wait(0.01):
                return 0

    def PostThreadMessageW(self, thread_id: int, message: int, wparam: int, lparam: int) -> int:  # noqa: N802
        self.posted.append((thread_id, message))
        if message == win32.WM_QUIT:
            self.quit.set()
        return 1


class FakeKernel32:
    def GetCurrentThreadId(self) -> int:  # noqa: N802
        return 4321


def _listener(user32: FakeUser32, callback: Any, *, last_error: int = 0) -> win32.HotkeyListener:
    return win32.HotkeyListener(
        "ctrl+alt+u",
        callback,
        user32=user32,
        kernel32=FakeKernel32(),
        get_last_error=lambda: last_error,
    )


def test_the_listener_parses_its_combination_up_front() -> None:
    listener = _listener(FakeUser32(), lambda: None)
    assert (listener.modifiers, listener.virtual_key) == (win32.MOD_CONTROL | win32.MOD_ALT, VK_U)
    with pytest.raises(win32.HotkeyError):
        win32.HotkeyListener("nope", lambda: None, user32=FakeUser32(), kernel32=FakeKernel32())


def test_register_pump_deliver_and_unregister() -> None:
    user32 = FakeUser32()
    fired = threading.Event()
    listener = _listener(user32, fired.set)

    assert listener.start(timeout=5.0) is True
    assert listener.registered is True
    assert listener.error is None
    hotkey_id, modifiers, virtual_key = user32.registered[0]
    assert hotkey_id == win32.HOTKEY_ID
    assert modifiers == win32.MOD_CONTROL | win32.MOD_ALT | win32.MOD_NOREPEAT
    assert virtual_key == VK_U

    assert user32.ready.wait(5.0), "the message loop must be pumping"
    user32.pending.append(win32.WM_HOTKEY)
    assert fired.wait(5.0), "WM_HOTKEY must reach the callback"

    listener.stop(timeout=5.0)
    assert user32.posted == [(4321, win32.WM_QUIT)]
    assert user32.unregistered == [win32.HOTKEY_ID]
    assert listener.registered is False


def test_an_unrelated_message_does_not_fire_the_callback() -> None:
    user32 = FakeUser32()
    calls: list[int] = []
    listener = _listener(user32, lambda: calls.append(1))
    assert listener.start(timeout=5.0) is True
    assert user32.ready.wait(5.0)

    user32.pending.append(win32.WM_NULL)
    listener.stop(timeout=5.0)
    assert calls == []


def test_a_conflict_is_reported_in_plain_words() -> None:
    user32 = FakeUser32(register_ok=False)
    listener = _listener(user32, lambda: None, last_error=win32.ERROR_HOTKEY_ALREADY_REGISTERED)

    assert listener.start(timeout=5.0) is False
    assert listener.registered is False
    assert listener.error is not None
    assert "ctrl+alt+u" in listener.error
    assert "already registered by another application" in listener.error
    assert user32.unregistered == [], "nothing was registered, so nothing is released"


def test_another_windows_error_carries_its_code() -> None:
    listener = _listener(FakeUser32(register_ok=False), lambda: None, last_error=87)
    assert listener.start(timeout=5.0) is False
    assert listener.error == "cannot register ctrl+alt+u (Windows error 87)"


def test_a_raising_callback_does_not_kill_the_thread() -> None:
    user32 = FakeUser32()
    calls: list[int] = []

    def bad() -> None:
        calls.append(1)
        raise RuntimeError("the window is gone")

    listener = _listener(user32, bad)
    assert listener.start(timeout=5.0) is True
    assert user32.ready.wait(5.0)

    user32.pending.append(win32.WM_HOTKEY)
    for _ in range(500):
        if calls:
            break
        threading.Event().wait(0.01)
    user32.pending.append(win32.WM_HOTKEY)
    for _ in range(500):
        if len(calls) > 1:
            break
        threading.Event().wait(0.01)

    assert len(calls) >= 2, "the loop survived the first exception"
    listener.stop(timeout=5.0)
    assert user32.unregistered == [win32.HOTKEY_ID]


def test_start_twice_is_a_no_op() -> None:
    user32 = FakeUser32()
    listener = _listener(user32, lambda: None)
    assert listener.start(timeout=5.0) is True
    assert listener.start(timeout=5.0) is True
    assert len(user32.registered) == 1
    listener.stop(timeout=5.0)


def test_stop_without_start_does_nothing() -> None:
    user32 = FakeUser32()
    _listener(user32, lambda: None).stop(timeout=1.0)
    assert user32.posted == []
    assert user32.unregistered == []


def test_the_message_struct_is_the_windows_one() -> None:
    """Guard against the MSG/byref wiring silently changing shape."""
    assert hasattr(ctypes.wintypes.MSG(), "message")  # pyright: ignore[reportAttributeAccessIssue]
