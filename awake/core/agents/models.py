"""Data types for AI-agent activity detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AgentKind(StrEnum):
    CLAUDE_CODE = "claude-code"


AGENT_NAMES: dict[AgentKind, str] = {AgentKind.CLAUDE_CODE: "Claude Code"}


class AgentStatus(StrEnum):
    BUSY = "busy"         # a turn is running
    WAITING = "waiting"   # blocked on the user (permission prompt, question) — treated as idle
    IDLE = "idle"         # finished, waiting for the next prompt


@dataclass(frozen=True, slots=True)
class AgentSession:
    """One live agent session. Deliberately holds no prompts, paths or names (privacy)."""

    agent: AgentKind
    session_id: str
    pid: int
    status: AgentStatus

    @property
    def is_busy(self) -> bool:
        return self.status is AgentStatus.BUSY


class SourceState(StrEnum):
    NOT_RUNNING = "not_running"   # no agent process on this machine
    ACTIVE = "active"             # agent running and its status was read successfully
    UNAVAILABLE = "unavailable"   # agent running but its status can't be read (old version, error)


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """Result of one detection pass."""

    sessions: tuple[AgentSession, ...] = ()
    claude: SourceState = SourceState.NOT_RUNNING
    claude_detail: str | None = None  # user-facing reason when UNAVAILABLE
    taken_at: float = field(default=0.0, compare=False)

    @property
    def busy_sessions(self) -> tuple[AgentSession, ...]:
        return tuple(s for s in self.sessions if s.is_busy)

    @property
    def any_busy(self) -> bool:
        return any(s.is_busy for s in self.sessions)
