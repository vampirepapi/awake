"""AI-agent activity detection (phase 1: Claude Code).

AgentMonitor (detects) → AgentCoordinator (decides, holds the "agents" power
owner). No widgets here; see docs/AGENT_DETECTION.md for the research and design.
"""

from awake.core.agents.coordinator import AgentActivity, AgentCoordinator, AgentHoldState
from awake.core.agents.models import AGENT_NAMES, AgentKind, AgentSession, AgentSnapshot, AgentStatus, SourceState
from awake.core.agents.monitor import AgentMonitor

__all__ = [
    "AGENT_NAMES",
    "AgentActivity",
    "AgentCoordinator",
    "AgentHoldState",
    "AgentKind",
    "AgentMonitor",
    "AgentSession",
    "AgentSnapshot",
    "AgentStatus",
    "SourceState",
]
