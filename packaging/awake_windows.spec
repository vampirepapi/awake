# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Windows: produces dist\Awake\Awake.exe (one-folder build,
# which starts faster than one-file and is what the Inno Setup installer packages).
# Build with:  scripts\build_windows.bat   (must run on Windows)

import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
sys.path.insert(0, str(ROOT))
from awake import APP_NAME, __version__  # noqa: E402

numeric = tuple(int(p) for p in __version__.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numeric, prodvers=numeric),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "Awake contributors"),
                StringStruct("FileDescription", f"{APP_NAME} — keep your computer awake"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", APP_NAME),
                StringStruct("LegalCopyright", "Copyright (c) 2026 Awake contributors. MIT License."),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", __version__),
            ])
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "awake" / "assets"), "awake/assets"),
        (str(ROOT / "awake" / "ui" / "styles" / "awake.qss"), "awake/ui/styles"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=[],
    excludes=["tkinter", "PySide6.QtTest", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=str(ROOT / "awake" / "assets" / "icons" / "app" / "Awake.ico"),
    version=version_info,
    uac_admin=False,  # never needs administrator rights
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)  # noqa: F821
