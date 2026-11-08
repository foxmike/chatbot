"""Deterministic persona-to-prompt assembly for the chatbot engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Optional

from chatbot.soul.character import CharacterProfile
from chatbot.soul.evolution import IdentityState
from chatbot.soul.linguistic_fingerprint import format_linguistic_fingerprint
from chatbot.soul.modes import ConversationMode, ModeSelector
from chatbot.soul.mood import MoodEngine, MoodState
from chatbot.soul.pacing import calculate_momentum, get_pacing_directive
from chatbot.soul.relationship import RelationshipConfig


_DEFAULT_CONFIG: dict[str, Any] = {
    "fallback_prompt": (
        "You are a helpful, neutral assistant. Respond clearly, accurately, "
        "and respectfully."
    ),
    "default_style": "concise",
    "section_order": [
        "identity",
        "tone",
        "traits",
        "backstory",
        "boundaries",
        "directives",
    ],
    "sections": {
        "identity": {
            "label": "Identity",
            "aliases": ["name", "display_name", "persona_name"],
            "empty": "helpful assistant",
        },
        "tone": {
            "label": "Tone",
            "aliases": ["tone", "voice", "communication_style"],
        },
        "traits": {
            "label": "Traits",
            "aliases": ["traits", "core_traits", "personality_traits"],
        },
        "backstory": {
            "label": "Backstory",
            "aliases": ["backstory", "background", "description"],
        },
        "boundaries": {
            "label": "Boundaries",
            "aliases": ["boundaries", "constraints", "rules", "limits"],
        },
        "directives": {
            "label": "Directives",
            "aliases": [
                "directives",
                "custom_directives",
                "custom_instructions",
                "instructions",
            ],
        },
    },
    "styles": {
        "concise": {
            "heading": "Persona",
            "heading_format": "{heading}",
            "section_format": "{label}: {content}",
            "item_prefix": "- ",
            "section_separator": "\n",
        },
        "narrative": {
            "heading": "Persona Profile",
            "heading_format": "{heading}",
            "section_format": "{label}\n{content}",
            "item_prefix": "- ",
            "section_separator": "\n\n",
        },
        "roleplay": {
            "heading": "Role Instructions",
            "heading_format": "{heading}",
            "section_format": "{label}\n{content}",
            "item_prefix": "* ",
            "section_separator": "\n\n",
            "tail": "Maintain this persona consistently unless safety or truthfulness requires otherwise.",
        },
    },
}


class PersonaPromptAssembler:
    """Build reproducible system prompts from structured persona dictionaries."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = _merge_config(_DEFAULT_CONFIG, config or {})

    @classmethod
    def from_dict(cls, config: Mapping[str, Any] | None = None) -> "PersonaPromptAssembler":
        return cls(config)

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any] | None = None
    ) -> "PersonaPromptAssembler":
        return cls(config)

    def assemble(
        self,
        persona: dict[str, Any],
        mood: MoodState | None = None,
        mode: ConversationMode | None = None,
        relationship_config: Optional[RelationshipConfig] = None,
        character_profile: Optional[CharacterProfile] = None,
        identity_state: Optional[IdentityState] = None,
        punctuation_habits: Optional[dict[str, Any]] = None,
        sentence_length_profile: Optional[dict[str, Any]] = None,
        emoji_style: Optional[dict[str, Any]] = None,
        vocabulary_density: Optional[float] = None,
        slang_profile: Optional[dict[str, Any]] = None,
        scene_type: Optional[str] = None,
        momentum: Optional[str] = None,
        user_response_time_seconds: Optional[float] = None,
        user_message_length: Optional[int] = None,
        relationship_stage_known: bool = True,
        relationship_history: str | None = None,
        developed_self: str | None = None,
    ) -> str:
        """``developed_self`` is the companion's established, evidence-backed views
        (``chatbot.soul.self_model.developed_self_text``); it is rendered after the
        configured identity, which stays authoritative.
        ``relationship_stage_known=False`` omits the relationship stage, depth
        and stage guidance (use it whenever ``depth`` is not evidence-based);
        ``relationship_history`` adds a factual history line."""
        persona_data = _persona_mapping(persona)

        # Resolve Linguistic Fingerprint variables
        ph = punctuation_habits or persona_data.get("punctuation_habits")
        sl = sentence_length_profile or persona_data.get("sentence_length_profile")
        es = emoji_style or persona_data.get("emoji_style")
        vd = vocabulary_density if vocabulary_density is not None else persona_data.get("vocabulary_density")
        sp = slang_profile or persona_data.get("slang_profile")

        fingerprint_text = format_linguistic_fingerprint(
            punctuation_habits=ph,
            sentence_length_profile=sl,
            emoji_style=es,
            vocabulary_density=vd,
            slang_profile=sp,
        )

        # Resolve Pacing variables
        resolved_scene = scene_type or persona_data.get("scene_type")
        if not resolved_scene and mode is not None:
            mode_str = mode.value if hasattr(mode, "value") else str(mode)
            resolved_scene = mode_str.lower()
        resolved_scene = resolved_scene or "casual"

        resolved_momentum = momentum or persona_data.get("momentum")
        if not resolved_momentum:
            res_time = user_response_time_seconds if user_response_time_seconds is not None else persona_data.get("user_response_time_seconds")
            msg_len = user_message_length if user_message_length is not None else persona_data.get("user_message_length")
            resolved_momentum = calculate_momentum(res_time, msg_len)

        pacing_directive = get_pacing_directive(resolved_scene, resolved_momentum)

        if not persona_data:
            return self._with_prompt_modifiers(
                str(self._config["fallback_prompt"]),
                "\n",
                mood,
                mode,
                relationship_config,
                character_profile,
                identity_state,
                fingerprint_text=fingerprint_text,
                pacing_directive=pacing_directive,
                relationship_stage_known=relationship_stage_known,
                relationship_history=relationship_history,
                developed_self=developed_self,
            )

        style_name = _first_text(
            _persona_value(persona_data, "template"),
            _persona_value(persona_data, "style"),
        )
        if not style_name:
            style_name = str(self._config["default_style"])

        styles = self._config.get("styles", {})
        style = styles.get(style_name) if isinstance(styles, Mapping) else None
        if not isinstance(style, Mapping):
            style = styles.get(self._config["default_style"], {})
        if not isinstance(style, Mapping):
            style = {}

        sections = self._render_sections(persona_data, style)
        identity_sections = _render_identity_sections(
            style=style,
            relationship_config=relationship_config,
            character_profile=character_profile,
            identity_state=identity_state,
            fingerprint_text=fingerprint_text,
            relationship_stage_known=relationship_stage_known,
            relationship_history=relationship_history,
            developed_self=developed_self,
        )
        if not sections:
            separator = str(style.get("section_separator", "\n"))
            return self._with_prompt_modifiers(
                str(self._config["fallback_prompt"]),
                separator,
                mood,
                mode,
                relationship_config,
                character_profile,
                identity_state,
                fingerprint_text=fingerprint_text,
                pacing_directive=pacing_directive,
                relationship_stage_known=relationship_stage_known,
                relationship_history=relationship_history,
                developed_self=developed_self,
            )

        heading = _format_template(
            str(style.get("heading_format", "{heading}")),
            heading=str(style.get("heading", "Persona")),
        )
        tail = _clean_text(style.get("tail"))
        parts = [heading, *sections]
        if tail:
            parts.append(tail)

        separator = str(style.get("section_separator", "\n"))
        parts.extend(identity_sections)
        modifier_section = _render_prompt_modifiers(
            style=style,
            mood=mood,
            mode=mode,
            pacing_directive=pacing_directive,
        )
        if modifier_section:
            parts.append(modifier_section)

        return separator.join(parts)

    def _with_prompt_modifiers(
        self,
        prompt: str,
        separator: str,
        mood: MoodState | None,
        mode: ConversationMode | None,
        relationship_config: Optional[RelationshipConfig],
        character_profile: Optional[CharacterProfile],
        identity_state: Optional[IdentityState],
        fingerprint_text: str | None = None,
        pacing_directive: str | None = None,
        relationship_stage_known: bool = True,
        relationship_history: str | None = None,
        developed_self: str | None = None,
    ) -> str:
        style = _default_modifier_style(self._config)
        parts = [prompt]
        parts.extend(
            _render_identity_sections(
                style=style,
                relationship_config=relationship_config,
                character_profile=character_profile,
                identity_state=identity_state,
                fingerprint_text=fingerprint_text,
                relationship_stage_known=relationship_stage_known,
                relationship_history=relationship_history,
                developed_self=developed_self,
            )
        )
        modifier_section = _render_prompt_modifiers(
            style=style,
            mood=mood,
            mode=mode,
            pacing_directive=pacing_directive,
        )
        if not modifier_section:
            return separator.join(parts)
        return separator.join([*parts, modifier_section])

    def _render_sections(
        self, persona: Mapping[str, Any], style: Mapping[str, Any]
    ) -> list[str]:
        section_map = self._config.get("sections", {})
        if not isinstance(section_map, Mapping):
            return []

        order = self._config.get("section_order", [])
        section_keys = list(order) if isinstance(order, list) else sorted(section_map)
        rendered: list[str] = []

        for key in section_keys:
            section = section_map.get(key)
            if not isinstance(section, Mapping):
                continue

            value = _lookup_section_value(persona, section)
            if value is None and key == "identity":
                value = section.get("empty")

            content = _render_value(value, str(style.get("item_prefix", "- ")))
            if not content:
                continue

            rendered.append(
                _format_template(
                    str(style.get("section_format", "{label}: {content}")),
                    label=str(section.get("label", key)),
                    content=content,
                )
            )

        return rendered


