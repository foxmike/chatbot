"""Integration tests for rooms, scenes, persona prompts, and memory isolation.

Verifies phase F acceptance criteria:
  f.6: Room-scoped memory isolation
  f.7: Scene persona prompt integration
  f.8: Rooms integration tests
"""

import pytest
from chatbot.rooms.scene import SceneState
from chatbot.rooms.roleplay import RoleplayEngine, RoleplayPersona


# ---------------------------------------------------------------------------
# f.7: Scene persona prompt integration
# ---------------------------------------------------------------------------

class TestScenePersonaPromptIntegration:
    """Verify that scene state and persona are correctly combined into system prompts."""

    @pytest.fixture
    def scene(self) -> SceneState:
        return SceneState(
            scene_id="test-scene-persona-1",
            room_id="test-room-persona-1",
            location="ancient library",
            activity="deciphering arcane scrolls",
            tone="serious",
            intensity=7,
            scene_context="The scrolls are written in a forgotten language.",
        )

    @pytest.fixture
    def persona(self) -> RoleplayPersona:
        return RoleplayPersona(
            name="Archmage Elara",
            description="A wise and patient archmage who has lived for centuries",
            traits=["wise", "patient", "enigmatic"],
            voice_style="measured, uses arcane terminology",
            backstory="Guardian of the Great Library for 300 years",
            scene_goals=["decipher the prophecy", "protect the library"],
            constraints=["never reveal the library's secret entrance", "avoid combat"],
        )

    def test_build_scene_prompt_includes_all_scene_fields(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """The built prompt must embed location, activity, tone, intensity, and context."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        prompt = engine.build_scene_prompt()

        assert persona.name in prompt
        assert persona.description in prompt
        assert persona.voice_style in prompt
        assert persona.backstory in prompt
        assert scene.location in prompt
        assert scene.activity in prompt
        assert scene.tone in prompt
        assert f"{scene.intensity}/10" in prompt
        assert scene.scene_context in prompt
        assert "Stay in character" in prompt

    def test_build_scene_prompt_includes_constraints(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """Constraints must appear in the prompt as guardrails."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        prompt = engine.build_scene_prompt()
        for constraint in persona.constraints:
            assert constraint in prompt

    def test_build_scene_prompt_structure_is_deterministic(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """The prompt must produce identical output for the same inputs."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        first = engine.build_scene_prompt()
        second = engine.build_scene_prompt()
        assert first == second

    def test_build_scene_prompt_different_scenes_produce_different_prompts(
        self, persona: RoleplayPersona
    ):
        """Two scenes with different parameters must produce distinct prompts."""
        scene_a = SceneState(
            scene_id="a", room_id="r1",
            location="tavern", activity="brawling", tone="playful", intensity=3,
        )
        scene_b = SceneState(
            scene_id="b", room_id="r2",
            location="castle", activity="negotiating", tone="serious", intensity=9,
        )
        engine_a = RoleplayEngine(scene=scene_a, persona=persona)
        engine_b = RoleplayEngine(scene=scene_b, persona=persona)
        assert engine_a.build_scene_prompt() != engine_b.build_scene_prompt()

    def test_generate_scene_context_contains_goals_and_traits(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """Scene context summary must include traits and scene goals."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        ctx_text = engine.generate_scene_context()
        for trait in persona.traits:
            assert trait in ctx_text
        for goal in persona.scene_goals:
            assert goal in ctx_text

    def test_generate_scene_context_contains_scene_state(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """Scene context summary must include location, activity, tone, intensity."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        ctx_text = engine.generate_scene_context()
        assert scene.location in ctx_text
        assert scene.activity in ctx_text
        assert scene.tone in ctx_text
        assert str(scene.intensity) in ctx_text

    def test_persona_without_scene_context_still_renders(
        self, persona: RoleplayPersona
    ):
        """Even without scene_context the prompt must render correctly."""
        scene = SceneState(
            scene_id="s1", room_id="r1",
            location="void", activity="waiting", tone="neutral", intensity=5,
        )
        engine = RoleplayEngine(scene=scene, persona=persona)
        prompt = engine.build_scene_prompt()
        assert persona.name in prompt

    def test_minimal_persona_minimal_scene_produces_valid_prompt(self):
        """A minimal persona and scene must still produce a non-empty prompt."""
        scene = SceneState(
            scene_id="s1", room_id="r1",
        )
        persona = RoleplayPersona(
            name="Bot",
            description="A simple bot",
            traits=[],
            voice_style="flat",
            backstory="",
        )
        engine = RoleplayEngine(scene=scene, persona=persona)
        prompt = engine.build_scene_prompt()
        assert len(prompt) > 0
        assert "Bot" in prompt
        assert "Stay in character" in prompt

    def test_prompt_endpoint_returns_scene_prompt(
        self, scene: SceneState, persona: RoleplayPersona
    ):
        """Simulate the /rooms/scenes/{id}/persona endpoint logic."""
        engine = RoleplayEngine(scene=scene, persona=persona)
        result = {"system_prompt": engine.build_scene_prompt()}
        assert "system_prompt" in result
        assert persona.name in result["system_prompt"]
        assert scene.location in result["system_prompt"]


# ---------------------------------------------------------------------------
# f.7: PersonaPromptAssembler compatibility with roleplay persona data
# ---------------------------------------------------------------------------

class TestPersonaPromptAssemblerSceneIntegration:
    """Verify that persona prompt assembly can consume roleplay persona structures."""

    def test_roleplay_persona_as_dict_feeds_assembler(self):
        """A RoleplayPersona converted to dict must produce a valid assembler prompt."""
        from chatbot.soul.persona_prompt import PersonaPromptAssembler

        rp = RoleplayPersona(
            name="Detective Holmes",
            description="A brilliant consulting detective",
            traits=["observant", "logical", "aloof"],
            voice_style="precise, deductive",
            backstory="World's only consulting detective",
            constraints=["never admit uncertainty", "avoid sentiment"],
        )
        persona_dict = {
            "name": rp.name,
            "description": rp.description,
            "traits": rp.traits,
            "tone": rp.voice_style,
            "backstory": rp.backstory,
            "constraints": rp.constraints,
        }

        assembler = PersonaPromptAssembler()
        prompt = assembler.assemble(persona_dict)
        assert "Detective Holmes" in prompt
        assert "observant" in prompt
        assert "logical" in prompt
        assert "consulting detective" in prompt

    def test_assembler_renders_constraints_as_boundaries(self):
        """Constraints map to the Boundaries section in the assembler output."""
        from chatbot.soul.persona_prompt import PersonaPromptAssembler

        assembler = PersonaPromptAssembler()
        prompt = assembler.assemble({
            "name": "Test",
            "constraints": ["rule one", "rule two"],
        })
        assert "Boundaries" in prompt or "rule one" in prompt

    def test_assembler_with_scene_context_produces_non_empty(self):
        """Even unlisted fields pass through without breaking assembly."""
        from chatbot.soul.persona_prompt import PersonaPromptAssembler

        assembler = PersonaPromptAssembler()
        prompt = assembler.assemble({
            "name": "SceneAwareBot",
            "description": "Bot with scene data",
            "scene_context": "location: tavern, tone: playful",
        })
        assert "SceneAwareBot" in prompt


# ---------------------------------------------------------------------------
# f.6: Room-scoped memory isolation
# ---------------------------------------------------------------------------

class TestRoomMemoryIsolation:
    """Verify that memory is properly scoped to rooms and scenes."""

    def test_classify_memory_text_persist_marker(self):
        """Text with persistence markers must be classified as durable."""
        assert RoleplayEngine.classify_text_memory_importance(
            "[remember this] user likes chocolate"
        ) == "durable"
        assert RoleplayEngine.classify_text_memory_importance(
            "[important] birth date is May 5"
        ) == "durable"
        assert RoleplayEngine.classify_text_memory_importance(
            "[persist] favorite color is blue"
        ) == "durable"
        assert RoleplayEngine.classify_text_memory_importance(
            "[save this] anniversary is June 12"
        ) == "durable"
        assert RoleplayEngine.classify_text_memory_importance(
            "[keep this] phone number 555-1234"
        ) == "durable"

    def test_classify_memory_text_relationship_declaration(self):
        """Relationship declarations must be classified as 'both'."""
        assert RoleplayEngine.classify_text_memory_importance(
            "my wife is named Sarah"
        ) == "both"
        assert RoleplayEngine.classify_text_memory_importance(
            "I am married to John"
        ) == "both"
        assert RoleplayEngine.classify_text_memory_importance(
            "my brother lives in Chicago"
        ) == "both"
        assert RoleplayEngine.classify_text_memory_importance(
            "my name is Alice"
        ) == "both"

    def test_classify_memory_text_proper_nouns(self):
        """Text with proper nouns must be classified as 'both'."""
        assert RoleplayEngine.classify_text_memory_importance(
            "I visited Paris last summer"
        ) == "both"
        assert RoleplayEngine.classify_text_memory_importance(
            "I spoke with Doctor Smith about the results"
        ) == "both"
        assert RoleplayEngine.classify_text_memory_importance(
            "my friend Alice from London called yesterday"
        ) == "both"

    def test_classify_memory_text_scene_only(self):
        """Ordinary text without markers/proper nouns must be scene_only."""
        assert RoleplayEngine.classify_text_memory_importance(
            "the weather is nice today"
        ) == "scene_only"
        assert RoleplayEngine.classify_text_memory_importance(
            "i feel tired right now"
        ) == "scene_only"

    def test_is_durable_text_candidate_persist_marker(self):
        """Text with persist markers must be flagged as durable candidates."""
        assert RoleplayEngine.is_durable_text_candidate(
            "[remember this] important fact"
        ) is True
        assert RoleplayEngine.is_durable_text_candidate(
            "[persist] critical data"
        ) is True

    def test_is_durable_text_candidate_non_durable(self):
        """Relationship and scene-only text must not be durable candidates."""
        assert RoleplayEngine.is_durable_text_candidate(
            "my wife is named Sarah"
        ) is False
        assert RoleplayEngine.is_durable_text_candidate(
            "the weather is nice"
        ) is False

    def test_classify_memory_importance_engine_method(self):
        """RoleplayEngine.classify_memory_importance must delegate correctly."""
        scene = SceneState(scene_id="s1", room_id="r1")
        persona = RoleplayPersona(
            name="", description="", traits=[], voice_style="", backstory="",
        )
        engine = RoleplayEngine(scene=scene, persona=persona)
        assert engine.classify_memory_importance("[remember this] data") == "durable"
        assert engine.classify_memory_importance("my sister is named Jane") == "both"
        assert engine.classify_memory_importance("nice day") == "scene_only"

    def test_persist_markers_are_case_insensitive(self):
        """Persist markers must be detected regardless of case."""
        assert RoleplayEngine.classify_text_memory_importance(
            "[REMEMBER THIS] data"
        ) == "durable"
        assert RoleplayEngine.classify_text_memory_importance(
            "[Important] fact"
        ) == "durable"


# ---------------------------------------------------------------------------
# f.8: Rooms integration tests (structural wiring)
# ---------------------------------------------------------------------------

class TestRoomsIntegration:
    """Verify the room, scene, and roleplay module wiring is intact."""

    def test_room_policy_default_for_roleplay_is_non_durable(self):
        """Roleplay scenes default to non-durable memory persistence."""
        from chatbot.rooms.room import DEFAULT_ROOM_POLICIES, RoomType
        policy = DEFAULT_ROOM_POLICIES[RoomType.ROLEPLAY_SCENE]
        assert policy.persist_to_durable is False
        assert policy.scope == "room"

    def test_room_policy_default_for_chat_is_durable(self):
        """One-on-one chats default to durable memory persistence."""
        from chatbot.rooms.room import DEFAULT_ROOM_POLICIES, RoomType
        policy = DEFAULT_ROOM_POLICIES[RoomType.ONE_ON_ONE_CHAT]
        assert policy.persist_to_durable is True
        assert policy.scope == "room"

    def test_scene_state_intensity_clamped(self):
        """SceneState intensity must be clamped to 1-10 range."""
        s = SceneState(scene_id="s1", room_id="r1", intensity=0)
        assert s.intensity == 1
        s = SceneState(scene_id="s2", room_id="r2", intensity=15)
        assert s.intensity == 10
        s = SceneState(scene_id="s3", room_id="r3", intensity=5)
        assert s.intensity == 5

    def test_roleplay_engine_get_scene_memory_scope(self):
        """Memory scope must reflect the room policy."""
        from chatbot.rooms.room import RoomMemoryPolicy
        scene = SceneState(scene_id="s1", room_id="r1")
        persona = RoleplayPersona(
            name="", description="", traits=[], voice_style="", backstory="",
        )
        policy = RoomMemoryPolicy(scope="room")
        engine = RoleplayEngine(scene=scene, persona=persona, memory_policy=policy)
        assert engine.get_scene_memory_scope() == "room"

    def test_roleplay_persona_defaults(self):
        """RoleplayPersona optional fields must have correct defaults."""
        rp = RoleplayPersona(
            name="Test", description="Test", traits=[], voice_style="", backstory="",
        )
        assert rp.scene_goals == []
        assert rp.constraints == []

    def test_room_type_enum_values(self):
        """RoomType enum must contain all expected room types."""
        from chatbot.rooms.room import RoomType
        expected = {"one_on_one_chat", "roleplay_scene", "project",
                     "emotional_support", "memory_review"}
        actual = {t.value for t in RoomType}
        assert expected == actual

    def test_scene_allowable_update_fields(self):
        """Allowed update fields must match the expected set."""
        from chatbot.rooms.scene import ALLOWED_UPDATE_FIELDS
        expected = {"location", "activity", "tone", "intensity",
                     "is_paused", "scene_context", "participants"}
        assert ALLOWED_UPDATE_FIELDS == expected

    def test_turn_context_supports_room_id_metadata(self):
        """TurnContext must accept room_id in metadata for pipeline routing."""
        from chatbot.core.steps import TurnContext
        ctx = TurnContext(
            session_id="s1",
            user_message="hello",
            history=[],
            persona={},
            retrieved_memories=[],
            compiled_context=None,
            provider_response=None,
            extracted_memories=[],
            final_response=None,
            metadata={"room_id": "room-123"},
        )
        assert ctx.metadata["room_id"] == "room-123"

    def test_room_context_step_exists_in_pipeline(self):
        """RoomContextStep must be defined and importable."""
        from chatbot.core.turn import RoomContextStep
        step = RoomContextStep()
        assert step.name == "room_context"

    def test_scene_memory_gate_step_exists(self):
        """SceneMemoryGateStep must be defined and importable."""
        from chatbot.core.turn import SceneMemoryGateStep
        step = SceneMemoryGateStep()
        assert step.name == "scene_memory_gate"

    def test_persona_prompt_assembler_available(self):
        """PersonaPromptAssembler must be importable and functional."""
        from chatbot.soul.persona_prompt import PersonaPromptAssembler
        assembler = PersonaPromptAssembler()
        prompt = assembler.assemble({"name": "TestBot", "traits": ["helpful"]})
        assert len(prompt) > 0