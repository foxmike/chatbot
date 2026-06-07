from __future__ import annotations

import logging
from typing import Union

from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.config import get_settings
from chatbot.db.models import Message, SemanticFact
from chatbot.memory.retrieval import OpenRouterEmbeddingProvider, DisabledEmbeddingProvider

logger = logging.getLogger(__name__)


async def generate_embedding(
    db: AsyncSession,
    target: Union[Message, SemanticFact],
) -> list[float]:
    """Generate embedding for a Message or SemanticFact and store it in pgvector.

    Returns the generated embedding vector. If the embedding provider is disabled
    or an error occurs during processing (where we log and gracefully skip/return empty),
    returns an empty list.
    """
    if target.embedding is not None:
        return target.embedding

    if isinstance(target, Message):
        text = f"{target.role}: {target.content}"
    elif isinstance(target, SemanticFact):
        text = target.fact_text
    else:
        raise TypeError("Target must be a Message or SemanticFact instance")

    if not text.strip():
        logger.warning("Empty text for target, skipping embedding generation.")
        return []

    settings = get_settings()
    if settings.openrouter_api_key:
        provider = OpenRouterEmbeddingProvider(
            api_key=settings.openrouter_api_key,
            base_url=getattr(settings, "embedding_base_url", settings.openrouter_base_url),
            model=getattr(settings, "embedding_model", "openai/text-embedding-3-small"),
        )
    else:
        provider = DisabledEmbeddingProvider(
            "Embedding generation skipped because no embedding provider API key is configured"
        )

    if isinstance(provider, DisabledEmbeddingProvider):
        logger.warning("Embedding provider is disabled: no API key configured.")
        return []

    try:
        embeddings = await provider.embed([text])
        if not embeddings or len(embeddings) != 1:
            raise ValueError("Embedding provider did not return exactly one vector")
        vector = embeddings[0]

        # Verify dimension metadata matches what provider expects
        if provider.dimension > 0 and len(vector) != provider.dimension:
            raise ValueError(
                f"Generated embedding dimension {len(vector)} does not match provider dimension {provider.dimension}"
            )

        target.embedding = vector
        db.add(target)
        await db.flush()
        return vector

    except Exception as e:
        logger.error(f"Error generating or storing embedding: {e}", exc_info=True)
        raise


__all__ = ["generate_embedding"]
