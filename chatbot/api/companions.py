"""Companion API — CRUD endpoints for per-user companions."""

from __future__ import annotations

import json
import random
import re
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.companions.companion import (
    CompanionCreateRequest,
    CompanionListResponse,
    CompanionManager,
    CompanionVoiceError,
    CompanionResponse,
)
from chatbot.db import get_db
from chatbot.db.models import CharacterProfileModel, IdentityStateModel, Session
from chatbot.providers.base import ProviderConfig, ProviderError, ProviderRegistry

companion_router = APIRouter(prefix="/companions", tags=["companions"])

GenerateFieldName = Literal[
    "name",
    "backstory",
    "persona",
    "personality",
    "favorite_topics",
]
GenderValue = Literal["male", "female"]

PERSONALITY_DIMS = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    "curiosity",
    "creativity",
)
HUMOR_STYLES = ("dry", "witty", "silly", "dark", "none")
COMMUNICATION_STYLES = (
    "verbose",
    "concise",
    "storyteller",
    "question_driven",
    "aphoristic",
)
PERSONALITY_ANGLES = [
    "Emphasize inner contradictions — traits that seem to conflict but coexist",
    "Focus on how this person acts under stress vs when relaxed",
    "Interpret this character as someone who has recently changed significantly",
    "Emphasize the gap between how they present publicly and who they are privately",
    "Focus on what makes this person difficult or frustrating to be around",
    "Interpret this character as someone suppressing a core part of themselves",
    "Focus on their warmth and how they connect with people",
    "Emphasize their intellectual and analytical side",
    "Interpret this character as someone driven primarily by emotion over logic",
    "Focus on their independence and self-reliance",
    "Emphasize their playfulness and how they use humor as a tool",
    "Interpret this character as guarded and slow to trust",
    "Focus on their restless energy and need for novelty",
    "Emphasize their groundedness and comfort with routine",
    "Interpret this character through their relationship with authority",
    "Focus on their creative impulses and how those shape their personality",
]
SLIDER_SEEDS = [
    "Make one slider notably extreme (above 0.9 or below 0.15)",
    "Create a strong contrast between openness and conscientiousness",
    "Make extraversion and agreeableness pull in opposite directions",
    "Set neuroticism high and pair it with high creativity",
    "Make curiosity the dominant trait, noticeably above everything else",
    "Create a profile where conscientiousness is surprisingly low",
    "Set agreeableness very low but creativity very high",
    "Make extraversion the most extreme value in either direction",
    "Create an unusual pairing: high neuroticism with low openness",
    "Make at least three sliders above 0.8",
    "Make at least two sliders below 0.25",
    "Set creativity and curiosity in opposite directions",
]


class GenerateFieldContext(BaseModel):
    gender: GenderValue = "female"
    name: str | None = None
    backstory: str | None = None
    persona: str | None = None
    personality: dict[str, Any] | None = None


class GenerateFieldRequest(BaseModel):
    field: GenerateFieldName
    context: GenerateFieldContext = Field(default_factory=GenerateFieldContext)
    model_override: str | None = None
    provider_override: str | None = None


class GenerateFieldResponse(BaseModel):
    field: GenerateFieldName
    content: str | dict[str, Any]


