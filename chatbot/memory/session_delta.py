"""Session-resume delta injection module.

This module provides tools to format summarized context and parameters
into a structured delta block. This delta block can be injected into
the prompt to quickly resume an existing conversation session.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionDelta:
    """Represents a session-resume delta containing context and parameters."""

    summary: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True if there is no context to resume."""
        return not (self.summary or self.parameters or self.events or self.active_goals)

    def format_block(self) -> str:
        """Format the session delta into a structured text block.

        Returns:
            A formatted string block containing the session delta,
            or an empty string if the delta is empty.
        """
        if self.is_empty():
            return ""

        lines = ["=== SESSION RESUME CONTEXT ==="]

        if self.summary:
            lines.append("--- Previous Context Summary ---")
            lines.append(self.summary.strip())
            lines.append("")

        if self.parameters:
            lines.append("--- Session Parameters ---")
            for key, value in sorted(self.parameters.items()):
                lines.append(f"{key}: {value}")
            lines.append("")

        if self.active_goals:
            lines.append("--- Active Goals ---")
            for goal in self.active_goals:
                lines.append(f"- {goal}")
            lines.append("")

        if self.events:
            lines.append("--- Recent Events ---")
            for event in self.events:
                lines.append(f"- {event}")
            lines.append("")

        # Strip trailing newline if any, before closing the block
        if lines[-1] == "":
            lines.pop()

        lines.append("==============================")

        return "\n".join(lines)


def format_session_resume_block(
    summary: str,
    parameters: Mapping[str, Any] | None = None,
    events: Sequence[str] | None = None,
    active_goals: Sequence[str] | None = None,
) -> str:
    """Helper function to format a session resume delta block.

    Args:
        summary: A textual summary of the previous conversation.
        parameters: Optional dictionary of extracted parameters.
        events: Optional list of recent significant events.
        active_goals: Optional list of currently active goals or tasks.

    Returns:
        A formatted string block ready for injection.
    """
    delta = SessionDelta(
        summary=summary,
        parameters=dict(parameters) if parameters else {},
        events=list(events) if events else [],
        active_goals=list(active_goals) if active_goals else [],
    )
    return delta.format_block()
