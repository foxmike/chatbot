"""Attention anchoring system to track and format active attention anchors.

This module tracks unresolved questions, companion promises, and active user
goals, formatting them for injection into prompt layers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Import timeline functions from continuity. Use fallback if not available.
try:
    from chatbot.continuity.timeline import (
        TimelineEntry,
        add_timeline_entry,
        get_active_projects,
        get_pending_promises,
        get_timeline,
        update_timeline_entry,
    )
except ImportError:
    # Minimal fallbacks for robustness/testing if imports fail
    TimelineEntry = None
    add_timeline_entry = None
    get_active_projects = None
    get_pending_promises = None
    get_timeline = None
    update_timeline_entry = None


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Return the current datetime in UTC."""
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AttentionAnchor:
    """An active attention anchor representation."""

    anchor_id: str
    session_id: str
    anchor_type: str  # "question", "promise", "goal"
    content: str
    status: str = "active"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class AttentionAnchorManager:
    """Manages tracking, resolving, and formatting attention anchors for a session."""

    def __init__(self, session_id: str, db_session: Any | None = None) -> None:
        self.session_id = session_id
        self.db_session = db_session

    async def track_unresolved_question(self, question: str, turn_id: str | None = None) -> None:
        """Track an unresolved question by saving it as a timeline entry."""
        if not question or not question.strip():
            return

        question = question.strip()
        t_id = turn_id or "system"

        if add_timeline_entry is not None:
            try:
                await add_timeline_entry(
                    session_id=self.session_id,
                    entry_type="running_project",
                    title="Unresolved Question",
                    description=question,
                    content=question,
                    turn_id=t_id,
                    tags=["question", "unresolved_question"],
                    metadata={"source": "attention_anchor_system"},
                )
            except Exception as e:
                logger.error("Failed to save unresolved question to timeline: %s", e)
        else:
            logger.warning("Timeline module unavailable; cannot track unresolved question persistently.")

    async def resolve_question(self, question: str) -> None:
        """Resolve a tracked question by marking the matching timeline entry as resolved."""
        if not question or not question.strip():
            return

        question = question.strip()
        if get_timeline is not None and update_timeline_entry is not None:
            try:
                # Find the active question entries
                entries = await get_timeline(self.session_id)
                for entry in entries:
                    if (
                        entry.entry_type == "running_project"
                        and entry.status == "active"
                        and entry.tags
                        and "question" in entry.tags
                        and (entry.description == question or entry.content == question)
                    ):
                        await update_timeline_entry(entry.entry_id, status="resolved")
            except Exception as e:
                logger.error("Failed to resolve question in timeline: %s", e)

    async def track_user_goal(self, goal: str, turn_id: str | None = None) -> None:
        """Track an active user goal by saving it as a timeline entry."""
        if not goal or not goal.strip():
            return

        goal = goal.strip()
        t_id = turn_id or "system"

        if add_timeline_entry is not None:
            try:
                await add_timeline_entry(
                    session_id=self.session_id,
                    entry_type="running_project",
                    title="Active Goal",
                    description=goal,
                    content=goal,
                    turn_id=t_id,
                    tags=["active_goal", "user_goal"],
                    metadata={"source": "attention_anchor_system"},
                )
            except Exception as e:
                logger.error("Failed to save user goal to timeline: %s", e)

    async def resolve_user_goal(self, goal: str) -> None:
        """Resolve a tracked user goal by marking the matching timeline entry as completed/resolved."""
        if not goal or not goal.strip():
            return

        goal = goal.strip()
        if get_timeline is not None and update_timeline_entry is not None:
            try:
                entries = await get_timeline(self.session_id)
                for entry in entries:
                    if (
                        entry.entry_type == "running_project"
                        and entry.status == "active"
                        and (entry.description == goal or entry.content == goal)
                    ):
                        await update_timeline_entry(entry.entry_id, status="completed")
            except Exception as e:
                logger.error("Failed to resolve user goal in timeline: %s", e)

    async def track_companion_promise(self, promise: str, turn_id: str | None = None) -> None:
        """Track an active companion promise by saving it as a timeline entry."""
        if not promise or not promise.strip():
            return

        promise = promise.strip()
        t_id = turn_id or "system"

        if add_timeline_entry is not None:
            try:
                await add_timeline_entry(
                    session_id=self.session_id,
                    entry_type="promise",
                    title="Companion Promise",
                    description=promise,
                    content=promise,
                    turn_id=t_id,
                    tags=["promise", "companion_promise"],
                    metadata={"source": "attention_anchor_system"},
                )
            except Exception as e:
                logger.error("Failed to save companion promise to timeline: %s", e)

    async def resolve_companion_promise(self, promise: str) -> None:
        """Resolve a tracked companion promise by marking the matching timeline entry as resolved."""
        if not promise or not promise.strip():
            return

        promise = promise.strip()
        if get_timeline is not None and update_timeline_entry is not None:
            try:
                entries = await get_timeline(self.session_id)
                for entry in entries:
                    if (
                        entry.entry_type == "promise"
                        and entry.status == "active"
                        and (entry.description == promise or entry.content == promise)
                    ):
                        await update_timeline_entry(entry.entry_id, status="resolved")
            except Exception as e:
                logger.error("Failed to resolve companion promise in timeline: %s", e)

    async def get_unresolved_questions(self, history: list[dict[str, Any]] | None = None) -> list[str]:
        """Get all unresolved questions from timeline entries and/or chat history."""
        questions: list[str] = []

        # 1. Fetch from database timeline entries
        if get_timeline is not None:
            try:
                entries = await get_timeline(self.session_id)
                for entry in entries:
                    if (
                        entry.entry_type == "running_project"
                        and entry.status == "active"
                        and entry.tags
                        and ("question" in entry.tags or "unresolved_question" in entry.tags)
                    ):
                        content = entry.description or entry.content
                        if content and content.strip():
                            questions.append(content.strip())
            except Exception as e:
                logger.error("Failed to retrieve questions from timeline: %s", e)

        # 2. Extract from recent history
        if history:
            history_questions = extract_questions_from_history(history)
            for q in history_questions:
                if q not in questions:
                    questions.append(q)

        return questions

    async def get_companion_promises(self) -> list[str]:
        """Get all active companion promises from timeline entries."""
        promises: list[str] = []
        if get_pending_promises is not None:
            try:
                entries = await get_pending_promises(self.session_id)
                for entry in entries:
                    content = entry.description or entry.content
                    if content and content.strip():
                        promises.append(content.strip())
            except Exception as e:
                logger.error("Failed to retrieve promises from timeline: %s", e)
        return promises

    async def get_active_user_goals(self) -> list[str]:
        """Get all active user goals from timeline entries (excluding questions)."""
        goals: list[str] = []
        if get_active_projects is not None:
            try:
                entries = await get_active_projects(self.session_id)
                for entry in entries:
                    # Filter out entries that are tagged as questions
                    if entry.tags and ("question" in entry.tags or "unresolved_question" in entry.tags):
                        continue
                    content = entry.description or entry.content
                    if content and content.strip():
                        goals.append(content.strip())
            except Exception as e:
                logger.error("Failed to retrieve active goals from timeline: %s", e)
        return goals

    async def format_anchors_prompt(self, history: list[dict[str, Any]] | None = None) -> str:
        """Fetch and format all active attention anchors into a prompt-friendly string."""
        questions = await self.get_unresolved_questions(history)
        promises = await self.get_companion_promises()
        goals = await self.get_active_user_goals()

        return format_attention_anchors(questions, promises, goals)


