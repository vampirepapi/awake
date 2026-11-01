"""User-facing wording for AI-agent activity, shared by dashboard, tray and sidebar."""

from __future__ import annotations

from awake.core.agents import AGENT_NAMES, AgentActivity, AgentHoldState, AgentKind, SourceState
from awake.core.agents.coordinator import DEFAULT_BATTERY_FLOOR, DEFAULT_CAP_SECONDS


def who_is_working(activity: AgentActivity) -> str:
    """``"Claude Code"`` or ``"2 Claude Code sessions"``."""
    busy = activity.busy
    name = AGENT_NAMES[busy[0].agent] if busy else AGENT_NAMES[AgentKind.CLAUDE_CODE]
    return name if len(busy) <= 1 else f"{len(busy)} {name} sessions"


def describe(activity: AgentActivity | None) -> str:
    """One-line status for the power-controls row."""
    if activity is None or activity.state is AgentHoldState.DISABLED:
        return "While Claude Code is working."
    match activity.state:
        case AgentHoldState.HOLDING:
            return f"{who_is_working(activity)} working now."
        case AgentHoldState.GRACE:
            return "Just finished — sleeping normally soon."
        case AgentHoldState.LOW_BATTERY:
            return f"Paused: battery below {DEFAULT_BATTERY_FLOOR:.0f}%."
        case AgentHoldState.CAPPED:
            return f"Paused after {DEFAULT_CAP_SECONDS // 3600} h. Resumes when idle."
        case AgentHoldState.ERROR:
            return "System refused. Retrying…"
    snapshot = activity.snapshot
    if snapshot.claude is SourceState.UNAVAILABLE:
        return snapshot.claude_detail or "Can't read Claude Code's status."
    if snapshot.claude is SourceState.ACTIVE:
        return "No agent working right now."
    return "Watching for Claude Code."


def short_status(activity: AgentActivity | None) -> str | None:
    """Compact text for pills, sidebar and tray when an agent is holding the system, else None."""
    if activity is None or not activity.holding:
        return None
    if activity.state is AgentHoldState.GRACE:
        return "Claude Code finishing"
    return f"{who_is_working(activity)} working"
