"""Data types for AI-agent activity detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AgentKind(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    CURSOR = "cursor"


AGENT_NAMES: dict[AgentKind, str] = {
    AgentKind.CLAUDE_CODE: "Claude Code",
    AgentKind.CODEX: "Codex",
    AgentKind.CURSOR: "Cursor",
}


class AgentStatus(StrEnum):
    BUSY = "busy"         # a turn is running
    WAITING = "waiting"   # blocked on the user (permission prompt, question) — treated as idle
    IDLE = "idle"         # finished, waiting for the next prompt


@dataclass(frozen=True, slots=True)
class AgentSession:
    """One live agent session. Deliberately holds no prompts, paths or names (privacy)."""

    agent: AgentKind
    session_id: str
    pid: int  # 0 when the source can't tie a session to a process (Codex, Cursor)
    status: AgentStatus

    @property
    def is_busy(self) -> bool:
        return self.status is AgentStatus.BUSY


class SourceState(StrEnum):
    NOT_RUNNING = "not_running"       # no agent process on this machine
    ACTIVE = "active"                 # agent running and its status was read successfully
    UNAVAILABLE = "unavailable"       # agent running but its status can't be read right now
    NOT_CONNECTED = "not_connected"   # agent running but needs a one-time connection (Cursor hooks)


@dataclass(frozen=True, slots=True)
class SourceStatus:
    agent: AgentKind
    state: SourceState
    detail: str | None = None  # user-facing reason for UNAVAILABLE / NOT_CONNECTED


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """Result of one detection pass across all agents."""

    sessions: tuple[AgentSession, ...] = ()
    sources: tuple[SourceStatus, ...] = ()
    taken_at: float = field(default=0.0, compare=False)

    def source(self, agent: AgentKind) -> SourceStatus:
        for status in self.sources:
            if status.agent is agent:
                return status
        return SourceStatus(agent, SourceState.NOT_RUNNING)

    @property
    def claude(self) -> SourceState:
        return self.source(AgentKind.CLAUDE_CODE).state

    @property
    def claude_detail(self) -> str | None:
        return self.source(AgentKind.CLAUDE_CODE).detail

    @property
    def busy_sessions(self) -> tuple[AgentSession, ...]:
        return tuple(s for s in self.sessions if s.is_busy)

    @property
    def any_busy(self) -> bool:
        return any(s.is_busy for s in self.sessions)

    @property
    def unknown(self) -> bool:
        """Some running agent's status couldn't be read — absence of "busy" proves nothing."""
        return any(s.state is SourceState.UNAVAILABLE for s in self.sources)

    @property
    def any_running(self) -> bool:
        return any(s.state is not SourceState.NOT_RUNNING for s in self.sources)

    def with_sources(self, *sources: SourceStatus) -> AgentSnapshot:
        return AgentSnapshot(self.sessions, tuple(sources), self.taken_at)


def claude_snapshot(
    sessions: tuple[AgentSession, ...] = (),
    state: SourceState = SourceState.ACTIVE,
    detail: str | None = None,
) -> AgentSnapshot:
    """Convenience for a Claude-only snapshot (tests and the Claude source)."""
    return AgentSnapshot(sessions, (SourceStatus(AgentKind.CLAUDE_CODE, state, detail),))
