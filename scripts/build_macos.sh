#!/usr/bin/env bash
# Build Awake.app and Awake-<version>.dmg on macOS.
#   scripts/build_macos.sh            # uses ./.venv (created if missing)
# Output: dist/Awake.app, dist/Awake-<version>-macOS-<arch>.dmg
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "build_macos.sh must run on macOS." >&2
  exit 1
fi

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3.12}"

if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-dev.txt

VERSION="$(.venv/bin/python -c 'import awake; print(awake.__version__)')"
ARCH="$(uname -m)"

echo "==> Tests"
.venv/bin/python -m pytest -q -m "not native"

echo "==> Icons"
.venv/bin/python scripts/make_icons.py

echo "==> PyInstaller"
rm -rf build/awake_macos dist/Awake.app dist/Awake
.venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/awake_macos.spec

echo "==> DMG"
STAGING="$(mktemp -d)"
cp -R dist/Awake.app "$STAGING/"
ln -s /Applications "$STAGING/Applications"
DMG="dist/Awake-${VERSION}-macOS-${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "Awake ${VERSION}" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGING"

echo "==> Done"
du -sh dist/Awake.app "$DMG"
echo "The app is ad-hoc signed only. See docs/RELEASING.md for Developer ID signing and notarization."
