"""Generate application icons from the in-code logo.

Outputs (committed to the repo so each platform build can use them):
  awake/assets/icons/app/awake.png    1024px master
  awake/assets/icons/app/Awake.icns   macOS (requires `iconutil`, i.e. run on macOS)
  awake/assets/icons/app/Awake.ico    Windows (16–256 px)

Usage:  python scripts/make_icons.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

OUT = ROOT / "awake" / "assets" / "icons" / "app"


def png_bytes(size: int, app_icon_inset: bool) -> bytes:
    from awake.ui.icons import logo_image

    if app_icon_inset:
        # macOS icon grid: artwork occupies ~80% of the canvas with transparent margin.
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter

        canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        inner = round(size * 0.805)
        painter = QPainter(canvas)
        offset = (size - inner) // 2
        painter.drawImage(offset, offset, logo_image(inner))
        painter.end()
        image = canvas
    else:
        image = logo_image(size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, sizes: list[int]) -> None:
    """Write a PNG-compressed .ico (supported since Windows Vista)."""
    images = [png_bytes(s, app_icon_inset=False) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, data in zip(sizes, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        payload += data
    path.write_bytes(header + entries + payload)


def write_icns(path: Path) -> None:
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("iconutil not found (not macOS) — skipping .icns")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Awake.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            (iconset / f"icon_{base}x{base}.png").write_bytes(png_bytes(base, True))
            (iconset / f"icon_{base}x{base}@2x.png").write_bytes(png_bytes(base * 2, True))
        subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(path)], check=True)


def main() -> None:
    app = QGuiApplication([])  # noqa: F841 - needed for QPainter/SVG
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "awake.png").write_bytes(png_bytes(1024, app_icon_inset=False))
    write_ico(OUT / "Awake.ico", [16, 24, 32, 48, 64, 128, 256])
    write_icns(OUT / "Awake.icns")
    for item in sorted(OUT.iterdir()):
        print(f"{item.relative_to(ROOT)}  {item.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
