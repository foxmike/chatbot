"""Deterministic persona-to-prompt assembly for the chatbot engine."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


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

    def assemble(self, persona: dict[str, Any]) -> str:
        if not persona:
            return str(self._config["fallback_prompt"])

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
        if not sections:
            return str(self._config["fallback_prompt"])

        heading = _format_template(
            str(style.get("heading_format", "{heading}")),
            heading=str(style.get("heading", "Persona")),
        )
        tail = _clean_text(style.get("tail"))
        parts = [heading, *sections]
        if tail:
            parts.append(tail)

        separator = str(style.get("section_separator", "\n"))
        return separator.join(parts)

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


def assemble_persona_prompt(persona: dict[str, Any]) -> str:
    assembler = PersonaPromptAssembler()
    return assembler.assemble(persona)


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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


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
