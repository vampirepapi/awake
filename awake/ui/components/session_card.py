"""Compact row describing one past session (used on the dashboard)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from awake.database.models import Session
from awake.ui.components.common import IconTile, label
from awake.ui.formatting import STATUS_LABELS, format_duration, format_planned, session_span


def status_badge_text(session: Session) -> tuple[str, str]:
    """(badge style, label) for a session status."""
    return session.status.value, STATUS_LABELS[session.status]


class SessionCard(QFrame):
    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("rowHover", True)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(12)
        row.addWidget(IconTile("infinity" if session.is_indefinite else "timer"), 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        actual = format_duration(session.elapsed_seconds())
        title = f"{actual} · {session.session_type}"
        if not session.is_indefinite:
            title += f" ({format_planned(session.duration_seconds)})"
        text.addWidget(label(title, "rowTitle"))
        details = session_span(session)
        if session.display_awake:
            details += "  ·  Display kept on"
        text.addWidget(label(details, "rowDescription"))
        row.addLayout(text, 1)

        style, badge_text = status_badge_text(session)
        badge = label(badge_text)
        badge.setProperty("badge", style)
        badge.setToolTip(_reason_tooltip(session))
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)


def _reason_tooltip(session: Session) -> str:
    reasons = {
        "expired": "The timer finished.",
        "user_stopped": "Stopped manually.",
        "app_quit": "Stopped when Awake quit.",
        "app_terminated": "Awake was closed unexpectedly while this session was running.",
        "power_error": "The system stopped honouring the power request.",
    }
    return reasons.get(session.end_reason.value if session.end_reason else "", "")
