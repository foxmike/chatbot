from __future__ import annotations

from datetime import datetime, timezone
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    username: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
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
    adaptive_verbosity: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    sessions: Mapped[list[Session]] = relationship(back_populates="user")
    semantic_facts: Mapped[list[SemanticFact]] = relationship(back_populates="user")
    user_sessions: Mapped[list[UserSession]] = relationship(back_populates="user")
    companions: Mapped[list[Companion]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    session_id: Mapped[str] = mapped_column(
        sa.String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    revoked: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
    )

    user: Mapped[User] = relationship(back_populates="user_sessions")


class Companion(Base):
    __tablename__ = "companions"

    companion_id: Mapped[str] = mapped_column(
        sa.String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    gender: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    persona: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    avatar_header: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    backstory: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    personality_sliders: Mapped[dict[str, object] | None] = mapped_column(
        sa.JSON, nullable=True
    )
    humor_style: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    communication_style: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    punctuation_habits: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    sentence_length_profile: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    emoji_style: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    vocabulary_density: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    slang_profile: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    favorite_topics: Mapped[list[str] | None] = mapped_column(sa.JSON, nullable=True)
    appearance_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey(
            "appearances.appearance_id",
            name="companions_appearance_id_fkey",
            use_alter=True,
        ),
        nullable=True,
    )
    # Default voice for conversations with this companion (a Voice Library reference).
    voice_id: Mapped[str | None] = mapped_column(
        sa.String(36),
        sa.ForeignKey("voices.voice_id", name="companions_voice_id_fkey", ondelete="RESTRICT",
                      use_alter=True),
        index=True,
        nullable=True,
    )
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

    user: Mapped[User] = relationship(back_populates="companions")

    def to_character_profile(self) -> "CharacterProfile":
        from chatbot.soul.character import CharacterProfile  # local import avoids circular dep
        defaults = CharacterProfile.defaults()
        return CharacterProfile(
            name=self.name,
            backstory=(
                self.backstory if self.backstory is not None else defaults.backstory
            ),
            personality_sliders=(
                {k: float(v) for k, v in self.personality_sliders.items()}  # type: ignore[arg-type]
                if self.personality_sliders is not None
                else dict(defaults.personality_sliders)
            ),
            humor_style=(
                self.humor_style if self.humor_style is not None else defaults.humor_style
            ),
            communication_style=(
                self.communication_style
                if self.communication_style is not None
                else defaults.communication_style
            ),
            favorite_topics=(
                list(self.favorite_topics)
                if self.favorite_topics is not None
                else list(defaults.favorite_topics)
            ),
        )


class CompanionIdentityModel(Base):
    """Durable adjustments to configuration; session drift never writes here."""

    __tablename__ = "companion_identities"
    companion_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    trait_adjustments: Mapped[dict] = mapped_column(
        sa.JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    # Developed opinions, preferences, interests and self-understanding with
    # evidence (``chatbot/soul/self_model.py``). ``{}`` means no development.
    self_model: Mapped[dict] = mapped_column(
        sa.JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
    __mapper_args__ = {"version_id_col": version}


class CompanionRelationshipModel(Base):
    """One relationship per owned companion, independent of conversations."""

    __tablename__ = "companion_relationships"
    companion_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    type: Mapped[str] = mapped_column(sa.String, nullable=False, server_default=sa.text("'friend'"))
    tone: Mapped[str] = mapped_column(sa.String, nullable=False, server_default=sa.text("'warm'"))
    directness: Mapped[float] = mapped_column(
        sa.Float, nullable=False, server_default=sa.text("0.5")
    )
    warmth: Mapped[float] = mapped_column(sa.Float, nullable=False, server_default=sa.text("0.7"))
    initiative_level: Mapped[float] = mapped_column(
        sa.Float, nullable=False, server_default=sa.text("0.3")
    )
    content_level: Mapped[str] = mapped_column(
        sa.String, nullable=False, server_default=sa.text("'moderate'")
    )
    depth: Mapped[float] = mapped_column(sa.Float, nullable=False, server_default=sa.text("0"))
    completed_turns: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    first_interaction_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_interaction_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class Voice(Base):
    """A saved Voice Library voice: metadata only.

    The identity is the canonical reference recording (a WAV file in the voice library
    directory, never in this row) plus its transcript; Qwen3-TTS Base clones from them.
    `user_id` is NULL only for built-in voices, which every user may use but not modify.
    """

    __tablename__ = "voices"
    __table_args__ = (
        sa.CheckConstraint("method IN ('designed', 'cloned')", name="voices_method_check"),
        sa.CheckConstraint("(user_id IS NULL) = is_builtin", name="voices_owner_check"),
    )

    voice_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    method: Mapped[str] = mapped_column(sa.String(16), nullable=False)          # designed | cloned
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)    # designed voices
    reference_path: Mapped[str] = mapped_column(sa.String(255), nullable=False)  # relative to library root
    reference_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    reference_duration: Mapped[float] = mapped_column(sa.Float, nullable=False)
    reference_sample_rate: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reference_transcript: Mapped[str] = mapped_column(sa.Text, nullable=False)
    language: Mapped[str] = mapped_column(sa.String(32), nullable=False, server_default="English")
    model_family: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    creation_model: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    synthesis_model: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="ready")
    is_builtin: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.sql.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )


