"""Model discovery endpoints for the frontend model picker."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.config import get_settings

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/models", tags=["models"])

_OLLAMA_TIMEOUT_SECONDS = 5.0


class OllamaModelsResponse(BaseModel):
    provider: str = "ollama"
    models: list[str]


def ollama_tags_url(base_url: str) -> str:
    """Return Ollama's native ``/api/tags`` URL for the configured base URL.

    ``OLLAMA_BASE_URL`` points at the OpenAI-compatible API (``.../v1``) used for
    chat, while model listing lives on Ollama's native API at the server root.
    """

    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/api/tags", "", ""))


def _model_ids(payload: Any) -> list[str]:
    """Extract exact model IDs, in the order Ollama returned them."""

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("unexpected response shape")

    ids: list[str] = []
    for entry in payload["models"]:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("name") or entry.get("model")
        if isinstance(model_id, str) and model_id and model_id not in ids:
            ids.append(model_id)
    return ids


@models_router.get("/ollama", response_model=OllamaModelsResponse)
async def list_ollama_models(
    current_user: CurrentUser = Depends(get_current_user),
) -> OllamaModelsResponse:
    """List the models installed on the configured Ollama server.

    Never substitutes anything: an unreachable or misbehaving server is a 502,
    and a server with nothing installed is an empty list.
    """

    del current_user

    settings = get_settings()
    url = ollama_tags_url(settings.ollama_base_url)
    headers = {"Authorization": f"Bearer {settings.ollama_api_key}"} if settings.ollama_api_key else {}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=_OLLAMA_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("Ollama model discovery failed for %s: %s", url, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cannot connect to Ollama at {settings.ollama_base_url}: {exc.__class__.__name__}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama at {settings.ollama_base_url} returned HTTP {response.status_code}",
        )

    try:
        models = _model_ids(response.json())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama at {settings.ollama_base_url} returned an unexpected response",
        ) from exc

    return OllamaModelsResponse(models=models)
