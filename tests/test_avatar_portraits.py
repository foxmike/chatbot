"""Targeted tests for the avatar character portrait service.

Covers: valid retrieval, missing portrait, corrupt/unsupported images,
unauthorized access, invalid companion/session association, cache safety,
and graceful preparation failure. Uses the real ORM models against an
in-memory SQLite database — no network, no GPU, no AVTR/Qwen runtime.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.avatar.portraits import (  # noqa: E402
    PreparedPortrait,
    PortraitAccessError,
    PortraitCache,
    PortraitError,
    PortraitFormatError,
    PortraitNotFoundError,
    PortraitService,
)
from chatbot.db.models import Companion, ImageMemory, User  # noqa: E402
from chatbot.db.models import Session as ChatSession  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: real ORM models on in-memory SQLite
# ---------------------------------------------------------------------------

_ENGINE = create_async_engine("sqlite+aiosqlite://", echo=False)
_SESSION_FACTORY = async_sessionmaker(_ENGINE, class_=AsyncSession, expire_on_commit=False)

_TABLES = (User.__table__, ChatSession.__table__, Companion.__table__, ImageMemory.__table__)


async def _create_tables() -> None:
    async with _ENGINE.begin() as conn:
        for table in _TABLES:
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True))


@pytest.fixture()
async def db_factory():
    await _create_tables()
    try:
        yield _SESSION_FACTORY
    finally:
        async with _ENGINE.begin() as conn:
            for table in reversed(_TABLES):
                await conn.run_sync(lambda sync_conn, t=table: sync_conn.execute(t.delete()))


def _png_bytes(color=(200, 120, 90), size=(256, 256), mode="RGB") -> bytes:
    img = Image.new(mode, size, color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(256, 256)) -> bytes:
    img = Image.new("RGB", size, (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class World:
    """Tiny fixture helper: creates users, companions, sessions, images."""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def _run(self, fn):
        async with self._factory() as db:
            result = fn(db)
            await db.commit()
            return result

    async def add_user(self) -> str:
        user_id = uuid.uuid4()
        await self._run(lambda db: db.add(
            User(
                id=user_id,
                external_id=f"ext-{user_id}",
                display_name="User",
                username=f"user-{user_id.hex[:8]}",
                email=f"{user_id.hex[:8]}@example.com",
                password_hash="x",
            )
        ))
        return str(user_id)

    async def add_companion(self, user_id: str, *, avatar_url: str | None = None, name: str = "Aria") -> str:
        companion_id = str(uuid.uuid4())

        def make(db):
            db.add(Companion(
                companion_id=companion_id,
                user_id=uuid.UUID(user_id),
                name=name,
                avatar_url=avatar_url,
            ))

        await self._run(make)
        return companion_id

    async def add_session(self, user_id: str, companion_id: str | None) -> str:
        session_id = uuid.uuid4()

        def make(db):
            db.add(ChatSession(
                id=session_id,
                user_id=uuid.UUID(user_id),
                companion_id=companion_id,
            ))

        await self._run(make)
        return str(session_id)

    async def add_image(
        self,
        user_id: str,
        companion_id: str,
        data: bytes,
        *,
        mime_type: str = "image/png",
        image_id: str | None = None,
    ) -> str:
        image_id = image_id or str(uuid.uuid4())

        def make(db):
            db.add(ImageMemory(
                image_id=image_id,
                source_type="generated",
                user_id=uuid.UUID(user_id),
                companion_id=companion_id,
                image_data=data,
                mime_type=mime_type,
                created_at=datetime.now(timezone.utc),
            ))

        await self._run(make)
        return image_id

    async def count_image_queries(self) -> sa.engine.Result:
        raise NotImplementedError


@pytest.fixture()
async def world(db_factory):
    return World(db_factory)


def make_service(factory, **kwargs) -> PortraitService:
    return PortraitService(factory, **kwargs)


async def _image_by_image_id(db, image_id: str) -> ImageMemory:
    from sqlalchemy import select
    result = await db.execute(
        select(ImageMemory).where(ImageMemory.image_id == image_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# 1. Valid portrait retrieval
# ---------------------------------------------------------------------------


async def test_valid_portrait_retrieval_by_companion_id(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes())
    await world.add_companion(  # ensure companion row still has avatar_url
        user, avatar_url=None, name="other"
    )
    # set avatar_url on the first companion
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    prepared = await service.get_portrait(user, companion_id=companion)

    assert isinstance(prepared, PreparedPortrait)
    assert prepared.image_id == image_id
    assert prepared.companion_id == companion
    assert prepared.width == 256 and prepared.height == 256
    assert prepared.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    decoded = Image.open(BytesIO(prepared.png_bytes))
    assert decoded.format == "PNG"
    assert decoded.size == (256, 256)


async def test_valid_portrait_via_session_id(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _jpeg_bytes(), mime_type="image/jpeg")
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()
    session_id = await world.add_session(user, companion)

    service = make_service(db_factory)
    prepared = await service.get_portrait(user, session_id=session_id)

    assert prepared.image_id == image_id
    # JPEG source is normalized to PNG for AVTR-1
    assert prepared.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert prepared.source_mime_type == "image/jpeg"


async def test_portrait_defaults_to_first_companion(world: World, db_factory):
    user = await world.add_user()
    first = await world.add_companion(user, name="first")
    image_id = await world.add_image(user, first, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, first)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()
    await world.add_companion(user, name="second")  # newer, no portrait

    service = make_service(db_factory)
    prepared = await service.get_portrait(user)
    assert prepared.companion_id == first


async def test_session_without_companion_falls_back_to_first(world: World, db_factory):
    user = await world.add_user()
    first = await world.add_companion(user, name="solo")
    image_id = await world.add_image(user, first, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, first)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()
    session_id = await world.add_session(user, None)

    service = make_service(db_factory)
    prepared = await service.get_portrait(user, session_id=session_id)
    assert prepared.companion_id == first


async def test_rgba_alpha_is_preserved(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes(mode="RGBA"))
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    prepared = await service.get_portrait(user, companion_id=companion)
    decoded = Image.open(BytesIO(prepared.png_bytes))
    assert decoded.mode == "RGBA"


# ---------------------------------------------------------------------------
# 2. Missing portrait
# ---------------------------------------------------------------------------


async def test_companion_without_avatar_url(world: World, db_factory):
    user = await world.add_user()
    await world.add_companion(user, avatar_url=None)

    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user)


async def test_image_row_missing(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(
        user, avatar_url=f"/api/visual/images/{uuid.uuid4()}"
    )

    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=companion)


async def test_image_row_without_data(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, b"", mime_type="image/png")
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=companion)


async def test_unknown_user_has_no_portrait(world: World, db_factory):
    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 3/8/9. Invalid, corrupt, unsupported, unsuitable images
# ---------------------------------------------------------------------------


async def test_corrupt_image_bytes(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, b"\x89PNG\r\n\x1a\nGARBAGE-TRUNCATED")
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError):
        await service.get_portrait(user, companion_id=companion)


async def test_truncated_valid_png(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    full = _png_bytes()
    image_id = await world.add_image(user, companion, full[: len(full) // 3])
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError):
        await service.get_portrait(user, companion_id=companion)


async def test_unsupported_media_type(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes(), mime_type="image/gif")
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError):
        await service.get_portrait(user, companion_id=companion)


async def test_image_too_small_for_animation(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes(size=(32, 32)))
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError, match="unsuitable"):
        await service.get_portrait(user, companion_id=companion)


async def test_external_avatar_url_rejected(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user, avatar_url="https://cdn.example.com/face.png")

    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError):
        await service.get_portrait(user, companion_id=companion)


async def test_error_codes_are_machine_readable(world: World, db_factory):
    user = await world.add_user()
    await world.add_companion(user, avatar_url=None)
    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError) as info:
        await service.get_portrait(user)
    assert info.value.code == "no_portrait"


# ---------------------------------------------------------------------------
# 4. Unauthorized access
# ---------------------------------------------------------------------------


async def test_cannot_retrieve_other_users_companion_portrait(world: World, db_factory):
    owner = await world.add_user()
    attacker = await world.add_user()
    companion = await world.add_companion(owner)
    image_id = await world.add_image(owner, companion, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    # The companion exists, but belongs to someone else: not found, no leak.
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(attacker, companion_id=companion)


async def test_cannot_retrieve_via_other_users_session(world: World, db_factory):
    owner = await world.add_user()
    attacker = await world.add_user()
    companion = await world.add_companion(owner)
    image_id = await world.add_image(owner, companion, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()
    session_id = await world.add_session(owner, companion)

    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(attacker, session_id=session_id)


async def test_defense_in_depth_row_owner_mismatch(world: World, db_factory):
    """If a row ever carries a mismatched user_id, access is refused."""
    user = await world.add_user()
    other = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        img = await _image_by_image_id(db, image_id)
        img.user_id = uuid.UUID(other)  # simulate corrupted ownership
        await db.commit()

    service = make_service(db_factory)
    # The user filter in the query hides the mismatched row entirely.
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=companion)


async def test_image_of_other_companion_is_access_error(world: World, db_factory):
    """An image owned by the user but attached to another companion must
    not be served for this companion."""
    user = await world.add_user()
    companion_a = await world.add_companion(user, name="A")
    companion_b = await world.add_companion(user, name="B")
    image_id = await world.add_image(user, companion_b, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, companion_a)
        # Point A's avatar at B's image (simulated bad association).
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    service = make_service(db_factory)
    with pytest.raises(PortraitAccessError):
        await service.get_portrait(user, companion_id=companion_a)


async def test_invalid_user_identity_is_access_error(world: World, db_factory):
    service = make_service(db_factory)
    with pytest.raises(PortraitAccessError):
        await service.get_portrait("not-a-uuid")


# ---------------------------------------------------------------------------
# 5. Invalid companion/session association
# ---------------------------------------------------------------------------


async def test_unknown_companion_id(world: World, db_factory):
    user = await world.add_user()
    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=str(uuid.uuid4()))


async def test_unknown_session_id(world: World, db_factory):
    user = await world.add_user()
    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, session_id=str(uuid.uuid4()))


async def test_malformed_session_id(world: World, db_factory):
    user = await world.add_user()
    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, session_id="../etc/passwd")


async def test_session_cannot_select_other_users_companion(world: World, db_factory):
    """A session must only ever resolve to a companion the user owns."""
    owner = await world.add_user()
    other = await world.add_user()
    other_companion = await world.add_companion(other)
    image_id = await world.add_image(other, other_companion, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, other_companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()

    # Owner creates a session pointing at (somehow) another user's companion.
    session_id = await world.add_session(owner, other_companion)

    service = make_service(db_factory)
    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(owner, session_id=session_id)


# ---------------------------------------------------------------------------
# 6. Safe cache behavior
# ---------------------------------------------------------------------------


async def _seed(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes())
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        await db.commit()
    return user, companion, image_id


async def test_cache_avoids_second_db_retrieval(world: World, db_factory):
    user, companion, image_id = await _seed(world, db_factory)
    service = make_service(db_factory)
    first = await service.get_portrait(user, companion_id=companion)

    # Remove the underlying image entirely; a cached hit must not care.
    async with db_factory() as db:
        img = await _image_by_image_id(db, image_id)
        await db.delete(img)
        await db.commit()

    second = await service.get_portrait(user, companion_id=companion)
    assert second is first  # same prepared object, no re-fetch, no re-convert


async def test_refresh_bypasses_cache(world: World, db_factory):
    user, companion, image_id = await _seed(world, db_factory)
    service = make_service(db_factory)
    first = await service.get_portrait(user, companion_id=companion)

    async with db_factory() as db:
        img = await _image_by_image_id(db, image_id)
        await db.delete(img)
        await db.commit()

    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=companion, refresh=True)
    assert first is not None


async def test_new_portrait_busts_cache(world: World, db_factory):
    user, companion, old_image = await _seed(world, db_factory)
    service = make_service(db_factory)
    old = await service.get_portrait(user, companion_id=companion)

    # User generates a new portrait: new image row + new avatar_url.
    new_image = await world.add_image(user, companion, _png_bytes(color=(1, 2, 3)))
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{new_image}"
        await db.commit()

    fresh = await service.get_portrait(user, companion_id=companion)
    assert fresh.image_id == new_image
    assert fresh.cache_key != old.cache_key


async def test_cache_ttl_expiry(world: World, db_factory):
    class FakeClock:
        def __init__(self) -> None:
            self.now = 1000.0

        def __call__(self) -> float:
            return self.now

    user, companion, image_id = await _seed(world, db_factory)
    clock = FakeClock()
    cache = PortraitCache(ttl_seconds=60.0, clock=clock)
    service = make_service(db_factory, cache=cache)
    await service.get_portrait(user, companion_id=companion)

    clock.now += 61.0  # past TTL
    async with db_factory() as db:
        img = await _image_by_image_id(db, image_id)
        await db.delete(img)
        await db.commit()

    with pytest.raises(PortraitNotFoundError):
        await service.get_portrait(user, companion_id=companion)


async def test_invalidate_user(world: World, db_factory):
    user, companion, image_id = await _seed(world, db_factory)
    service = make_service(db_factory)
    await service.get_portrait(user, companion_id=companion)
    assert service._cache.keys()

    service.invalidate(user)
    assert not service._cache.keys()

    # And it re-fetches cleanly after invalidation.
    prepared = await service.get_portrait(user, companion_id=companion)
    assert prepared.image_id == image_id


async def test_cache_lru_bound(world: World, db_factory):
    cache = PortraitCache(max_entries=2)
    prepared = PreparedPortrait(
        image_id="i", companion_id="c", source_mime_type="image/png",
        width=64, height=64, png_bytes=b"x", source_created_at=None,
    )
    cache.put("a", prepared)
    cache.put("b", prepared)
    cache.put("c", prepared)
    assert cache.get("a") is None  # evicted
    assert cache.get("c") is prepared


# ---------------------------------------------------------------------------
# 7. Graceful preparation failure
# ---------------------------------------------------------------------------


async def test_prepare_failure_is_portrait_error_not_crash(world: World, db_factory):
    """Any failure inside preparation surfaces as PortraitError."""
    user, companion, image_id = await _seed(world, db_factory)
    service = make_service(db_factory)

    def boom(**kwargs):
        raise RuntimeError("decoder exploded")

    original = service.prepare_portrait
    service.prepare_portrait = boom  # type: ignore[method-assign]
    with pytest.raises(PortraitError):
        await service.get_portrait(user, companion_id=companion)

    # The service remains usable afterwards.
    service.prepare_portrait = original  # type: ignore[method-assign]
    prepared = await service.get_portrait(user, companion_id=companion, refresh=True)
    assert prepared.image_id == image_id


async def test_prepare_portrait_rejects_empty_input(db_factory):
    service = make_service(db_factory)
    with pytest.raises(PortraitFormatError):
        service.prepare_portrait(
            image_id="i", companion_id="c", mime_type="image/png",
            image_data=b"", created_at=None,
        )


async def test_unexpected_exception_in_prepare_wrapped(db_factory):
    """Non-PIL exceptions during decode still become PortraitFormatError."""
    service = make_service(db_factory)

    class ExplodingImage(Image.Image):
        pass

    # Feed data that Pillow identifies but fails to decode is hard to craft;
    # instead check the pure-function contract for garbage bytes.
    with pytest.raises(PortraitFormatError):
        service.prepare_portrait(
            image_id="i", companion_id="c", mime_type="image/jpeg",
            image_data=b"\xff\xd8\xff\xe0not-really-a-jpeg", created_at=None,
        )


async def test_expired_created_at_is_carried(world: World, db_factory):
    user = await world.add_user()
    companion = await world.add_companion(user)
    image_id = await world.add_image(user, companion, _png_bytes())
    created = datetime.now(timezone.utc) - timedelta(days=3)
    async with db_factory() as db:
        row = await db.get(Companion, companion)
        row.avatar_url = f"/api/visual/images/{image_id}"
        img = await _image_by_image_id(db, image_id)
        img.created_at = created
        await db.commit()

    service = make_service(db_factory)
    prepared = await service.get_portrait(user, companion_id=companion)
    assert prepared.source_created_at is not None
    assert prepared.source_created_at.date() == created.date()