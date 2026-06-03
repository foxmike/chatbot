from __future__ import annotations

from datetime import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ModuleNotFoundError:

    class Vector(sa.types.UserDefinedType):  # type: ignore[no-redef]
        cache_ok = True

        def __init__(self, dimensions: int | None = None, dim: int | None = None) -> None:
            self.dimensions = dimensions if dimensions is not None else dim

        def get_col_spec(self, **_kw: object) -> str:
            if self.dimensions is None:
                return "VECTOR"
            return f"VECTOR({self.dimensions})"


def _embedding_vector_type() -> Vector:
    try:
        return Vector(dimensions=None)
    except TypeError:
        return Vector(None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    external_id: Mapped[str] = mapped_column(sa.String, unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    sessions: Mapped[list[Session]] = relationship(back_populates="user")
    semantic_facts: Mapped[list[SemanticFact]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    mood_state: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    user_state: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    conversation_mode: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list[Message]] = relationship(back_populates="session")
    episodes: Mapped[list[Episode]] = relationship(back_populates="session")
    relationship_configs: Mapped[list[RelationshipConfigModel]] = relationship(
        back_populates="session"
    )
    character_profiles: Mapped[list[CharacterProfileModel]] = relationship(
        back_populates="session"
    )
    identity_states: Mapped[list[IdentityStateModel]] = relationship(back_populates="session")


class RelationshipConfigModel(Base):
    __tablename__ = "relationship_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    type: Mapped[str] = mapped_column(sa.String, nullable=False)
    tone: Mapped[str] = mapped_column(sa.String, nullable=False)
    directness: Mapped[float] = mapped_column(sa.Float, nullable=False)
    warmth: Mapped[float] = mapped_column(sa.Float, nullable=False)
    initiative_level: Mapped[float] = mapped_column(sa.Float, nullable=False)
    content_level: Mapped[str] = mapped_column(sa.String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="relationship_configs")


class CharacterProfileModel(Base):
    __tablename__ = "character_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.String, nullable=False)
    backstory: Mapped[str] = mapped_column(sa.Text, nullable=False)
    personality_sliders: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    humor_style: Mapped[str] = mapped_column(sa.String, nullable=False)
    communication_style: Mapped[str] = mapped_column(sa.String, nullable=False)
    favorite_topics: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="character_profiles")


class IdentityStateModel(Base):
    __tablename__ = "identity_states"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    fixed: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    slow: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    mood: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="identity_states")


class BotMoodLog(Base):
    __tablename__ = "bot_mood_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    mood: Mapped[str] = mapped_column(sa.String, nullable=False)
    energy: Mapped[float] = mapped_column(sa.Float, nullable=False)
    closeness: Mapped[float] = mapped_column(sa.Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class UserStateLog(Base):
    __tablename__ = "user_state_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    primary_state: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    evidence_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_messages_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(sa.String, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(_embedding_vector_type(), nullable=True)
    token_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="messages")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sessions.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.String, nullable=False)
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    start_message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("messages.id"),
        nullable=True,
    )
    end_message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("messages.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="episodes")
    start_message: Mapped[Message | None] = relationship(foreign_keys=[start_message_id])
    end_message: Mapped[Message | None] = relationship(foreign_keys=[end_message_id])


class SemanticFact(Base):
    __tablename__ = "semantic_facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    fact_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(_embedding_vector_type(), nullable=True)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("messages.id"),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="semantic_facts")
    source_message: Mapped[Message | None] = relationship()


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(sa.String, index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.String, nullable=False)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


__all__ = [
    "Base",
    "BotMoodLog",
    "CharacterProfileModel",
    "Entity",
    "Episode",
    "IdentityStateModel",
    "Message",
    "RelationshipConfigModel",
    "SemanticFact",
    "Session",
    "User",
    "UserStateLog",
]
