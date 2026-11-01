"""Settings page: General, Power management, Notifications, Privacy and About."""

from __future__ import annotations

import logging
import platform
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from awake import LICENSE_NAME, REPOSITORY_URL, __version__
from awake.core.settings_manager import SettingsManager, StartupBehaviour
from awake.database.database import DatabaseError
from awake.logging_setup import kv
from awake.paths import app_data_dir
from awake.ui.components.common import Card, Divider, IconLabel, label
from awake.ui.components.power_toggle import ToggleSwitch
from awake.ui.dashboard import page_header
from awake.ui.formatting import DURATION_LABELS

log = logging.getLogger("awake.ui")


def setting_row(title: str, description: str, control: QWidget | None) -> QFrame:
    row = QFrame()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 10, 4, 10)
    layout.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(label(title, "rowTitle"))
    if description:
        text.addWidget(label(description, "rowDescription", wrap=True))
    layout.addLayout(text, 1)
    if control is not None:
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def info_check(text: str) -> QWidget:
    """A green check icon, used for privacy guarantees that are not toggles."""
    wrapper = QWidget()
    row = QHBoxLayout(wrapper)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(IconLabel("circle-check", 16, "success"))
    row.addWidget(label(text, "caption"))
    return wrapper


class SettingsSection(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = label(title.upper(), "section")
        heading.setContentsMargins(4, 0, 0, 0)
        layout.addWidget(heading)
        self.card = Card(padding=12, spacing=0)
        layout.addWidget(self.card)
        self._rows = 0

    def add(self, row: QWidget) -> QWidget:
        if self._rows:
            self.card.body.addWidget(Divider())
        self.card.body.addWidget(row)
        self._rows += 1
        return row


class SettingsPage(QWidget):
    clear_history_requested = Signal()

    def __init__(self, settings: SettingsManager, power_backend: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("page", True)
        self._settings = settings
        self._switches: dict[str, ToggleSwitch] = {}
        self._combos: dict[str, QComboBox] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(32, 28, 32, 32)
        root.setSpacing(24)
        header, _ = page_header("Settings", "Configure how Awake behaves on this computer.")
        root.addLayout(header)

        tray_word = "menu bar" if sys.platform == "darwin" else "system tray"

        general = SettingsSection("General")
        general.add(setting_row("Launch at startup", "Open Awake automatically when you sign in.",
                                self._switch("launch_at_startup")))
        general.add(setting_row("Start minimized", f"Start quietly in the {tray_word} without opening this window.",
                                self._switch("start_minimized")))
        general.add(setting_row("Minimize to tray instead of closing",
                                f"Closing the window keeps Awake running in the {tray_word}.",
                                self._switch("close_to_tray")))
        general.add(setting_row("Dark mode", "Use the dark appearance. Turn off for the light theme.",
                                self._switch("dark_mode")))
        if sys.platform == "darwin":
            general.add(setting_row("Show time left in the menu bar",
                                    "Display the countdown (or ∞) next to the menu-bar icon while Awake is on.",
                                    self._switch("show_menu_bar_timer")))
        root.addWidget(general)

        power = SettingsSection("Power management")
        power.add(setting_row("Default awake duration", "Pre-selected when Awake opens.",
                              self._combo("default_duration", [(DURATION_LABELS[d], d) for d in DURATION_LABELS])))
        power.add(setting_row("Keep system awake by default", "Prevent system sleep in new sessions.",
                              self._switch("keep_system_awake_default")))
        power.add(setting_row("Keep display awake by default",
                              "Off lets the screen turn off while background work continues.",
                              self._switch("keep_display_awake_default")))
        power.add(setting_row("When Awake starts", "What to do each time the app launches.",
                              self._combo("startup_behaviour", [
                                  ("Stay off", StartupBehaviour.STAY_OFF),
                                  ("Start a session", StartupBehaviour.START_SESSION),
                              ])))
        root.addWidget(power)

        agents = SettingsSection("AI agents")
        agents.add(setting_row(
            "Keep awake while AI agents work",
            "Detects when Claude Code is working and keeps the system awake until it finishes, then lets "
            "it sleep normally after a minute. The display can still turn off. Awake only reads each "
            "session's busy/idle status — never prompts, files or output.",
            self._switch("agent_detection_enabled")))
        agents.add(setting_row(
            "Pause on low battery",
            "Don't keep awake for agents while on battery below 20%.",
            self._switch("agent_battery_guard")))
        agents.add(setting_row(
            "Supported agents",
            "Claude Code (via its built-in status command). Codex, Cursor, OpenCode and Gemini CLI are planned.",
            None))
        root.addWidget(agents)

        notifications = SettingsSection("Notifications")
        notifications.add(setting_row("Session starts", "Notify when an awake session begins.",
                                      self._switch("notify_on_start")))
        notifications.add(setting_row("Session ends", "Notify when you stop a session.",
                                      self._switch("notify_on_stop")))
        notifications.add(setting_row("Session expires", "Notify when a timed session finishes.",
                                      self._switch("notify_on_expire")))
        root.addWidget(notifications)

        privacy = SettingsSection("Privacy")
        open_folder = QPushButton("Show folder")
        open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir()))))
        privacy.add(setting_row("Local-only storage",
                                f"Session history and settings are stored only on this computer in {app_data_dir()}.",
                                open_folder))
        privacy.add(setting_row("No account required", "Awake works fully offline. There is nothing to sign in to.",
                                info_check("Always")))
        privacy.add(setting_row("No telemetry", "Awake never sends usage data, analytics or crash reports.",
                                info_check("Never collected")))
        clear = QPushButton("Clear history…")
        clear.setProperty("variant", "danger")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear_history_requested)
        privacy.add(setting_row("Clear session history", "Permanently delete all finished sessions.", clear))
        root.addWidget(privacy)

        about = SettingsSection("About")
        about.add(setting_row("Version", "", label(f"Awake {__version__}", "caption")))
        about.add(setting_row("License", "", label(LICENSE_NAME, "caption")))
        repo_button = QPushButton("Open on GitHub")
        repo_button.setProperty("variant", "link")
        repo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        repo_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
        about.add(setting_row("Source code", REPOSITORY_URL, repo_button))
        about.add(setting_row(
            "Credits",
            f"Built with Qt for Python {pyside_version} (LGPLv3) on Python {platform.python_version()}. "
            f"Icons by Lucide (ISC). Power backend: {power_backend}.",
            None,
        ))
        root.addWidget(about)
        root.addStretch(1)

        settings.changed.connect(self._on_setting_changed)

    # ------------------------------------------------------------------ controls

    def _switch(self, key: str) -> ToggleSwitch:
        switch = ToggleSwitch()
        switch.set_checked_silently(bool(self._settings.get(key)))
        switch.clicked.connect(lambda checked, k=key: self._apply(k, checked))
        self._switches[key] = switch
        return switch

    def _combo(self, key: str, options: list[tuple[str, object]]) -> QComboBox:
        combo = QComboBox()
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for text, value in options:
            combo.addItem(text, value)
        combo.setCurrentIndex(max(0, combo.findData(self._settings.get(key))))
        combo.activated.connect(lambda index, k=key, c=combo: self._apply(k, c.itemData(index)))
        self._combos[key] = combo
        return combo

    def _apply(self, key: str, value: object) -> None:
        try:
            self._settings.set(key, value)
        except (ValueError, DatabaseError) as exc:
            log.error(kv("setting_save_failed", key=key, error=exc))
            QMessageBox.warning(self, "Settings", f"This setting could not be saved:\n{exc}")
            self._on_setting_changed(key, self._settings.get(key))

    def _on_setting_changed(self, key: str, value: object) -> None:
        if key in self._switches:
            self._switches[key].set_checked_silently(bool(value))
        elif key in self._combos:
            combo = self._combos[key]
            combo.setCurrentIndex(max(0, combo.findData(value)))