class ImageMemory(Base):
    __tablename__ = "image_memories"
    __table_args__ = (
        sa.CheckConstraint(
            "source_type IN ('generated', 'uploaded', 'understood')",
            name="ck_image_memories_source_type",
        ),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(
        sa.String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    source_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id"),
        nullable=True,
    )
    companion_id: Mapped[str | None] = mapped_column(
        sa.String(36),
        sa.ForeignKey("companions.companion_id"),
        index=True,
        nullable=True,
    )
    image_data: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    image_url: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    mime_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    prompt_used: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    seed_used: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    model_used: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    width: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    generation_time_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    detected_objects: Mapped[list[dict[str, object]] | None] = mapped_column(
        sa.JSON,
        nullable=True,
    )
    detected_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    scene_category: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    understanding_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    processing_time_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )


class Appearance(Base):
    __tablename__ = "appearances"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    appearance_id: Mapped[str] = mapped_column(
        sa.String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    companion_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("companions.companion_id"),
        nullable=True,
    )
    character_seed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=42)
    character_description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    visual_traits: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    art_style: Mapped[str] = mapped_column(
        sa.String(128),
        nullable=False,
        default="digital illustration",
    )
    color_palette: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
        onupdate=datetime.utcnow,
    )


class OutfitRecord(Base):
    __tablename__ = "outfit_records"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    outfit_id: Mapped[str] = mapped_column(
        sa.String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    appearance_id: Mapped[str] = mapped_column(
        sa.ForeignKey("appearances.appearance_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False, default="default")
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    visual_tags: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    companion_id: Mapped[str | None] = mapped_column(sa.String(36), sa.ForeignKey("companions.companion_id"), index=True, nullable=True)
    room_id: Mapped[str | None] = mapped_column(sa.ForeignKey("rooms.room_id"), index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    mood_state: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    user_state: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    conversation_mode: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    # Session-level voice override; when NULL the companion's default voice is used.
    voice_id: Mapped[str | None] = mapped_column(
        sa.String(36),
        sa.ForeignKey("voices.voice_id", name="sessions_voice_id_fkey", ondelete="RESTRICT",
                      use_alter=True),
        index=True,
        nullable=True,
    )
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
    companion: Mapped[Companion | None] = relationship()
    room: Mapped[Room | None] = relationship(back_populates="session", foreign_keys=[room_id])
    messages: Mapped[list[Message]] = relationship(back_populates="session", cascade="all, delete-orphan")
    episodes: Mapped[list[Episode]] = relationship(back_populates="session", cascade="all, delete-orphan")
    relationship_configs: Mapped[list[RelationshipConfigModel]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    character_profiles: Mapped[list[CharacterProfileModel]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    identity_states: Mapped[list[IdentityStateModel]] = relationship(back_populates="session", cascade="all, delete-orphan")
    bot_mood_logs: Mapped[list[BotMoodLog]] = relationship(cascade="all, delete-orphan")
    user_state_logs: Mapped[list[UserStateLog]] = relationship(cascade="all, delete-orphan")
    # A room can outlive a conversation; only its optional session reference is cleared.
    referenced_rooms: Mapped[list[Room]] = relationship(foreign_keys="[Room.session_id]")


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
    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"), index=True, nullable=True)
    companion_id: Mapped[str | None] = mapped_column(sa.String(36), sa.ForeignKey("companions.companion_id"), index=True, nullable=True)
    role: Mapped[str] = mapped_column(sa.String, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(_embedding_vector_type(), nullable=True)
    token_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )

    session: Mapped[Session] = relationship(back_populates="messages")
    user: Mapped[User | None] = relationship()
    companion: Mapped[Companion | None] = relationship()
    semantic_facts: Mapped[list[SemanticFact]] = relationship(back_populates="source_message")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sessions.id"), nullable=False)
    extraction_metadata: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
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
    __table_args__ = (
        sa.Index("ix_memory_scope_assertion", "user_id", "scope_key", "assertion_key"),
        sa.Index("ix_memory_scope_slot", "user_id", "scope_key", "slot_key"),
        sa.CheckConstraint("lifecycle_status IN ('active', 'superseded', 'historical', 'suppressed')", name="ck_memory_lifecycle_status"),
        sa.CheckConstraint(
            "source_type IN ('explicit', 'inferred')",
            name="ck_semantic_facts_source_type",
        ),
        sa.CheckConstraint(
            "privacy_level IN ('private', 'temporary', 'permanent')",
            name="ck_semantic_facts_privacy_level",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False)
    extraction_metadata: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="active", server_default="active")
    scope_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    assertion_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    slot_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    reinforcement_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")
    last_reinforced_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("semantic_facts.id", ondelete="SET NULL"), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    fact_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(_embedding_vector_type(), nullable=True)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("messages.id"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(
        sa.String,
        nullable=False,
        default="inferred",
        server_default=sa.text("'inferred'"),
    )
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    privacy_level: Mapped[str] = mapped_column(
        sa.String,
        nullable=False,
        default="temporary",
        server_default=sa.text("'temporary'"),
    )
    pinned: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
        server_default=sa.text("false"),
    )
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
    source_message: Mapped[Message | None] = relationship(back_populates="semantic_facts")


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


class TimelineEntry(Base):
    __tablename__ = "timeline_entries"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.String(64), index=True, nullable=False)
    entry_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="active")
    tags: Mapped[list[str] | None] = mapped_column(sa.JSON, nullable=True)
    metadata_: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )


class NarrativeThread(Base):
    __tablename__ = "narrative_threads"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.String(64), index=True, nullable=False)
    label: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    turns: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    relevance_score: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    last_referenced_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )


class Ritual(Base):
    __tablename__ = "rituals"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    ritual_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    schedule_description: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    next_trigger_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    trigger_prompt: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    metadata_: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )


