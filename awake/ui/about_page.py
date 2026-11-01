"""About page."""

from __future__ import annotations

import platform

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from awake import APP_NAME, APP_TAGLINE, LICENSE_NAME, REPOSITORY_URL, __version__
from awake.ui import icons
from awake.ui.components.common import Card, Divider, label
from awake.ui.settings_page import setting_row


class AboutPage(QWidget):
    def __init__(self, power_backend: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("page", True)
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 40, 32, 32)
        root.setSpacing(0)

        column = QVBoxLayout()
        column.setSpacing(0)
        column.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(72))
        column.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(16)
        name = label(APP_NAME, "title")
        column.addWidget(name, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(4)
        column.addWidget(label(APP_TAGLINE, "subtitle"), 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(12)
        version = label(f"Version {__version__}")
        version.setProperty("badge", "active")
        column.addWidget(version, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(20)
        blurb = label(
            "Awake keeps your computer from going to sleep while you work — for a set time or until "
            "you stop it. Keep the system awake while the display turns off, or keep both on. "
            "Everything stays on this computer.",
            "body",
            wrap=True,
        )
        blurb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        blurb.setFixedWidth(480)
        column.addWidget(blurb, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(28)

        card = Card(padding=12, spacing=0)
        card.setMaximumWidth(560)
        card.setMinimumWidth(460)
        github = QPushButton("Open on GitHub")
        github.setProperty("variant", "link")
        github.setCursor(Qt.CursorShape.PointingHandCursor)
        github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
        rows = [
            setting_row("Source code", REPOSITORY_URL, github),
            setting_row("License", "Free and open source.", label(LICENSE_NAME, "caption")),
            setting_row("Built with", f"Python {platform.python_version()} · Qt {qVersion()} · "
                        f"Qt for Python {pyside_version} (LGPLv3)", None),
            setting_row("Icons", "Lucide — ISC License", None),
            setting_row("Power backend", power_backend, None),
        ]
        for index, row in enumerate(rows):
            if index:
                card.body.addWidget(Divider())
            card.body.addWidget(row)
        column.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)

        root.addLayout(column)
        root.addStretch(1)
