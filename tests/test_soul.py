from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.soul import (
    DEFAULT_PERSONALITY,
    InMemoryStateRepository,
    Personality,
    SoulState,
    assemble_persona_prompt,
    compute_identity_anchor,
    update_identity_anchor,
)


def make_state(*, identity_anchor: str = "") -> SoulState:
    return SoulState(
        session_id="session-1",
        user_id="user-1",
        personality_id="personality-1",
        identity_anchor=identity_anchor,
    )


def test_personality_defaults() -> None:
    assert DEFAULT_PERSONALITY == Personality(
        name="Companion",
        description="A helpful, thoughtful chatbot companion.",
        core_traits=["empathetic", "analytical", "clear"],
        tone="neutral",
        verbosity="balanced",
    )
    assert DEFAULT_PERSONALITY.use_emojis is False
    assert DEFAULT_PERSONALITY.use_markdown is True
    assert DEFAULT_PERSONALITY.max_response_length == 2000
    assert DEFAULT_PERSONALITY.custom_instructions is None


def test_personality_serialization() -> None:
    personality = Personality(
        name="Guide",
        description="A precise testing assistant.",
        core_traits=["clear", "patient"],
        tone="formal",
        verbosity="concise",
        use_emojis=True,
        use_markdown=False,
        max_response_length=500,
        custom_instructions="Prefer short answers.",
    )

    assert Personality.from_dict(personality.to_dict()) == personality


def test_state_repository_in_memory() -> None:
    async def run() -> None:
        repository = InMemoryStateRepository()
        state = make_state(identity_anchor="stable identity")

        await repository.save_state(state)
        assert await repository.get_state("session-1") == state

        await repository.delete_state("session-1")
        assert await repository.get_state("session-1") is None

    asyncio.run(run())


def test_identity_anchor_persistence() -> None:
    state = make_state(identity_anchor="already known")

    assert compute_identity_anchor(state, DEFAULT_PERSONALITY) == "already known"


def test_identity_anchor_computation() -> None:
    state = make_state()

    expected = (
        "Name: Companion\n"
        "Description: A helpful, thoughtful chatbot companion.\n"
        "Core traits: empathetic, analytical, clear\n"
        "Tone: neutral"
    )

    assert compute_identity_anchor(state, DEFAULT_PERSONALITY) == expected
    assert compute_identity_anchor(state, DEFAULT_PERSONALITY) == expected


def test_identity_update_no_drift() -> None:
    state = make_state(identity_anchor="stable identity")

    updated = update_identity_anchor(state, DEFAULT_PERSONALITY, new_anchor="replacement")

    assert updated.identity_anchor == "stable identity"


def test_persona_prompt_contains_identity() -> None:
    prompt = assemble_persona_prompt(DEFAULT_PERSONALITY, "identity anchor text")

    assert "identity anchor text" in prompt


def test_persona_prompt_deterministic() -> None:
    state = make_state(identity_anchor="identity anchor text")

    first = assemble_persona_prompt(DEFAULT_PERSONALITY, state.identity_anchor, state)
    second = assemble_persona_prompt(DEFAULT_PERSONALITY, state.identity_anchor, state)

    assert first == second


def test_persona_prompt_sections() -> None:
    prompt = assemble_persona_prompt(
        DEFAULT_PERSONALITY,
        "identity anchor text",
        make_state(identity_anchor="identity anchor text"),
    )

    for section in ("IDENTITY", "PERSONALITY", "TONE", "CONSTRAINTS", "CONTEXT"):
        assert section in prompt.splitlines()