@companion_router.post(
    "",
    response_model=CompanionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_companion(
    body: CompanionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    try:
        return await manager.create(current_user.user_id, body)
    except CompanionVoiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@companion_router.post("/generate-field", response_model=GenerateFieldResponse)
async def generate_companion_field(
    request: Request,
    body: GenerateFieldRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> GenerateFieldResponse:
    del current_user

    prompt = _build_generate_field_prompt(body.field, body.context)
    provider_name = _clean_override(body.provider_override) or str(
        getattr(request.app.state, "default_provider", "openrouter")
    )
    model = _clean_override(body.model_override) or str(
        getattr(request.app.state, "default_model", "google/gemma-4-31b-it")
    )
    registry = getattr(request.app.state, "provider_registry", None)
    if not isinstance(registry, ProviderRegistry):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider registry is not initialized",
        )

    try:
        provider = registry.get(provider_name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider is not available: {provider_name}",
        )

    try:
        response = await provider.complete(
            prompt=prompt,
            history=[],
            config=ProviderConfig(
                model=model,
                temperature=0.95,
                max_tokens=900 if body.field in {"backstory", "persona"} else 450,
                top_p=0.95,
                timeout_seconds=45,
            ),
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Generation provider failed: {exc}",
        ) from exc

    text = _strip_model_wrapping(response.text)
    if body.field == "personality":
        return GenerateFieldResponse(
            field=body.field,
            content=_parse_personality_response(text),
        )

    return GenerateFieldResponse(field=body.field, content=text)


@companion_router.get("", response_model=CompanionListResponse)
async def list_companions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionListResponse:
    manager = CompanionManager(db)
    return await manager.list_by_user(current_user.user_id)


@companion_router.get("/{companion_id}", response_model=CompanionResponse)
async def get_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    companion = await manager.get(current_user.user_id, companion_id)
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
    return companion


@companion_router.put("/{companion_id}", response_model=CompanionResponse)
async def update_companion(
    companion_id: str,
    body: CompanionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    try:
        companion = await manager.update(current_user.user_id, companion_id, body)
    except CompanionVoiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )

    # Propagate changes to existing sessions
    sessions_result = await db.execute(
        select(Session.id).where(Session.companion_id == companion_id)
    )
    session_ids = [row[0] for row in sessions_result.all()]

    for sid in session_ids:
        # Update IdentityStateModel
        is_result = await db.execute(
            select(IdentityStateModel)
            .where(IdentityStateModel.session_id == sid)
            .order_by(IdentityStateModel.last_updated.desc())
            .limit(1)
        )
        identity_state = is_result.scalar_one_or_none()
        if identity_state:
            fixed = dict(identity_state.fixed)
            fixed["name"] = companion.name
            fixed["backstory"] = companion.backstory
            if companion.humor_style is not None:
                fixed["humor_style"] = companion.humor_style
            identity_state.fixed = fixed

            slow = dict(identity_state.slow)
            if companion.personality_sliders:
                for key in ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism", "curiosity", "creativity"]:
                    if key in companion.personality_sliders:
                        slow[key] = companion.personality_sliders[key]
            identity_state.slow = slow

        # Update CharacterProfileModel
        await db.execute(
            update(CharacterProfileModel)
            .where(CharacterProfileModel.session_id == sid)
            .values(
                name=companion.name,
                backstory=companion.backstory,
                personality_sliders=companion.personality_sliders,
                humor_style=companion.humor_style,
                communication_style=companion.communication_style,
                favorite_topics=companion.favorite_topics,
            )
        )

    if session_ids:
        await db.commit()

    return companion


@companion_router.delete("/{companion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    manager = CompanionManager(db)
    deleted = await manager.delete(current_user.user_id, companion_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )


def _clean_override(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _build_generate_field_prompt(
    field: GenerateFieldName,
    context: GenerateFieldContext,
) -> str:
    context_lines = _generation_context_lines(field, context)

    shared = (
        "You generate companion profile fields for a chatbot app.\n"
        "Use the provided gender for names, pronouns, and life details.\n"
        "Treat this as a fresh re-roll for the requested field, even if a previous value exists.\n"
        "Make the result specific, vivid, and internally coherent. Avoid generic archetypes.\n"
        "Return only the requested content. No preamble, no markdown fences, no labels.\n\n"
        f"Variation key: {uuid4()}\n\n"
        "Current companion context:\n"
        + "\n".join(f"- {line}" for line in context_lines)
        + "\n\n"
    )

    if field == "name":
        return (
            shared
            + "Generate one new companion name strictly appropriate to the selected gender. "
            "Choose from Western and Eastern European and select Asion including English, German, French, Italian, Swedish, Danish, Serbian, Japanese, South Korean and Russian names. "
            "Never repeat a common choice. Use 1-2 words only. No punctuation. "
            "Do not reuse any existing name from context. "
            "Pick a name that is uncommon and unexpected but not so much so as to stray "
            "from traditional Western and Eastern European or Japanese/Korean names. Do not default to popular or "
            "frequently-used names."
        )
    if field == "backstory":
        return (
            shared
            + "Generate a new 2-4 paragraph backstory for this companion. "
            "Include concrete history, formative experiences, contradictions, and present-day motivations. "
            "Do not preserve or paraphrase a previous backstory. "
            "Do not include or reference the companion's name anywhere in the output."
        )
    if field == "persona":
        return (
            shared
            + "Generate a new persona description covering voice, mannerisms, conversational style, "
            "emotional texture, and how the companion tends to respond in chat. "
            "Do not preserve or paraphrase a previous persona. "
            "Do not include or reference the companion's name anywhere in the output."
        )
    if field == "favorite_topics":
        return (
            shared
            + "Generate 8-12 favorite topics or interests as a comma-separated list. "
            "Make them varied and grounded in the current profile. "
            "Do not include or reference the companion's name anywhere in the output."
        )

    random_angle = random.choice(PERSONALITY_ANGLES)
    random_slider_seed = random.choice(SLIDER_SEEDS)
    return (
        shared
        + (
            f"Interpretation lens: {random_angle}\n"
            f"Slider guidance: {random_slider_seed}\n\n"
            "Generate a personality profile that fits the companion's backstory and persona\n"
            "but filtered through the above lens. The result should feel like a valid reading\n"
            "of this character, not a generic one.\n\n"
            "You MUST randomly select humor_style and communication_style — do NOT default to any\n"
            "particular option. Roll a mental dice for each independently.\n\n"
            "Generate a new coherent personality profile as strict JSON only.\n"
            "Do not preserve prior dropdown choices or slider values.\n"
            "humor_style must be one of: dry, witty, silly, dark, none\n"
            "communication_style must be one of: verbose, concise, storyteller, question_driven, aphoristic\n"
            "Include sliders for exactly these keys: openness, conscientiousness, extraversion,\n"
            "agreeableness, neuroticism, curiosity, creativity\n"
            "Slider values must be numbers from 0.0 to 1.0 with visible variation between dimensions.\n"
            "IMPORTANT: Each call should produce a genuinely different combination. Vary ALL fields.\n"
            'Return shape: {"humor_style":"silly","communication_style":"verbose","sliders":{"openness":0.5,...}}'
        )
    )


def _generation_context_lines(
    field: GenerateFieldName,
    context: GenerateFieldContext,
) -> list[str]:
    lines = [f"Gender: {context.gender}"]

    if field in {"name", "personality"}:
        lines.append(f"Name: {_optional_context(context.name)}")

    if field in {"persona", "personality", "favorite_topics"}:
        lines.append(f"Backstory: {_optional_context(context.backstory)}")

    if field in {"personality", "favorite_topics"}:
        lines.append(f"Persona: {_optional_context(context.persona)}")

    if field == "favorite_topics" and context.personality:
        lines.append(
            "Personality: "
            + json.dumps(context.personality, ensure_ascii=True, sort_keys=True)
        )
    elif field == "favorite_topics":
        lines.append("Personality: not set")

    return lines


def _optional_context(value: str | None) -> str:
    if value is None:
        return "not set"
    stripped = value.strip()
    return stripped or "not set"


def _strip_model_wrapping(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {'"', "'"}
    ):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _parse_personality_response(text: str) -> dict[str, Any]:
    raw = _extract_json_object(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Generation provider returned invalid personality JSON",
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Generation provider returned invalid personality data",
        )

    humor_style = str(data.get("humor_style", "witty")).strip()
    communication_style = str(data.get("communication_style", "concise")).strip()
    if humor_style not in HUMOR_STYLES:
        humor_style = "witty"
    if communication_style not in COMMUNICATION_STYLES:
        communication_style = "concise"

    raw_sliders = data.get("sliders")
    if not isinstance(raw_sliders, dict):
        raw_sliders = {}

    sliders: dict[str, float] = {}
    for dimension in PERSONALITY_DIMS:
        value = raw_sliders.get(dimension, 0.5)
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.5
        sliders[dimension] = max(0.0, min(1.0, round(number, 2)))

    return {
        "humor_style": humor_style,
        "communication_style": communication_style,
        "sliders": sliders,
    }


def _extract_json_object(text: str) -> str:
    cleaned = _strip_model_wrapping(text)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        return match.group(0)
    return cleaned
