from collections.abc import AsyncGenerator
import os
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/chatbot"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _raise_missing_asyncpg(*_args: object, **_kwargs: object) -> None:
    error = ModuleNotFoundError("No module named 'asyncpg'")
    error.name = "asyncpg"
    raise error


try:
    engine = create_async_engine(DATABASE_URL)
except ModuleNotFoundError as exc:
    if exc.name != "asyncpg":
        raise

    missing_asyncpg = SimpleNamespace(
        Binary=lambda value: value,
        connect=_raise_missing_asyncpg,
        paramstyle="numeric_dollar",
    )
    engine = create_async_engine(DATABASE_URL, module=missing_asyncpg)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


__all__ = ["async_session_factory", "get_db"]
