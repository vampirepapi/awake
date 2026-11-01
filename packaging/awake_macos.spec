# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS: produces dist/Awake.app
# Build with:  scripts/build_macos.sh   (must run on macOS)

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
sys.path.insert(0, str(ROOT))
from awake import APP_NAME, BUNDLE_ID, __version__  # noqa: E402

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
    argv_emulation=False,
    target_arch=None,          # build for the host architecture (arm64 or x86_64)
    codesign_identity=None,    # ad-hoc signature; see docs/RELEASING.md for Developer ID signing
    entitlements_file=None,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)  # noqa: F821

app = BUNDLE(  # noqa: F821
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "awake" / "assets" / "icons" / "app" / "Awake.icns"),
    bundle_identifier=BUNDLE_ID,
    version=__version__,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "Copyright © 2026 Awake contributors. MIT License.",
    },
)