def extract_questions_from_history(history: list[dict[str, Any]], limit: int = 5) -> list[str]:
    """Helper to scan recent messages in history for potential unresolved questions."""
    if not history:
        return []

    questions = []
    # Match sentences ending with '?'
    sentence_regex = re.compile(r"[^.!?]*\?")

    # Scan the last few messages (e.g. 6 messages)
    for msg in reversed(history[-6:]):
        content = msg.get("content", "")
        if not content:
            continue

        found = sentence_regex.findall(content)
        for q in found:
            q_clean = q.strip()
            # Basic validation to ensure it's a real question sentence
            if q_clean and len(q_clean) > 5:
                if q_clean not in questions:
                    questions.append(q_clean)

    questions.reverse()
    return questions[:limit]


def format_attention_anchors(
    unresolved_questions: list[str],
    companion_promises: list[str],
    active_goals: list[str],
) -> str:
    """Format lists of anchors into a prompt injection block."""
    sections = []
    if unresolved_questions:
        lines = "\n".join(f"- {q}" for q in unresolved_questions)
        sections.append(f"Unresolved Questions:\n{lines}")
    if companion_promises:
        lines = "\n".join(f"- {p}" for p in companion_promises)
        sections.append(f"Companion Promises:\n{lines}")
    if active_goals:
        lines = "\n".join(f"- {g}" for g in active_goals)
        sections.append(f"Active Goals:\n{lines}")

    if not sections:
        return ""

    return "Active Attention Anchors:\n" + "\n\n".join(sections)


__all__ = [
    "AttentionAnchor",
    "AttentionAnchorManager",
    "extract_questions_from_history",
    "format_attention_anchors",
]