# Continuity compatibility names point to the migration-backed tables above.
RelationshipTimelineEntryModel = TimelineEntry
CompanionRitualModel = Ritual


class AgencyRitual(Base):
    __tablename__ = "agency_rituals"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    ritual_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    message_template: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    ritual_metadata: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
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


class AgencyTimeline(Base):
    __tablename__ = "agency_timeline"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    occurs_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    context: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    recurring: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    recurrence_rule: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class InitiativeSettings(Base):
    __tablename__ = "initiative_settings"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    level: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="gentle_checkins")
    quiet_hours_start: Mapped[str | None] = mapped_column(sa.String(5), nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(sa.String(5), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class Notification(Base):
    __tablename__ = "agency_notifications"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    notif_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    read: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    dismissed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(
        "ritual_metadata",
        sa.JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class Room(Base):
    __tablename__ = "rooms"

    room_id: Mapped[str] = mapped_column(
        sa.String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    companion_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    room_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    persist_to_durable: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )
    memory_scope: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="room"
    )
    max_context_turns: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=50
    )
    include_embeddings: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True
    )
    scene_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey(
            "scenes.scene_id",
            name="rooms_scene_id_fkey",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("sessions.id"),
        nullable=True,
    )

    scenes: Mapped[list[Scene]] = relationship(
        back_populates="room",
        foreign_keys="[Scene.room_id]",
    )
    scene: Mapped[Scene | None] = relationship(foreign_keys=[scene_id])
    session: Mapped[Session | None] = relationship(back_populates="room", foreign_keys="[Session.room_id]")


