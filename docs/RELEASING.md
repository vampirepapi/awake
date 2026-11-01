# Releasing

## Checklist

1. Bump `__version__` in `awake/__init__.py` (keep `BUNDLE_ID` and the `AppId` in `packaging/installer.iss` stable across releases).
2. `pytest` passes on both platforms, including `pytest -m native`.
3. Manual hardware checks in [TESTING.md](TESTING.md) done on macOS **and** Windows.
4. Build on each OS (`scripts/build_macos.sh`, `scripts\build_windows.bat`) or tag `vX.Y.Z` to let GitHub Actions build.
5. Sign and notarize (below), then publish.

## macOS code signing and notarization (required for distribution)

Unsigned or ad-hoc-signed apps downloaded from the internet are blocked by Gatekeeper. You need an Apple Developer ID.

```bash
# Sign (hardened runtime is required for notarization)
codesign --deep --force --options runtime --timestamp \
  --sign "Developer ID Application: <Name> (<TEAMID>)" dist/Awake.app

# Rebuild the DMG from the signed app, then sign the DMG
codesign --force --timestamp --sign "Developer ID Application: <Name> (<TEAMID>)" dist/Awake-X.Y.Z-macOS-arm64.dmg

# Notarize and staple
xcrun notarytool submit dist/Awake-X.Y.Z-macOS-arm64.dmg --keychain-profile "AC_NOTARY" --wait
xcrun stapler staple dist/Awake-X.Y.Z-macOS-arm64.dmg
```

PyInstaller can sign during the build via `codesign_identity` / `entitlements_file` in `packaging/awake_macos.spec`. Awake needs no special entitlements (IOKit power assertions are unrestricted). In CI, import the certificate into a temporary keychain from encrypted secrets.

Optional: a universal2 build needs a universal2 Python and universal2 wheels for every dependency; building separate arm64 and x86_64 DMGs (as the workflow does) is simpler.

## Windows code signing

Unsigned executables and installers trigger SmartScreen warnings. Sign both `dist\Awake\Awake.exe` (before running Inno Setup) and the installer:

```bat
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\Awake\Awake.exe
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\Awake-X.Y.Z-Setup.exe
```

An OV/EV certificate or Azure Trusted Signing is required.

## Qt / PySide6 licensing (LGPLv3)

Awake uses PySide6 under the LGPLv3. Verified from the installed package metadata (2026-11-01):

```
$ pip show PySide6-Essentials shiboken6 | grep -iE '^(name|version|license)'
Name: PySide6_Essentials
Version: 6.12.0
License: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only
Name: shiboken6
Version: 6.12.0
License: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only
```

Awake elects the LGPL-3.0 option. To comply when distributing binaries:

- Ship Qt/PySide6 as **dynamically linked, replaceable** libraries — the PyInstaller one-folder `.app` and `Awake\` layouts do this (do not switch to a static Qt build).
- Do not modify Qt/PySide6; if you do, publish those modifications.
- Include the license texts and a notice — `THIRD_PARTY_NOTICES.md` is bundled; keep it with every release.
- Provide or point to the Qt source (links are in `THIRD_PARTY_NOTICES.md`).
- Do not prohibit reverse engineering for debugging such modifications in your terms.

Using the commercial Qt license instead removes these obligations.