def assemble_persona_prompt(
    persona: dict[str, Any],
    mood: MoodState | None = None,
    mode: ConversationMode | None = None,
    relationship_config: Optional[RelationshipConfig] = None,
    character_profile: Optional[CharacterProfile] = None,
    identity_state: Optional[IdentityState] = None,
    punctuation_habits: Optional[dict[str, Any]] = None,
    sentence_length_profile: Optional[dict[str, Any]] = None,
    emoji_style: Optional[dict[str, Any]] = None,
    vocabulary_density: Optional[float] = None,
    slang_profile: Optional[dict[str, Any]] = None,
    scene_type: Optional[str] = None,
    momentum: Optional[str] = None,
    user_response_time_seconds: Optional[float] = None,
    user_message_length: Optional[int] = None,
) -> str:
    if isinstance(mood, str):
        return _render_legacy_personality_prompt(persona, mood, mode)

    assembler = PersonaPromptAssembler()
    return assembler.assemble(
        persona,
        mood=mood,
        mode=mode,
        relationship_config=relationship_config,
        character_profile=character_profile,
        identity_state=identity_state,
        punctuation_habits=punctuation_habits,
        sentence_length_profile=sentence_length_profile,
        emoji_style=emoji_style,
        vocabulary_density=vocabulary_density,
        slang_profile=slang_profile,
        scene_type=scene_type,
        momentum=momentum,
        user_response_time_seconds=user_response_time_seconds,
        user_message_length=user_message_length,
    )


