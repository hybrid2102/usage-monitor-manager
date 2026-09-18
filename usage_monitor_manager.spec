# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for UsageMonitorManager.exe.

One file, no console, the ``ui/web`` assets and the ``locale`` catalogs
bundled as datas, and the excludes
the monitors also use so the executable stays around 15 MB instead of pulling
in the scientific stack. Run it through ``build.py``, which regenerates
``version_info.py`` and the icon first.
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)

datas = [
    (str(SPEC_DIR / "src" / "usage_monitor_manager" / "ui" / "web"), "usage_monitor_manager/ui/web"),
    # The translation catalogs (phase 8): plain JSON read at runtime through
    # `i18n.locale_dir()`, so PyInstaller has to be told about them by hand.
    (str(SPEC_DIR / "src" / "usage_monitor_manager" / "locale"), "usage_monitor_manager/locale"),
]

hiddenimports = [
    # pystray and pywebview pick their backend at runtime through importlib,
    # so the analysis never sees these two.
    "pystray._win32",
    "webview.platforms.edgechromium",
]

excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "test",
    "numpy",
    "pandas",
    "matplotlib",
    "scipy",
    "IPython",
    "setuptools",
    "pip",
    "sqlite3",
    "PIL.ImageQt",
    "PySide6",
    "PyQt5",
    "PyQt6",
]

a = Analysis(
    # Not `usage_monitor_manager/__main__.py`: PyInstaller runs the entry
    # script as a top-level module, which breaks every relative import inside
    # the package. `umm_launcher.py` imports the package by its absolute name.
    [str(SPEC_DIR / "src" / "umm_launcher.py")],
    pathex=[str(SPEC_DIR / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="UsageMonitorManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "assets" / "UsageMonitorManager.ico"),
    version=str(SPEC_DIR / "version_info.py"),
)
