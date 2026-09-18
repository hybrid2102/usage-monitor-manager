"""Windows primitives: PE file version, ``HKCU\\...\\Run`` entries, shortcuts, Popen flags.

Everything here is ctypes / winreg / subprocess: no pywin32 dependency (PLAN.md
section 4.2). The module is imported only when ``sys.platform == "win32"``; the
dispatcher in ``platform/__init__.py`` picks it.

Shortcut reading deserves a note. A ``.lnk`` is a binary shell-link structure
and resolving it properly means COM (``IShellLinkW``), which through raw ctypes
is a lot of fragile code for one legacy import path. We therefore shell out to
PowerShell's ``WScript.Shell`` **once** for the whole batch of shortcuts and
parse its JSON: one process per discovery pass, and the parsing rules stay
Microsoft's instead of ours.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import threading
import winreg
from collections.abc import Callable, Sequence
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

__all__ = [
    "ATTACH_PARENT_PROCESS",
    "HOTKEY_MODIFIERS",
    "HotkeyError",
    "HotkeyListener",
    "format_hotkey",
    "parse_hotkey",
    "CREATE_NO_WINDOW",
    "DEFAULT_RUN_ROOT",
    "DETACHED_PROCESS",
    "MANAGER_RUN_NAME",
    "RUN_KEY_PATH",
    "MutexHolder",
    "RegistryRoot",
    "attach_parent_console",
    "authenticode_status",
    "delete_run_key",
    "file_product_name",
    "file_version",
    "get_manager_autostart",
    "hold_mutex",
    "manager_autostart_command",
    "mutex_is_held",
    "no_window_popen_kwargs",
    "parse_run_command",
    "read_run_entries",
    "read_run_key",
    "read_shortcuts",
    "set_manager_autostart",
    "sync_manager_autostart_path",
    "write_run_key",
]

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

_DEFAULT_TRANSLATION = (0x0409, 0x04B0)
"""US English / Unicode: what the monitors' ``version_info.py`` files declare."""

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

_version_dll = ctypes.WinDLL("version.dll")

_version_dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
_version_dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
_version_dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
_version_dll.GetFileVersionInfoW.restype = wintypes.BOOL
_version_dll.VerQueryValueW.argtypes = [
    wintypes.LPCVOID,
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.LPVOID),
    ctypes.POINTER(wintypes.UINT),
]
_version_dll.VerQueryValueW.restype = wintypes.BOOL


class _FixedFileInfo(ctypes.Structure):
    """``VS_FIXEDFILEINFO``: the numeric version block always present in a PE."""

    _fields_ = (
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    )


def _version_block(path: Path | str) -> ctypes.Array[ctypes.c_char] | None:
    """Load the whole version resource of ``path`` into a buffer."""
    text = str(path)
    try:
        size = _version_dll.GetFileVersionInfoSizeW(text, None)
    except OSError:
        return None
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    try:
        ok = _version_dll.GetFileVersionInfoW(text, 0, size, buffer)
    except OSError:
        return None
    return buffer if ok else None