def _merge_config(
    default: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(dict(default))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _lookup_section_value(
    persona: Mapping[str, Any], section: Mapping[str, Any]
) -> Any | None:
    aliases = section.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]

    for alias in aliases:
        if alias in persona and not _is_empty(persona[alias]):
            return persona[alias]

    return None


def _render_legacy_personality_prompt(
    persona: Any,
    identity_anchor: str,
    state: Any,
) -> str:
    persona_data = _persona_mapping(persona)
    anchor = _clean_fragment(identity_anchor)
    if not anchor:
        anchor = _clean_fragment(getattr(state, "identity_anchor", ""))

    name = _clean_text(_persona_value(persona_data, "name"))
    description = _clean_text(_persona_value(persona_data, "description"))
    traits = _persona_value(persona_data, "core_traits")
    tone = _clean_text(_persona_value(persona_data, "tone"))
    verbosity = _clean_text(_persona_value(persona_data, "verbosity"))
    custom_instructions = _clean_fragment(
        _persona_value(persona_data, "custom_instructions")
    )

    personality_lines = []
    if name:
        personality_lines.append(f"Name: {name}")
    if description:
        personality_lines.append(f"Description: {description}")
    traits_text = _render_trait_summary(traits)
    if traits_text:
        personality_lines.append(f"Core traits: {traits_text}")

    tone_lines = []
    if tone:
        tone_lines.append(f"Tone: {tone}")
    if verbosity:
        tone_lines.append(f"Verbosity: {verbosity}")

    constraint_lines = []
    for key, label in (
        ("use_emojis", "Use emojis"),
        ("use_markdown", "Use markdown"),
        ("max_response_length", "Max response length"),
    ):
        value = _persona_value(persona_data, key)
        if value is not None:
            constraint_lines.append(f"{label}: {value}")
    if custom_instructions:
        constraint_lines.append(f"Custom instructions: {custom_instructions}")

    context_lines = []
    session_id = _clean_text(getattr(state, "session_id", ""))
    user_id = _clean_text(getattr(state, "user_id", ""))
    if session_id:
        context_lines.append(f"Session: {session_id}")
    if user_id:
        context_lines.append(f"User: {user_id}")

    return "\n".join(
        [
            "IDENTITY",
            anchor or name or str(_DEFAULT_CONFIG["fallback_prompt"]),
            "PERSONALITY",
            "\n".join(personality_lines) or "No fixed personality configured.",
            "TONE",
            "\n".join(tone_lines) or "neutral",
            "CONSTRAINTS",
            "\n".join(constraint_lines) or "No extra constraints.",
            "CONTEXT",
            "\n".join(context_lines) or "No additional context.",
        ]
    )


