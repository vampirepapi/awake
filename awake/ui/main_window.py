"""Main window: sidebar navigation + stacked pages."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from awake import APP_NAME, __version__
from awake.core.application_state import ApplicationState
from awake.database.repository import SessionRepository
from awake.ui import icons
from awake.ui.about_page import AboutPage
from awake.ui.components.common import label
from awake.ui.components.status_indicator import StatusDot
from awake.ui.dashboard import Dashboard
from awake.ui.formatting import format_duration
from awake.ui.sessions_page import SessionsPage
from awake.ui.settings_page import SettingsPage
from awake.ui.styles.theme import theme, theme_manager

PAGES = (("home", "Home", "house"), ("sessions", "Sessions", "history"),
         ("settings", "Settings", "settings"), ("about", "About", "info"))


class Sidebar(QFrame):
    def __init__(self, on_select: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(212)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(28))
        brand.addWidget(logo)
        name = QLabel(APP_NAME)
        name.setObjectName("BrandName")
        brand.addWidget(name)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(24)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in PAGES:
            button = QPushButton(f"  {text}")
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(16, 16))
            button.setProperty("iconName", icon_name)
            button.clicked.connect(lambda _=False, k=key: on_select(k))
            self._group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)

        status = QHBoxLayout()
        status.setContentsMargins(4, 0, 0, 0)
        status.setSpacing(2)
        self.status_dot = StatusDot(7)
        self.status_text = QLabel("Awake is off")
        self.status_text.setObjectName("SidebarStatus")
        status.addWidget(self.status_dot)
        status.addWidget(self.status_text, 1)
        layout.addLayout(status)
        layout.addSpacing(6)
        version = QLabel(f"Version {__version__}")
        version.setObjectName("SidebarVersion")
        version.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(version)

        self.refresh_icons()
        theme_manager().theme_changed.connect(self.refresh_icons)
        for button in self.buttons.values():
            button.toggled.connect(self.refresh_icons)

    def refresh_icons(self, *_: object) -> None:
        t = theme()
        for button in self.buttons.values():
            color = t.color("accent") if button.isChecked() else t.color("text_secondary")
            button.setIcon(icons.icon(button.property("iconName"), color, 16))

    def select(self, key: str) -> None:
        self.buttons[key].setChecked(True)


class MainWindow(QWidget):
    def __init__(
        self,
        state: ApplicationState,
        sessions_repo: SessionRepository,
        power_backend: str,
        tray_available: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1040, 740)
        self.setMinimumSize(920, 680)
        self._state = state
        self._tray_available = tray_available
        self._quitting = False
        self.on_hidden_to_tray: Callable[[], None] | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = Sidebar(self.show_page)
        layout.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")
        self.dashboard = Dashboard(state, sessions_repo)
        self.sessions_page = SessionsPage(sessions_repo)
        self.settings_page = SettingsPage(state.settings, power_backend)
        self.about_page = AboutPage(power_backend)
        self._page_index: dict[str, int] = {}
        for key, widget in (("home", self.dashboard), ("sessions", self.sessions_page),
                            ("settings", self.settings_page), ("about", self.about_page)):
            self._page_index[key] = self.pages.addWidget(widget)
        layout.addWidget(self.pages, 1)

        self.dashboard.view_all_requested.connect(lambda: self.show_page("sessions"))
        self.settings_page.clear_history_requested.connect(self.sessions_page.confirm_clear)
        self.sessions_page.history_cleared.connect(self.dashboard.reload_recent)
        state.sessions.session_started.connect(self._update_sidebar_status)
        state.sessions.session_changed.connect(self._update_sidebar_status)
        state.sessions.session_ended.connect(self._on_session_ended)

        # Sidebar status ticks once a minute — it shows minutes, not seconds.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(30_000)
        self._status_timer.timeout.connect(self._update_sidebar_status)

        QShortcut(QKeySequence.StandardKey.Close, self, activated=self.close)
        for index, (key, _, _) in enumerate(PAGES, start=1):
            QShortcut(QKeySequence(f"Ctrl+{index}"), self, activated=lambda k=key: self.show_page(k))

        self.sidebar.select("home")
        self._update_sidebar_status()

    # ------------------------------------------------------------------ navigation

    def show_page(self, key: str) -> None:
        index = self._page_index[key]
        self.sidebar.select(key)
        if self.pages.currentIndex() == index:
            return
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def bring_to_front(self, page: str | None = None) -> None:
        if page:
            self.show_page(page)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ status

    def _on_session_ended(self, _session) -> None:  # noqa: ANN001
        self._update_sidebar_status()
        if self.sessions_page.isVisible():
            self.sessions_page.reload()

    def _update_sidebar_status(self, *_: object) -> None:
        session = self._state.active_session
        if session is None:
            self.sidebar.status_dot.set_active(False)
            self.sidebar.status_text.setText("Awake is off")
            self._status_timer.stop()
            return
        self.sidebar.status_dot.set_active(True)
        if session.is_indefinite:
            self.sidebar.status_text.setText("On · indefinitely")
        else:
            left = session.remaining_seconds(self._state.sessions.now()) or 0
            self.sidebar.status_text.setText(f"On · {format_duration(max(60, left))} left")
        if not self._status_timer.isActive():
            self._status_timer.start()

    # ------------------------------------------------------------------ close / quit

    def prepare_quit(self) -> None:
        """Called before the application quits so closeEvent accepts."""
        self._quitting = True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        if self._state.settings.get("close_to_tray") and self._tray_available():
            event.ignore()
            self.hide()
            if self.on_hidden_to_tray:
                self.on_hidden_to_tray()
            return
        event.accept()
        from PySide6.QtWidgets import QApplication

        QApplication.instance().quit()

    def apply_theme(self, *_: object) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.instance().setStyleSheet(theme().stylesheet())
        self.update()
