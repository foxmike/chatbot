"""Per-model prompt formatting helpers.

The core turn pipeline keeps prompts provider-neutral. This module adapts that
neutral shape into the message layouts expected by common model families.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ModelFamily(str, Enum):
    """Prompt formatting families supported by the runtime."""

    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENAI = "openai"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class FormattedPrompt:
    """Provider-ready prompt payload with the selected formatting family."""

    family: ModelFamily
    payload: dict[str, Any]


_ANTHROPIC_MARKERS: tuple[str, ...] = (
    "anthropic/",
    "claude",
)
_GEMINI_MARKERS: tuple[str, ...] = (
    "gemini",
    "google/gemini",
)
_OPENAI_MARKERS: tuple[str, ...] = (
    "chatgpt",
    "gpt-",
    "gpt/",
    "o1",
    "o3",
    "o4",
    "openai/",
    "text-davinci",
)


def detect_model_family(
    model: str | None,
    *,
    provider: str | None = None,
) -> ModelFamily:
    """Return the prompt formatting family for a provider/model identifier.

    Unknown models intentionally fall back to ``ModelFamily.DEFAULT`` so callers
    can keep using the standard message layout instead of failing.
    """

    provider_text = _normalize_identifier(provider)
    model_text = _normalize_identifier(model)
    combined = "/".join(part for part in (provider_text, model_text) if part)

    if _contains_any(combined, _ANTHROPIC_MARKERS):
        return ModelFamily.ANTHROPIC
    if _contains_any(combined, _GEMINI_MARKERS):
        return ModelFamily.GEMINI
    if provider_text in {"openai", "openrouter", "sglang", "vllm", "ollama"}:
        return ModelFamily.OPENAI
    if _contains_any(combined, _OPENAI_MARKERS):
        return ModelFamily.OPENAI
    return ModelFamily.DEFAULT


def format_prompt(
    *,
    model: str | None,
    prompt: str,
    history: Sequence[Mapping[str, Any]] | None = None,
    system_prompt: str | None = None,
    provider: str | None = None,
) -> FormattedPrompt:
    """Format a prompt for the detected model family.

    ``history`` accepts OpenAI-style message dictionaries. Any ``system``
    messages in history are lifted into the family-specific system instruction
    field, after the explicit ``system_prompt`` if one is supplied.
    """

    family = detect_model_family(model, provider=provider)
    system, messages = _split_system_messages(history or (), system_prompt)
    current_prompt = str(prompt or "").strip()
    if current_prompt:
        messages.append({"role": "user", "content": current_prompt})

    if family is ModelFamily.ANTHROPIC:
        return FormattedPrompt(
            family=family,
            payload=_format_anthropic(system, messages),
        )
    if family is ModelFamily.GEMINI:
        return FormattedPrompt(
            family=family,
            payload=_format_gemini(system, messages),
        )
    if family is ModelFamily.OPENAI:
        return FormattedPrompt(
            family=family,
            payload=_format_openai(system, messages),
        )
    return FormattedPrompt(
        family=family,
        payload=_format_default(system, messages),
    )


def format_prompt_for_model(
    model: str | None,
    prompt: str,
    history: Sequence[Mapping[str, Any]] | None = None,
    *,
    system_prompt: str | None = None,
    provider: str | None = None,
) -> FormattedPrompt:
    """Positional-friendly wrapper around :func:`format_prompt`."""

    return format_prompt(
        model=model,
        prompt=prompt,
        history=history,
        system_prompt=system_prompt,
        provider=provider,
    )


def _format_openai(system: str, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    formatted = _standard_messages(system, messages)
    return {"messages": formatted}


def _format_default(system: str, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _format_openai(system, messages)


def _format_anthropic(system: str, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [
            {"role": _anthropic_role(message), "content": _message_content(message)}
            for message in messages
            if _message_content(message)
        ]
    }
    if system:
        payload["system"] = system
    return payload


def _format_gemini(system: str, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": _gemini_role(message),
                "parts": [{"text": _message_content(message)}],
            }
            for message in messages
            if _message_content(message)
        ]
    }
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    return payload


def _standard_messages(
    system: str,
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    formatted: list[dict[str, str]] = []
    if system:
        formatted.append({"role": "system", "content": system})
    for message in messages:
        content = _message_content(message)
        if not content:
            continue
        role = str(message.get("role") or "user").strip() or "user"
        formatted.append({"role": role, "content": content})
    return formatted


def _split_system_messages(
    history: Sequence[Mapping[str, Any]],
    system_prompt: str | None,
) -> tuple[str, list[dict[str, str]]]:
    system_parts = [_clean_text(system_prompt)]
    messages: list[dict[str, str]] = []

    for message in history:
        if not isinstance(message, Mapping):
            continue
        content = _message_content(message)
        if not content:
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "system":
            system_parts.append(content)
            continue
        messages.append({"role": role or "user", "content": content})

    system = "\n\n".join(part for part in system_parts if part)
    return system, messages


def _anthropic_role(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "").strip().lower()
    if role == "assistant":
        return "assistant"
    return "user"


def _gemini_role(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "").strip().lower()
    if role == "assistant":
        return "model"
    return "user"


def _message_content(message: Mapping[str, Any]) -> str:
    return _clean_text(message.get("content"))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_identifier(value: str | None) -> str:
    return str(value or "").strip().lower()


def _contains_any(value: str, markers: Sequence[str]) -> bool:
    return any(marker in value for marker in markers)


__all__ = [
    "FormattedPrompt",
    "ModelFamily",
    "detect_model_family",
    "format_prompt",
    "format_prompt_for_model",
]