def _persona_mapping(persona: Any) -> Mapping[str, Any]:
    if isinstance(persona, Mapping):
        return persona
    if is_dataclass(persona) and not isinstance(persona, type):
        return asdict(persona)
    return {}


def _persona_value(persona: Mapping[str, Any], key: str) -> Any | None:
    return persona.get(key)


def _render_trait_summary(value: Any) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_clean_text(item) for item in value if _clean_text(item))
    return _clean_text(value)


def _render_value(value: Any, item_prefix: str) -> str:
    if _is_empty(value):
        return ""

    if isinstance(value, Mapping):
        lines: list[str] = []
        for key in sorted(value, key=lambda item: str(item)):
            rendered = _render_value(value[key], item_prefix)
            if rendered:
                lines.append(f"{item_prefix}{key}: {rendered}")
        return "\n".join(lines)

    if isinstance(value, (list, tuple)):
        return _render_sequence(value, item_prefix)

    if isinstance(value, (set, frozenset)):
        return _render_sequence(sorted(value, key=lambda item: str(item)), item_prefix)

    return _clean_text(value)


def _render_sequence(value: list[Any] | tuple[Any, ...], item_prefix: str) -> str:
    lines = []
    for item in value:
        rendered = _render_value(item, item_prefix)
        if rendered:
            lines.append(f"{item_prefix}{rendered}")
    return "\n".join(lines)


def _render_prompt_modifiers(
    *,
    style: Mapping[str, Any],
    mood: MoodState | None,
    mode: ConversationMode | None,
    pacing_directive: str | None = None,
) -> str:
    hints: list[str] = []

    if mood is not None:
        hints.append(_clean_text(MoodEngine().to_prompt_hints(mood)))

    if mode is not None:
        selector = ModeSelector()
        hints.append(_clean_text(selector.to_prompt_hints(mode)))
        parameter_hints = _render_response_parameter_hints(
            selector.get_response_params(mode)
        )
        if parameter_hints:
            hints.append(parameter_hints)

    if pacing_directive:
        hints.append(_clean_text(pacing_directive))

    if not hints:
        return ""

    hints.append(
        "Apply these as secondary response-shaping cues while preserving the "
        "fixed persona, boundaries, and directives above."
    )
    content = _render_sequence(hints, str(style.get("item_prefix", "- ")))
    return _format_template(
        str(style.get("section_format", "{label}: {content}")),
        label="Response guidance",
        content=content,
    )


