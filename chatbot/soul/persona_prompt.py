"""Deterministic persona-to-prompt assembly for the chatbot engine."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

from chatbot.soul.character import CharacterProfile
from chatbot.soul.evolution import IdentityState
from chatbot.soul.modes import ConversationMode, ModeSelector
from chatbot.soul.mood import MoodEngine, MoodState
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
    ) -> str:
        if not persona:
            return self._with_prompt_modifiers(
                str(self._config["fallback_prompt"]),
                "\n",
                mood,
                mode,
                relationship_config,
                character_profile,
                identity_state,
            )

        style_name = _first_text(persona.get("template"), persona.get("style"))
        if not style_name:
            style_name = str(self._config["default_style"])

        styles = self._config.get("styles", {})
        style = styles.get(style_name) if isinstance(styles, Mapping) else None
        if not isinstance(style, Mapping):
            style = styles.get(self._config["default_style"], {})
        if not isinstance(style, Mapping):
            style = {}

        sections = self._render_sections(persona, style)
        identity_sections = _render_identity_sections(
            style=style,
            relationship_config=relationship_config,
            character_profile=character_profile,
            identity_state=identity_state,
        )
        if not sections:
            if not identity_sections:
                return str(self._config["fallback_prompt"])
            separator = str(style.get("section_separator", "\n"))
            return separator.join(
                [str(self._config["fallback_prompt"]), *identity_sections]
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
    ) -> str:
        style = _default_modifier_style(self._config)
        parts = [prompt]
        parts.extend(
            _render_identity_sections(
                style=style,
                relationship_config=relationship_config,
                character_profile=character_profile,
                identity_state=identity_state,
            )
        )
        modifier_section = _render_prompt_modifiers(
            style=style,
            mood=mood,
            mode=mode,
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
) -> str:
    assembler = PersonaPromptAssembler()
    return assembler.assemble(
        persona,
        mood,
        mode,
        relationship_config,
        character_profile,
        identity_state,
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
) -> list[str]:
    sections: list[str] = []

    if relationship_config is not None:
        sections.append(
            _render_labeled_section(
                style,
                "RELATIONSHIP CONFIG",
                relationship_config.to_prompt_fragment(),
            )
        )

    if character_profile is not None:
        sections.append(
            _render_labeled_section(
                style,
                "IDENTITY",
                character_profile.to_prompt_fragment(),
            )
        )

    if identity_state is not None:
        tier_fragments = identity_state.to_prompt_fragment().split("\n\n", 2)
        for label, fragment in zip(
            ("FIXED IDENTITY", "CURRENT DRIFT TRAITS", "MOOD"),
            tier_fragments,
        ):
            sections.append(_render_labeled_section(style, label, fragment))

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
