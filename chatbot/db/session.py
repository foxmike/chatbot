from collections.abc import AsyncGenerator
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.config import get_settings

DATABASE_URL = get_settings().database_url


def _raise_missing_asyncpg(*_args: object, **_kwargs: object) -> None:
    error = ModuleNotFoundError("No module named 'asyncpg'")
    error.name = "asyncpg"
    raise error


from sqlalchemy.pool import NullPool

try:
    engine = create_async_engine(DATABASE_URL)
    streaming_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
except ModuleNotFoundError as exc:
    if exc.name != "asyncpg":
        raise

    missing_asyncpg = SimpleNamespace(
        Binary=lambda value: value,
        connect=_raise_missing_asyncpg,
        paramstyle="numeric_dollar",
    )
    engine = create_async_engine(DATABASE_URL, module=missing_asyncpg)
    streaming_engine = create_async_engine(DATABASE_URL, module=missing_asyncpg, poolclass=NullPool)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

streaming_session_factory = async_sessionmaker(
    streaming_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


__all__ = ["async_session_factory", "streaming_session_factory", "get_db"]