class Scene(Base):
    __tablename__ = "scenes"

    scene_id: Mapped[str] = mapped_column(
        sa.String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    room_id: Mapped[str] = mapped_column(
        sa.ForeignKey("rooms.room_id"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(
        sa.String(255), nullable=False, default=""
    )
    activity: Mapped[str] = mapped_column(
        sa.String(255), nullable=False, default=""
    )
    tone: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="neutral"
    )
    intensity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)
    is_paused: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    resumed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    participants: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    scene_context: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(sa.JSON, nullable=True)

    room: Mapped[Room] = relationship(back_populates="scenes", foreign_keys=[room_id])


class BackgroundJob(Base):
    """Durable completed-turn work dispatch row (Phase 2 design, Task 2.2).

    Job identity is unique per persisted logical turn and work type: the
    ``uq_background_jobs_turn_identity`` constraint on ``(task_type, turn_id)``
    makes re-enqueueing the same turn's work conflict-safe, so callers can
    insert with ``ON CONFLICT DO NOTHING``. Only the authoritative completion
    hook schedules a job, in the same transaction as the completed messages,
    so a rollback leaves neither completed turn nor job.

    Lease recovery: a ``running`` job whose ``claimed_at`` exceeds the fixed
    lease timeout is eligible again (claiming is done with ``SKIP LOCKED``
    row locking, so a live claim cannot be double-claimed). Retries are
    bounded by ``attempt_count`` against a finite attempt limit; exhaustion
    persists ``failed`` with ``last_error`` and ``completed_at``. Never
    silently discard terminal work.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_background_jobs_status",
        ),
        sa.UniqueConstraint(
            "task_type", "turn_id", name="uq_background_jobs_turn_identity"
        ),
        sa.Index(
            "ix_background_jobs_status_available_at", "status", "available_at"
        ),
    )

    # Deterministic UUID job key: the identity is the (task_type, turn_id)
    # pair above; the primary key is generated per row and indexed.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    task_type: Mapped[str] = mapped_column(sa.String, nullable=False)
    # Stable persisted turn id (String(64) to match messages.turn_id); nullable
    # only when identity is derived from the persisted user/assistant message
    # IDs carried in the payload instead.
    turn_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id"), nullable=True, index=True
    )
    # Nullable for companion-less sessions.
    companion_id: Mapped[str | None] = mapped_column(
        sa.String(36),
        sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Compact work parameters / references (turn/message ids), never a copied
    # unbounded conversation history; the worker loads the persisted turn
    # messages. Bounded by the writer, not by the database.
    payload: Mapped[dict[str, object]] = mapped_column(
        sa.JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        sa.String,
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    # When the job becomes claimable; now() for immediate work, later for
    # bounded exponential retry delay.
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # Bounded error text; writers truncate before persisting.
    last_error: Mapped[str | None] = mapped_column(sa.String(2000), nullable=True)
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


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TraceRecord(Base):
    __tablename__ = "observability_traces"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        sa.String(36), unique=True, nullable=False, index=True
    )
    turn_id: Mapped[str] = mapped_column(
        sa.String(36), nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(
        sa.String(36), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        sa.String(36), nullable=True, index=True
    )
    model_provider: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    task_class: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    completion_tokens: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    cost_usd_micros: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    total_latency_ms: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0
    )
    retrieve_latency_ms: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0
    )
    prompt: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default=""
    )
    routing_decision: Mapped[dict[str, object]] = mapped_column(
        sa.JSON, nullable=False, default=dict
    )
    retrieval_snippets: Mapped[list[object]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    steps: Mapped[list[object]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )


class UsageRecord(Base):
    __tablename__ = "observability_usage"
    __table_args__ = (
        sa.UniqueConstraint("session_id", "model_provider", "model_id"),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        sa.String(36), nullable=False, index=True
    )
    model_provider: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(
        sa.String(128), nullable=False, index=True
    )
    turn_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1
    )
    prompt_tokens: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    completion_tokens: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    cost_usd_micros: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    avg_latency_ms: Mapped[float] = mapped_column(
        sa.Float, nullable=False, default=0
    )
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


__all__ = [
    "Voice",
    "AgencyRitual",
    "AgencyTimeline",
    "Appearance",
    "BackgroundJob",
    "Base",
    "BotMoodLog",
    "CharacterProfileModel",
    "Companion",
    "Entity",
    "Episode",
    "IdentityStateModel",
    "ImageMemory",
    "InitiativeSettings",
    "Message",
    "NarrativeThread",
    "Notification",
    "OutfitRecord",
    "RelationshipConfigModel",
    "Ritual",
    "Room",
    "Scene",
    "SemanticFact",
    "Session",
    "TimelineEntry",
    "TraceRecord",
    "UsageRecord",
    "User",
    "UserSession",
    "UserStateLog",
]


class MemoryEvidence(Base):
    """Immutable extraction evidence; many source turns can support one fact."""
    __tablename__ = "memory_evidence"
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    turn_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    assertion_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence: Mapped[str] = mapped_column(sa.Text, nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    source_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class MemoryRelation(Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "target_id", "relation", name="uq_memory_relation"),
        sa.CheckConstraint("source_id <> target_id", name="ck_memory_relation_distinct"),
        sa.CheckConstraint("relation IN ('supersedes', 'changed_from', 'conflicts_with')", name="ck_memory_relation_kind"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memory_evidence.evidence_id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
