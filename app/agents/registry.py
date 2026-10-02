"""In-memory registry for explicitly selected agents."""

from typing import Dict, List

from app.agents.base import Agent


class AgentAlreadyRegisteredError(ValueError):
    """Raised when registering an agent name that is already in use."""


class AgentNotFoundError(LookupError):
    """Raised when a requested agent name is not registered."""


class AgentRegistry:
    """Store agents by name and provide explicit lookup."""

    def __init__(self) -> None:
        self._agents: Dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register an agent, rejecting names that are already present."""
        if agent.name in self._agents:
            raise AgentAlreadyRegisteredError(
                f"Agent '{agent.name}' is already registered"
            )
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent:
        """Return the named agent or raise a clear lookup error."""
        try:
            return self._agents[name]
        except KeyError as exc:
            raise AgentNotFoundError(f"Agent '{name}' is not registered") from exc

    def list_agents(self) -> List[str]:
        """Return registered agent names in registration order."""
        return list(self._agents)
