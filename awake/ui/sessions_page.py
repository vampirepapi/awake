"""Session history page: sortable table, empty state and clear-history action."""

from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from awake.database.database import DatabaseError
from awake.database.models import Session, SessionStatus
from awake.database.repository import SessionRepository
from awake.logging_setup import kv
from awake.ui.components.common import Card, IconLabel, label
from awake.ui.dashboard import page_header
from awake.ui.formatting import STATUS_LABELS, format_datetime, format_duration, format_planned
from awake.ui.styles.theme import theme

log = logging.getLogger("awake.ui")

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
COLUMNS = ("Started", "Ended", "Awake for", "Type", "Status")
_STATUS_COLORS = {
    SessionStatus.COMPLETED: ("success", "success_soft"),
    SessionStatus.ACTIVE: ("accent", "accent_soft"),
    SessionStatus.STOPPED: ("text_secondary", "surface_alt"),
    SessionStatus.INTERRUPTED: ("warning", "warning_soft"),
    SessionStatus.FAILED: ("danger", "danger_soft"),
}


def _type_text(s: Session) -> str:
    text = "Indefinite" if s.is_indefinite else f"Timed · {format_planned(s.duration_seconds)}"
    return f"{text} · display on" if s.display_awake else text


class SessionTableModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[Session] = []

    def set_sessions(self, sessions: list[Session]) -> None:
        self.beginResetModel()
        self._rows = list(sessions)
        self.endResetModel()

    def session_at(self, row: int) -> Session:
        return self._rows[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section].upper()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: C901
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                format_datetime(s.started_at),
                format_datetime(s.ended_at) if s.ended_at else ("In progress" if s.is_active else "Unknown"),
                format_duration(s.elapsed_seconds()) if s.ended_at else "—",
                _type_text(s),
                STATUS_LABELS[s.status],
            )[col]
        if role == SORT_ROLE:
            return (
                s.started_at.timestamp(),
                s.ended_at.timestamp() if s.ended_at else 0.0,
                s.elapsed_seconds() or 0.0,
                s.duration_seconds if s.duration_seconds is not None else 10**9,
                s.status.value,
            )[col]
        if role == Qt.ItemDataRole.UserRole:
            return s
        if role == Qt.ItemDataRole.ForegroundRole and col in (1, 3):
            return theme().color("text_secondary")
        return None


class StatusDelegate(QStyledItemDelegate):
    """Draws the status column as a rounded badge."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        session: Session | None = index.data(Qt.ItemDataRole.UserRole)
        if session is None:
            return super().paint(painter, option, index)
        # Selection / background first.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawControl(style.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        fg_key, bg_key = _STATUS_COLORS[session.status]
        text = STATUS_LABELS[session.status]
        font = painter.font()
        font.setPixelSize(11)
        font.setWeight(font.Weight.DemiBold)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 16
        height = 20
        rect = QRectF(option.rect.x() + 12, option.rect.center().y() - height / 2 + 0.5, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        painter.fillPath(path, theme().color(bg_key))
        painter.setFont(font)
        painter.setPen(theme().color(fg_key))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class SessionsPage(QWidget):
    history_cleared = Signal()

    def __init__(self, repository: SessionRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("page", True)
        self._repo = repository

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(20)
        header, text = page_header("Sessions", "Every awake session, stored only on this computer.")
        self.summary = text.itemAt(1).widget()
        self.clear_button = QPushButton("Clear history")
        self.clear_button.setProperty("variant", "danger")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.confirm_clear)
        header.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Table
        table_card = Card(padding=8, spacing=0)
        self.model = SessionTableModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header_view = self.table.horizontalHeader()
        header_view.setHighlightSections(False)
        header_view.setSortIndicatorShown(True)
        header_view.setMinimumSectionSize(72)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header_view.resizeSection(2, 100)
        header_view.resizeSection(4, 128)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setItemDelegateForColumn(len(COLUMNS) - 1, StatusDelegate(self.table))
        table_card.body.addWidget(self.table)
        self.stack.addWidget(table_card)

        # Empty state
        empty_card = Card(padding=40, spacing=8)
        empty_card.body.addStretch(1)
        empty_card.body.addWidget(IconLabel("history", 32, "text_muted"), 0, Qt.AlignmentFlag.AlignHCenter)
        empty_card.body.addSpacing(8)
        title = label("No sessions yet", "cardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_card.body.addWidget(title)
        hint = label("Sessions you start will appear here, newest first.", "body")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_card.body.addWidget(hint)
        empty_card.body.addStretch(1)
        self.stack.addWidget(empty_card)

        self.reload()

    def reload(self) -> None:
        try:
            sessions = self._repo.all()
        except DatabaseError as exc:
            log.error(kv("history_load_failed", error=exc))
            sessions = []
        self.model.set_sessions(sessions)
        finished = [s for s in sessions if not s.is_active]
        total = sum(s.elapsed_seconds() or 0 for s in finished)
        count = len(sessions)
        if count:
            noun = "session" if count == 1 else "sessions"
            self.summary.setText(f"{count} {noun} · {format_duration(total)} awake in total · stored only on this computer")
        else:
            self.summary.setText("Every awake session, stored only on this computer.")
        self.clear_button.setEnabled(bool(finished))
        self.stack.setCurrentIndex(0 if count else 1)

    def confirm_clear(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Clear session history")
        box.setText("Clear all session history?")
        box.setInformativeText("This permanently deletes every finished session from this computer. "
                               "A session that is currently running is kept.")
        clear = box.addButton("Clear history", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not clear:
            return
        try:
            self._repo.clear()
        except DatabaseError as exc:
            QMessageBox.critical(self, "Clear session history", f"History could not be cleared:\n{exc}")
            return
        self.reload()
        self.history_cleared.emit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
