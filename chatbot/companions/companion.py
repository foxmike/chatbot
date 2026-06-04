"""Companion model and per-user companion management logic."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CompanionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    persona: str | None = None
    description: str | None = None
    avatar_url: str | None = None


class CompanionResponse(BaseModel):
    companion_id: str
    user_id: str
    name: str
    persona: str | None
    description: str | None
    avatar_url: str | None
    created_at: str
    updated_at: str | None


class CompanionListResponse(BaseModel):
    companions: list[CompanionResponse]


COMPANION_MODEL_DEFINITIONS: dict[str, dict[str, dict]] = {
    "companions": {
        "companion_id": {"type": "String(36)", "primary_key": True, "default": uuid4},
        "user_id": {
            "type": "String(36)",
            "foreign_key": "users.user_id",
            "nullable": False,
        },
        "name": {"type": "String(128)", "nullable": False},
        "persona": {"type": "Text", "nullable": True},
        "description": {"type": "Text", "nullable": True},
        "avatar_url": {"type": "String(512)", "nullable": True},
        "created_at": {"type": "DateTime", "default": "func.now()"},
        "updated_at": {"type": "DateTime", "onupdate": "func.now()"},
    },
}


def _row_to_companion(row: object) -> CompanionResponse:
    m = row._mapping  # noqa: SLF001
    return CompanionResponse(
        companion_id=str(m["companion_id"]),
        user_id=str(m["user_id"]),
        name=str(m["name"]),
        persona=m["persona"],
        description=m["description"],
        avatar_url=m["avatar_url"],
        created_at=str(m["created_at"]),
        updated_at=str(m["updated_at"]) if m["updated_at"] is not None else None,
    )


class CompanionManager:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: str, data: CompanionCreateRequest) -> CompanionResponse:
        companion_id = str(uuid4())
        stmt = text(
            """INSERT INTO companions (companion_id, user_id, name, persona, description, avatar_url, created_at)
               VALUES (:companion_id, :user_id, :name, :persona, :description, :avatar_url, now())
               RETURNING *"""
        )
        result = await self._session.execute(
            stmt,
            dict(
                companion_id=companion_id,
                user_id=user_id,
                name=data.name,
                persona=data.persona,
                description=data.description,
                avatar_url=data.avatar_url,
            ),
        )
        row = result.fetchone()
        await self._session.commit()
        return _row_to_companion(row)

    async def list_by_user(self, user_id: str) -> CompanionListResponse:
        stmt = text("SELECT * FROM companions WHERE user_id = :user_id")
        result = await self._session.execute(stmt, dict(user_id=user_id))
        rows = result.fetchall()
        return CompanionListResponse(
            companions=[_row_to_companion(r) for r in rows]
        )

    async def get(self, user_id: str, companion_id: str) -> CompanionResponse | None:
        stmt = text(
            "SELECT * FROM companions WHERE companion_id = :companion_id"
        )
        result = await self._session.execute(stmt, dict(companion_id=companion_id))
        row = result.fetchone()
        if row is None:
            return None
        mapped = row._mapping  # noqa: SLF001
        if str(mapped["user_id"]) != user_id:
            return None
        return _row_to_companion(row)

    async def update(
        self, user_id: str, companion_id: str, data: CompanionCreateRequest
    ) -> CompanionResponse | None:
        existing = await self.get(user_id, companion_id)
        if existing is None:
            return None

        stmt = text(
            """UPDATE companions
               SET name = :name, persona = :persona, description = :description,
                   avatar_url = :avatar_url, updated_at = now()
               WHERE companion_id = :companion_id
               RETURNING *"""
        )
        result = await self._session.execute(
            stmt,
            dict(
                name=data.name,
                persona=data.persona,
                description=data.description,
                avatar_url=data.avatar_url,
                companion_id=companion_id,
            ),
        )
        row = result.fetchone()
        await self._session.commit()
        return _row_to_companion(row)

    async def delete(self, user_id: str, companion_id: str) -> bool:
        existing = await self.get(user_id, companion_id)
        if existing is None:
            return False

        stmt = text("DELETE FROM companions WHERE companion_id = :companion_id")
        await self._session.execute(stmt, dict(companion_id=companion_id))
        await self._session.commit()
        return True