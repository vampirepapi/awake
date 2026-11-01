"""User-facing wording for AI-agent activity, shared by dashboard, tray and sidebar.

Strings here are deliberately short: the dashboard row has room for ~24 characters.
"""

from __future__ import annotations

from awake.core.agents import AGENT_NAMES, AgentActivity, AgentHoldState, AgentKind, SourceState
from awake.core.agents.coordinator import DEFAULT_BATTERY_FLOOR, DEFAULT_CAP_SECONDS


def _busy_kinds(activity: AgentActivity) -> list[AgentKind]:
    kinds: list[AgentKind] = []
    for session in activity.busy:
        if session.agent not in kinds:
            kinds.append(session.agent)
    return kinds


def who_is_working(activity: AgentActivity) -> str:
    """``"Codex"``, ``"2 Claude Code sessions"`` or ``"Claude Code + Codex"``."""
    kinds = _busy_kinds(activity)
    if not kinds:
        return "Your AI agent"
    if len(kinds) > 1:
        return " + ".join(AGENT_NAMES[k] for k in kinds)
    count = len(activity.busy)
    name = AGENT_NAMES[kinds[0]]
    return name if count == 1 else f"{count} {name} sessions"


def hero_line(activity: AgentActivity) -> str:
    kinds = _busy_kinds(activity)
    if not kinds:
        return "AI mode: staying awake while your agent works."
    names = " + ".join(AGENT_NAMES[k] for k in kinds)
    verb = "work" if len(kinds) > 1 else "works"
    return f"AI mode: staying awake while {names} {verb}."


def describe(activity: AgentActivity | None) -> str:
    """One-line status for the power-controls row."""
    if activity is None or activity.state is AgentHoldState.DISABLED:
        return "While AI agents work."
    match activity.state:
        case AgentHoldState.HOLDING:
            busy = activity.busy
            if len(busy) == 1:
                return f"{AGENT_NAMES[busy[0].agent]} is working."
            return f"{len(busy)} sessions working." if busy else "Agent working."
        case AgentHoldState.GRACE:
            return "Finished — sleeping soon."
        case AgentHoldState.LOW_BATTERY:
            return f"Paused: battery below {DEFAULT_BATTERY_FLOOR:.0f}%."
        case AgentHoldState.CAPPED:
            return f"Paused after {DEFAULT_CAP_SECONDS // 3600} h. Resumes when idle."
        case AgentHoldState.ERROR:
            return "System refused. Retrying…"
    snapshot = activity.snapshot
    for status in snapshot.sources:
        if status.state is SourceState.UNAVAILABLE:
            return status.detail or f"Can't read {AGENT_NAMES[status.agent]}'s status."
    if any(s.state is SourceState.ACTIVE for s in snapshot.sources):
        return "No agent working right now."
    for status in snapshot.sources:
        if status.state is SourceState.NOT_CONNECTED:
            return f"Connect {AGENT_NAMES[status.agent]} in Settings."
    return "Watching for AI agents."


def short_status(activity: AgentActivity | None) -> str | None:
    """Compact text for pills, sidebar and tray when an agent is holding the system, else None."""
    if activity is None or not activity.holding:
        return None
    if activity.state is AgentHoldState.GRACE:
        return "Agent finishing"
    return f"{who_is_working(activity)} working"