def _render_identity_sections(
    *,
    style: Mapping[str, Any],
    relationship_config: Optional[RelationshipConfig],
    character_profile: Optional[CharacterProfile],
    identity_state: Optional[IdentityState],
    fingerprint_text: str | None = None,
    relationship_stage_known: bool = True,
    relationship_history: str | None = None,
    developed_self: str | None = None,
) -> list[str]:
    sections: list[str] = []

    if relationship_config is not None:
        if relationship_stage_known and relationship_history is None:
            fragment = relationship_config.to_prompt_fragment()
        else:
            fragment = relationship_config.to_prompt_fragment(
                include_stage=relationship_stage_known, history=relationship_history
            )
        sections.append(_render_labeled_section(style, "RELATIONSHIP CONFIG", fragment))

    if character_profile is not None:
        sections.append(
            _render_labeled_section(
                style,
                "IDENTITY",
                character_profile.to_prompt_fragment(),
            )
        )

    if developed_self:
        # Established, evidence-backed views; the configured identity above wins.
        sections.append(_render_labeled_section(style, "DEVELOPED SELF", developed_self))

    if identity_state is not None:
        tier_labels = {"fixed": "FIXED IDENTITY", "slow": "CURRENT DRIFT TRAITS", "mood": "MOOD"}
        prompt_tiers = getattr(identity_state, "prompt_tiers", None)
        if callable(prompt_tiers):
            tiers = prompt_tiers()  # empty tiers are left out
        else:
            tiers = zip(tier_labels, identity_state.to_prompt_fragment().split("\n\n", 2))
        for tier, fragment in tiers:
            sections.append(_render_labeled_section(style, tier_labels[tier], fragment))

    if fingerprint_text:
        prefix = "Linguistic Directives:\n"
        content = fingerprint_text
        if content.startswith(prefix):
            content = content[len(prefix):]
        sections.append(
            _render_labeled_section(
                style,
                "LINGUISTIC FINGERPRINT",
                content,
            )
        )

    return [section for section in sections if section]


def _render_labeled_section(
    style: Mapping[str, Any],
    label: str,
    content: str,
) -> str:
    text = _clean_fragment(content)
    if not text:
        return ""
    return _format_template(
        str(style.get("section_format", "{label}: {content}")),
        label=label,
        content=text,
    )


def _render_response_parameter_hints(params: Mapping[str, Any]) -> str:
    parts: list[str] = []

    length_ratio = params.get("max_tokens_ratio")
    if isinstance(length_ratio, (int, float)):
        parts.append(f"length multiplier {_format_number(float(length_ratio))}")

    initiative = _clean_text(params.get("initiative_level"))
    if initiative:
        parts.append(f"initiative {initiative.replace('_', ' ')}")

    temperature = params.get("temperature_modifier")
    if isinstance(temperature, (int, float)):
        parts.append(
            f"temperature adjustment {_format_signed_number(float(temperature))}"
        )

    should_suggest = params.get("should_suggest_topics")
    if isinstance(should_suggest, bool):
        parts.append(
            "topic suggestions enabled"
            if should_suggest
            else "topic suggestions restrained"
        )

    if not parts:
        return ""
    return f"Response parameters: {'; '.join(parts)}."


def _default_modifier_style(config: Mapping[str, Any]) -> Mapping[str, Any]:
    styles = config.get("styles", {})
    if not isinstance(styles, Mapping):
        return {}

    default_style = _clean_text(config.get("default_style"))
    style = styles.get(default_style, {})
    if isinstance(style, Mapping):
        return style
    return {}


def _format_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_signed_number(value: float) -> str:
    formatted = _format_number(value)
    if value > 0:
        return f"+{formatted}"
    return formatted


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _clean_fragment(value: Any) -> str:
    if value is None:
        return ""
    lines = [" ".join(line.strip().split()) for line in str(value).strip().splitlines()]
    return "\n".join(line for line in lines if line)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return len(value) == 0
    return False


def _format_template(template: str, **values: str) -> str:
    return template.format(**values)


__all__ = ["PersonaPromptAssembler", "assemble_persona_prompt"]
