"""Redis connection pool — initialized from REDIS_URL at import time.

Provides:
    get_redis() → async generator yielding a Redis client, or None when
                  Redis is unavailable (pool creation failed or ping fails).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Optional

import redis.asyncio as aioredis

from chatbot.config import get_settings

logger = logging.getLogger(__name__)

REDIS_URL = str(get_settings().redis_url)

_pool: Optional[aioredis.ConnectionPool] = None

try:
    _pool = aioredis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
except Exception:
    logger.warning("Failed to create Redis connection pool from %s", REDIS_URL, exc_info=True)


async def get_redis() -> AsyncGenerator[aioredis.Redis | None, None]:
    """Yield a Redis client for the current request lifecycle.

    Yields ``None`` when Redis is unavailable — either because the connection
    pool could not be created, or because the ``PING`` probe failed.  Callers
    should already tolerate a missing Redis dependency.
    """
    if _pool is None:
        yield None
        return

    client: aioredis.Redis = aioredis.Redis(connection_pool=_pool)
    try:
        await client.ping()
        yield client
    except Exception:
        logger.warning("Redis PING failed — yielding None", exc_info=True)
        yield None
    finally:
        await client.aclose()


__all__ = ["get_redis"]