def _translations(block: ctypes.Array[ctypes.c_char]) -> list[tuple[int, int]]:
    """Language / code-page pairs declared by the resource, default last."""
    pointer = wintypes.LPVOID()
    length = wintypes.UINT()
    found: list[tuple[int, int]] = []
    if _version_dll.VerQueryValueW(block, r"\VarFileInfo\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        address = pointer.value
        count = length.value // 4  # puLen is in bytes here: two WORDs per pair
        if address is not None and count:
            words = (wintypes.WORD * (count * 2)).from_address(address)
            found = [(int(words[i * 2]), int(words[i * 2 + 1])) for i in range(count)]
    if _DEFAULT_TRANSLATION not in found:
        found.append(_DEFAULT_TRANSLATION)
    return found


def _string_value(block: ctypes.Array[ctypes.c_char], name: str) -> str | None:
    """Read one ``StringFileInfo`` entry, trying every declared translation."""
    for lang, codepage in _translations(block):
        sub_block = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{name}"
        pointer = wintypes.LPVOID()
        length = wintypes.UINT()
        if not _version_dll.VerQueryValueW(block, sub_block, ctypes.byref(pointer), ctypes.byref(length)):
            continue
        address = pointer.value
        if address is None or not length.value:
            continue
        value = ctypes.wstring_at(address, length.value).split("\0", 1)[0].strip()
        if value:
            return value
    return None


def _fixed_product_version(block: ctypes.Array[ctypes.c_char]) -> str | None:
    """Fallback: the four numbers of ``VS_FIXEDFILEINFO`` as ``a.b.c.d``."""
    pointer = wintypes.LPVOID()
    length = wintypes.UINT()
    if not _version_dll.VerQueryValueW(block, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    address = pointer.value
    if address is None or length.value < ctypes.sizeof(_FixedFileInfo):
        return None
    info = _FixedFileInfo.from_address(address)
    if info.dwSignature != 0xFEEF04BD:
        return None
    high, low = int(info.dwProductVersionMS), int(info.dwProductVersionLS)
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def file_version(path: Path | str) -> str | None:
    """Return the ``ProductVersion`` of a PE file, or ``None`` if it has none.

    Order: ``StringFileInfo\\ProductVersion`` (what the monitors set, e.g.
    ``1.22.0.0``), then ``FileVersion``, then the numeric ``VS_FIXEDFILEINFO``
    product version. Never raises: an unreadable or resource-less file yields
    ``None``.
    """
    block = _version_block(path)
    if block is None:
        return None
    string_version = _string_value(block, "ProductVersion") or _string_value(block, "FileVersion")
    return string_version or _fixed_product_version(block)


def file_product_name(path: Path | str) -> str | None:
    """Return the PE ``ProductName`` (``Usage Monitor for Claude``) or ``None``."""
    block = _version_block(path)
    return None if block is None else _string_value(block, "ProductName")


RegistryRoot = tuple[int, str]
"""``(hive, subkey)``: which key the autostart helpers read and write.

Every function below takes one as an optional argument so that tests can point
them at a scratch key (``HKCU\\Software\\UsageMonitorManagerTest\\Run``) instead
of the real ``HKCU\\...\\Run``, which a test run must never touch.
"""

DEFAULT_RUN_ROOT: RegistryRoot = (winreg.HKEY_CURRENT_USER, RUN_KEY_PATH)

MANAGER_RUN_NAME = "UsageMonitorManager"
"""The manager's own autostart value name (PLAN.md section 6.5)."""


def read_run_entries(root: RegistryRoot | None = None) -> dict[str, str]:
    """Every string value of ``HKCU\\...\\Run``, as ``{name: command}``."""
    hive, subkey = root or DEFAULT_RUN_ROOT
    entries: dict[str, str] = {}
    try:
        key = winreg.OpenKey(hive, subkey)
    except OSError:
        return entries
    with key:
        index = 0
        while True:
            try:
                name, value, value_type = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                entries[name] = value
    return entries


def read_run_key(name: str, root: RegistryRoot | None = None) -> str | None:
    """Read a single autostart entry, ``None`` when absent."""
    return read_run_entries(root).get(name)


def write_run_key(name: str, command: str, root: RegistryRoot | None = None) -> None:
    """Create or update an autostart entry."""
    hive, subkey = root or DEFAULT_RUN_ROOT
    with winreg.CreateKey(hive, subkey) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def delete_run_key(name: str, root: RegistryRoot | None = None) -> None:
    """Remove an autostart entry; a missing entry is not an error."""
    hive, subkey = root or DEFAULT_RUN_ROOT
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return


# --------------------------------------------------------------------------- #
# Autostart of the manager itself (PLAN.md section 6.5)
# --------------------------------------------------------------------------- #


def manager_autostart_command(exe_cmd: str | None = None) -> str:
    """The command the manager registers for its own autostart.

    Frozen (PyInstaller) the answer is ``"<exe>" tray``; from a source
    checkout it is ``"<python>" -m usage_monitor_manager tray``, which makes
    the toggle usable during development too. ``exe_cmd`` overrides everything
    (the UI passes it when it knows better, tests pass a fake path).

    PLAN.md section 6.5 writes this command as ``"<exe>" --tray``; the CLI that
    actually exists uses the ``tray`` subcommand, and accepts ``--tray`` as an
    alias so a value written by hand in the PLAN's form still starts the tray.
    """
    if exe_cmd:
        return exe_cmd
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" tray'
    return f'"{Path(sys.executable).resolve()}" -m usage_monitor_manager tray'


def get_manager_autostart(root: RegistryRoot | None = None) -> str | None:
    """The manager's autostart command, or ``None`` when autostart is off."""
    return read_run_key(MANAGER_RUN_NAME, root)


def set_manager_autostart(
    enabled: bool,
    exe_cmd: str | None = None,
    root: RegistryRoot | None = None,
) -> str | None:
    """Turn the manager's own autostart on or off; returns the stored command.

    Raises:
        OSError: when the registry refuses the write. The caller reports it: an
            autostart toggle is never worth crashing the tray for.
    """
    if not enabled:
        delete_run_key(MANAGER_RUN_NAME, root)
        return None
    command = manager_autostart_command(exe_cmd)
    write_run_key(MANAGER_RUN_NAME, command, root)
    return command


def sync_manager_autostart_path(
    exe_cmd: str | None = None,
    root: RegistryRoot | None = None,
) -> str | None:
    """Rewrite the autostart command when the executable has moved.

    The manager lives in OneDrive, which is free to relocate the folder; the
    monitors solve this with their own ``sync_autostart_path`` and so do we.
    Does nothing when autostart is off (it never *enables* it) and nothing when
    the stored command already matches. Returns the command in force, or
    ``None`` when autostart is off.
    """
    current = get_manager_autostart(root)
    if current is None:
        return None
    wanted = manager_autostart_command(exe_cmd)
    if current.strip().casefold() == wanted.strip().casefold():
        return current
    try:
        write_run_key(MANAGER_RUN_NAME, wanted, root)
    except OSError:
        return current
    return wanted


def _split_arguments(rest: str) -> list[str]:
    """Split the argument tail of a command line, keeping Windows paths intact."""
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in rest:
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char.isspace() and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def parse_run_command(value: str) -> tuple[Path | None, list[str]]:
    """Split a Run-key command into executable and arguments.

    Handles the quoted form the monitors write
    (``"C:\\Tools\\UsageMonitorForClaude.exe" --config-dir="C:\\Users\\Marco\\.claude-valeria"``)
    and unquoted commands whose path contains spaces, by extending the
    executable until it ends in ``.exe``.
    """
    command = value.strip()
    if not command:
        return None, []
    if command.startswith('"'):
        end = command.find('"', 1)
        if end == -1:
            return Path(command[1:]), []
        return Path(command[1:end]), _split_arguments(command[end + 1 :])

    tokens = command.split(" ")
    for index in range(len(tokens)):
        head = " ".join(tokens[: index + 1])
        if head.casefold().endswith(".exe"):
            return Path(head), _split_arguments(" ".join(tokens[index + 1 :]))
    return Path(tokens[0]), _split_arguments(" ".join(tokens[1:]))


_SHORTCUT_SCRIPT = """
$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject WScript.Shell
$out = @()
foreach ($p in $input) {
  if ([string]::IsNullOrWhiteSpace($p)) { continue }
  $lnk = $shell.CreateShortcut($p)
  $out += [pscustomobject]@{ Link = $p; Target = $lnk.TargetPath; Arguments = $lnk.Arguments }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


def read_shortcuts(paths: Sequence[Path]) -> dict[Path, tuple[Path | None, list[str]]]:
    """Resolve several ``.lnk`` files in one PowerShell round trip.

    Returns ``{lnk_path: (target, arguments)}``; shortcuts that cannot be read
    are simply missing from the result. Never raises.
    """
    if not paths:
        return {}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, paths travel through stdin
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _SHORTCUT_SCRIPT,
            ],
            input="\n".join(str(p) for p in paths),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            **no_window_popen_kwargs(),
        )
        payload = cast(object, json.loads(completed.stdout or "[]"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

    rows: list[object] = cast(list[object], payload) if isinstance(payload, list) else [payload]
    result: dict[Path, tuple[Path | None, list[str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = cast(dict[str, object], row)
        link = fields.get("Link")
        if not isinstance(link, str) or not link:
            continue
        target = fields.get("Target")
        arguments = fields.get("Arguments")
        result[Path(link)] = (
            Path(target) if isinstance(target, str) and target else None,
            _split_arguments(arguments) if isinstance(arguments, str) else [],
        )
    return result


ATTACH_PARENT_PROCESS = -1
"""``AttachConsole`` argument meaning "the console of the parent process"."""


def attach_parent_console() -> bool:
    """Reattach stdout/stderr to the console that launched a windowed build.

    ``UsageMonitorManager.exe`` is built with ``--noconsole`` because it is a
    tray application: Windows gives it no console, and everything the CLI
    prints would go nowhere. When it is nevertheless started **from** a console
    (``UsageMonitorManager.exe list``), ``AttachConsole(-1)`` borrows the
    parent's console and the standard streams are reopened onto ``CONOUT$`` /
    ``CONIN$``, so the packaged executable behaves exactly like ``umm``.

    Returns ``True`` when a console is now attached. Never raises: a build
    started from Explorer simply has no parent console and keeps its null
    streams.
    """
    if sys.platform != "win32":  # pragma: no cover - the dispatcher never gets here
        return False
    try:
        if not _kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
    except OSError:  # pragma: no cover - defensive
        return False
    for name, path, mode in (("stdout", "CONOUT$", "w"), ("stderr", "CONOUT$", "w"), ("stdin", "CONIN$", "r")):
        try:
            stream = open(path, mode, encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115, PTH123
        except OSError:  # pragma: no cover - a redirected stream stays as it is
            continue
        setattr(sys, name, stream)
    return True


def no_window_popen_kwargs() -> dict[str, Any]:
    """``Popen`` keyword arguments that keep any console window hidden."""
    if sys.platform != "win32":  # pragma: no cover - the dispatcher never gets here
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startupinfo}


# --------------------------------------------------------------------------- #
# Single-instance mutex probe
# --------------------------------------------------------------------------- #

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.AttachConsole.argtypes = [wintypes.DWORD]
_kernel32.AttachConsole.restype = wintypes.BOOL
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class MutexHolder:
    """A named Win32 mutex kept alive for the lifetime of this process.

    :func:`mutex_is_held` deliberately closes the handle it creates, so that it
    can probe the monitors' singletons without becoming one. The manager's
    *own* singleton needs the opposite: create the mutex and hold the handle
    until the process exits, so a second manager sees ``ERROR_ALREADY_EXISTS``.
    Usable as a context manager.
    """

    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self._handle: int | None = handle

    def release(self) -> None:
        """Close the handle; releasing twice is harmless."""
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> MutexHolder:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def hold_mutex(name: str) -> MutexHolder | None:
    """Acquire a named mutex and keep it; ``None`` when somebody already has it.

    This is the manager's single-instance guard
    (``UsageMonitorManager_SingleInstance``). The returned holder must stay
    referenced for as long as the process should stay unique.
    """
    if not name:
        return None
    ctypes.set_last_error(0)
    handle = _kernel32.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        return None
    if error == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return MutexHolder(name, handle)


def mutex_is_held(name: str) -> bool:
    """True when a named Win32 mutex already exists, i.e. an instance is alive.

    This is the same check the monitors themselves do for their singleton
    (``UsageMonitorForClaude_SingleInstance<suffix>``): ``CreateMutexW`` always
    succeeds when the name is free, and sets ``ERROR_ALREADY_EXISTS`` when
    somebody else owns it. The handle we may have just created is closed
    immediately, so probing never becomes holding.

    ``ERROR_ACCESS_DENIED`` means the object exists but belongs to a session or
    user we may not open: that is "held" as far as we are concerned. Any other
    failure is reported as free, because refusing to act on an unexplained
    error would make ``stop``/``start`` unusable.
    """
    if not name:
        return False
    ctypes.set_last_error(0)
    handle = _kernel32.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if handle:
        _kernel32.CloseHandle(handle)
        return error == ERROR_ALREADY_EXISTS
    return error == ERROR_ACCESS_DENIED


# --------------------------------------------------------------------------- #
# Authenticode (information only)
# --------------------------------------------------------------------------- #

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_IGNORE = 0
WTD_SAFER_FLAG = 0x00000100
TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001


class _Guid(ctypes.Structure):
    """``GUID``; built from the literal fields of the Authenticode action GUID."""

    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    )


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.POINTER(_Guid)),
    )


class _WintrustData(ctypes.Structure):
    _fields_ = (
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(_WintrustFileInfo)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
    )


def _generic_verify_guid() -> _Guid:
    """``WINTRUST_ACTION_GENERIC_VERIFY_V2`` = {00AAC56B-CD44-11D0-8CC2-00C04FC295EE}."""
    raw = (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
    tail = (ctypes.c_byte * 8)(*(byte - 256 if byte > 127 else byte for byte in raw))
    return _Guid(0x00AAC56B, 0xCD44, 0x11D0, tail)


def authenticode_status(path: Path | str) -> str:
    """Report the Authenticode state of a file: information, never a verdict.

    Returns ``"signed_valid"``, ``"not_signed"``, ``"invalid(<code>)"`` with the
    ``WinVerifyTrust`` HRESULT, or ``"unavailable"`` when the check itself could
    not run. The monitors publish unsigned builds (PLAN.md section 1.1), so a
    caller must never treat anything here as a reason to refuse an update: the
    sha256 digest is what actually guards integrity.
    """
    target = Path(path)
    if not target.exists():
        return "unavailable"
    try:
        wintrust = ctypes.WinDLL("wintrust.dll")
        wintrust.WinVerifyTrust.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Guid), wintypes.LPVOID]
        wintrust.WinVerifyTrust.restype = ctypes.c_long
    except OSError:  # pragma: no cover - Windows without wintrust (Nano Server)
        return "unavailable"

    file_info = _WintrustFileInfo(
        cbStruct=ctypes.sizeof(_WintrustFileInfo),
        pcwszFilePath=str(target),
        hFile=None,
        pgKnownSubject=None,
    )
    data = _WintrustData(
        cbStruct=ctypes.sizeof(_WintrustData),
        pPolicyCallbackData=None,
        pSIPClientData=None,
        dwUIChoice=WTD_UI_NONE,
        fdwRevocationChecks=WTD_REVOKE_NONE,
        dwUnionChoice=WTD_CHOICE_FILE,
        pFile=ctypes.pointer(file_info),
        dwStateAction=WTD_STATEACTION_IGNORE,
        hWVTStateData=None,
        pwszURLReference=None,
        dwProvFlags=WTD_SAFER_FLAG,
        dwUIContext=0,
    )
    guid = _generic_verify_guid()
    try:
        result = int(wintrust.WinVerifyTrust(None, ctypes.byref(guid), ctypes.byref(data)))
    except OSError:  # pragma: no cover - defensive
        return "unavailable"
    code = result & 0xFFFFFFFF
    if code == 0:
        return "signed_valid"
    if code == TRUST_E_NOSIGNATURE:
        return "not_signed"
    if code in (TRUST_E_SUBJECT_FORM_UNKNOWN, TRUST_E_PROVIDER_UNKNOWN):
        return "unavailable"
    return f"invalid(0x{code:08X})"


# --------------------------------------------------------------------------- #
# Global hotkey (PLAN.md section 9.14)
# --------------------------------------------------------------------------- #

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_NULL = 0x0000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0xA17E
"""Our hotkey id; unique inside this thread, which is all Windows requires."""

HOTKEY_MODIFIERS: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

_MODIFIER_ORDER = (("ctrl", MOD_CONTROL), ("alt", MOD_ALT), ("shift", MOD_SHIFT), ("win", MOD_WIN))

_NAMED_KEYS: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
}
_NAMED_BY_CODE = {
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x1B: "esc",
    0x20: "space",
    0x21: "pageup",
    0x22: "pagedown",
    0x23: "end",
    0x24: "home",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
    0x2D: "insert",
    0x2E: "delete",
}


class HotkeyError(RuntimeError):
    """The combination is unparsable, or Windows refused to register it."""


def parse_hotkey(text: str) -> tuple[int, int]:
    """``"ctrl+alt+u"`` -> ``(MOD_CONTROL | MOD_ALT, 0x55)``.

    Accepts any order and any spacing, ``ctrl``/``control``, ``win``/``super``/
    ``meta``/``cmd``, the letters, the digits, ``f1``..``f24`` and the named
    keys of :data:`_NAMED_KEYS`. At least one modifier is required: Windows
    would happily register a bare letter and swallow it system-wide.

    Raises:
        HotkeyError: on an empty, duplicated, modifier-less or unknown combo.
    """
    parts = [part.strip().casefold() for part in str(text).replace("-", "+").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise HotkeyError("empty hotkey")
    modifiers = 0
    key: str | None = None
    for part in parts:
        flag = HOTKEY_MODIFIERS.get(part)
        if flag is not None:
            modifiers |= flag
            continue
        if key is not None:
            raise HotkeyError(f"hotkey {text!r} names two keys ({key!r} and {part!r})")
        key = part
    if key is None:
        raise HotkeyError(f"hotkey {text!r} has no key, only modifiers")
    if not modifiers:
        raise HotkeyError(f"hotkey {text!r} needs at least one modifier (ctrl, alt, shift or win)")
    return modifiers, _virtual_key(key, text)


def _virtual_key(key: str, original: str) -> int:
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:  # noqa: PLR2004 - VK_F1..VK_F24
            return 0x70 + number - 1
    code = _NAMED_KEYS.get(key)
    if code is None:
        raise HotkeyError(f"hotkey {original!r}: unknown key {key!r}")
    return code


def format_hotkey(modifiers: int, virtual_key: int) -> str:
    """The canonical spelling of a parsed combo (``ctrl+alt+u``)."""
    parts = [name for name, flag in _MODIFIER_ORDER if modifiers & flag]
    named = _NAMED_BY_CODE.get(virtual_key)
    if named is not None:
        parts.append(named)
    elif 0x70 <= virtual_key <= 0x87:  # noqa: PLR2004 - VK_F1..VK_F24
        parts.append(f"f{virtual_key - 0x70 + 1}")
    else:
        parts.append(chr(virtual_key).lower())
    return "+".join(parts)


def normalize_hotkey(text: str) -> str:
    """Parse and re-format, so stored combos have one spelling.

    Raises:
        HotkeyError: see :func:`parse_hotkey`.
    """
    modifiers, virtual_key = parse_hotkey(text)
    return format_hotkey(modifiers, virtual_key)


class HotkeyListener:
    """A system-wide hotkey on a thread of its own, in pure ctypes.

    ``RegisterHotKey(None, id, modifiers, vk)`` binds the combination to the
    *calling thread*, and ``WM_HOTKEY`` is then delivered to that thread's
    message queue - which is why the registration, the ``GetMessageW`` loop and
    the unregistration all have to happen on the same thread, and why stopping
    means posting a message to it rather than setting a flag.

    The Windows entry points are injectable so the tests can drive the whole
    lifecycle (registration, a delivered hotkey, the conflict error and the
    clean unregistration) with a fake ``user32``, without ever grabbing a
    combination on the developer's machine.
    """

    def __init__(
        self,
        combo: str,
        callback: Callable[[], None],
        *,
        user32: Any | None = None,
        kernel32: Any | None = None,
        get_last_error: Callable[[], int] | None = None,
    ) -> None:
        self.combo = combo
        self.modifiers, self.virtual_key = parse_hotkey(combo)
        self._callback = callback
        self._user32 = user32 if user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = kernel32 if kernel32 is not None else _kernel32
        self._get_last_error = get_last_error if get_last_error is not None else (lambda: ctypes.get_last_error())
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._thread_id: int | None = None
        self.error: str | None = None
        self.registered = False

    # -- lifecycle -----------------------------------------------------------

    def start(self, timeout: float = 5.0) -> bool:
        """Run the loop on a daemon thread; returns whether it registered.

        On failure :attr:`error` carries the reason, which is what the UI
        shows next to the preference field (a combination another application
        already owns is the common case).
        """
        if self._thread is not None and self._thread.is_alive():
            return self.registered
        self._ready.clear()
        self.error = None
        self.registered = False
        thread = threading.Thread(target=self.run, name="umm-hotkey", daemon=True)
        self._thread = thread
        thread.start()
        self._ready.wait(timeout)
        return self.registered

    def run(self) -> None:
        """Register, pump messages until asked to stop, always unregister."""
        try:
            self._thread_id = int(self._kernel32.GetCurrentThreadId())
        except OSError as error:  # pragma: no cover - defensive
            self.error = str(error)
            self._ready.set()
            return
        if not self._user32.RegisterHotKey(None, HOTKEY_ID, self.modifiers | MOD_NOREPEAT, self.virtual_key):
            code = self._get_last_error()
            self.error = (
                f"the combination {self.combo} is already registered by another application"
                if code == ERROR_HOTKEY_ALREADY_REGISTERED
                else f"cannot register {self.combo} (Windows error {code})"
            )
            self._ready.set()
            return
        self.registered = True
        self._ready.set()
        try:
            self._pump()
        finally:
            self.registered = False
            try:
                self._user32.UnregisterHotKey(None, HOTKEY_ID)
            except OSError:  # pragma: no cover - the thread is going away anyway
                pass

    def _pump(self) -> None:
        message = wintypes.MSG()
        while True:
            result = int(self._user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result in (0, -1):
                return
            if int(message.message) == WM_HOTKEY:
                try:
                    self._callback()
                except Exception:  # noqa: BLE001 - a bad callback must not kill the thread
                    continue

    def stop(self, timeout: float = 2.0) -> None:
        """Post ``WM_QUIT`` to the loop and wait for the thread to finish."""
        thread = self._thread
        thread_id = self._thread_id
        if thread is None or not thread.is_alive() or thread_id is None:
            self._thread = None
            return
        try:
            self._user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        except OSError:  # pragma: no cover - defensive
            pass
        thread.join(timeout)
        self._thread = None
