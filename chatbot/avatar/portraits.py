"""Character portrait service for the avatar subsystem.

Resolves the currently selected companion's portrait from chatbot's own
database (``Companion.avatar_url`` → ``ImageMemory``), enforces chatbot's
authenticated user/session/companion ownership model, validates and
normalizes the image to the PNG bytes AVTR-1 expects, and caches the
prepared result so repeated use does not re-fetch or re-convert images.

Ownership model (enforced on every request, never trusted from cache keys
alone):

* the caller must pass the ``user_id`` of the *authenticated* user
  (``CurrentUser.user_id`` from ``chatbot.auth.dependencies``);
* the companion must belong to that user;
* a session id, when given, must belong to that user and may only select
  one of that user's companions;
* the ``ImageMemory`` row backing the portrait must belong to that user
  *and* that companion (defense in depth — the query already filters, the
  row is re-checked after load).

A user can therefore never reach another user's private portrait through
this service: cross-user companions and images resolve to "not found"
(no existence leak), and any row that somehow carries a mismatched owner
raises :class:`PortraitAccessError`.

Deliberate non-goals (see docs/avatar/portrait-loading.md): this module
does not manage LLM turns, Qwen inference, browser audio, the WebSocket
coordinator, or the AVTR/TensorRT runtime. It produces prepared PNG bytes
and leaves portrait loading into AVTR-1 to the media runtime.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db.models import Companion, ImageMemory, Session as ChatSession

__all__ = [
    "PreparedPortrait",
    "PortraitAccessError",
    "PortraitCache",
    "PortraitError",
    "PortraitFormatError",
    "PortraitNotFoundError",
    "PortraitService",
]

#: URL prefix under which chatbot serves stored image bytes.
_VISUAL_IMAGE_PREFIX = "/api/visual/images/"

#: Image encodings accepted as portrait sources. Anything else (GIF, SVG,
#: BMP, ...) is rejected before decoding rather than blindly converted.
_SUPPORTED_SOURCE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})

#: Smallest edge length considered usable for facial animation. AVTR-1
#: runs face detection and landmark models on the portrait; a thumbnail
#: below this size would either fail detection or animate garbage.
MIN_PORTRAIT_DIMENSION = 64


class PortraitError(Exception):
    """Base class for portrait-service failures.

    Carries a machine-readable ``code`` so callers (API layer, WebSocket
    coordinator) can map failures onto protocol/API error codes without
    string matching.
    """

    code = "portrait_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class PortraitNotFoundError(PortraitError):
    """The selected companion has no usable portrait reference, or the
    companion/session/image does not exist for the authenticated user."""

    code = "portrait_not_found"


class PortraitAccessError(PortraitError):
    """A loaded row does not belong to the authenticated user/companion.

    Should be unreachable through normal queries; kept as defense in depth
    so a future code path that bypasses the ownership filter cannot leak
    another user's portrait bytes.
    """

    code = "portrait_access_denied"


class PortraitFormatError(PortraitError):
    """The portrait data is corrupt, unsupported, or unsuitable for
    animation (e.g. unreadable bytes, no face-sized image, external URL)."""

    code = "portrait_unusable"


@dataclass(frozen=True)
class PreparedPortrait:
    """A validated, animation-ready portrait.

    ``png_bytes`` is a PNG re-encoding of the source image with alpha
    preserved when present (AVTR-1 treats a 4-channel portrait as a
    matting candidate). ``cache_key`` captures everything that identifies
    the prepared artifact; a changed portrait always produces a new key.
    """

    image_id: str
    companion_id: str
    source_mime_type: str
    width: int
    height: int
    png_bytes: bytes
    source_created_at: datetime | None

    @property
    def cache_key(self) -> str:
        return (
            f"{self.companion_id}:{self.image_id}:"
            f"{self.source_created_at.isoformat() if self.source_created_at else '-'}:"
            f"{len(self.png_bytes)}"
        )


class PortraitCache:
    """Small in-process LRU cache for prepared portraits.

    Entries are keyed by ``(user_id, companion_id, avatar_url)`` and expire
    after ``ttl_seconds``. Keying on the companion's ``avatar_url`` keeps
    the cache safe: generating a new portrait changes ``avatar_url`` (it
    embeds the new ``image_id``), so a stale entry is never served; the
    TTL additionally bounds staleness if ``avatar_url`` is rewritten in
    place to the same value. Only *validated, already-normalized* PNG
    bytes are cached, so a cache hit can never serve a corrupt image.

    Not designed for cross-process sharing — each chatbot worker process
    keeps its own cache, which is sufficient to avoid repeated DB
    retrieval and image conversion within a worker.
    """

    def __init__(
        self,
        *,
        max_entries: int = 32,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, PreparedPortrait]] = OrderedDict()

    def get(self, key: str) -> PreparedPortrait | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, prepared = entry
        if self._clock() - stored_at > self._ttl_seconds:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return prepared

    def put(self, key: str, prepared: PreparedPortrait) -> None:
        self._entries[key] = (self._clock(), prepared)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def keys(self) -> list[str]:
        """Current cache keys (order not specified)."""
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()


def _parse_visual_image_id(avatar_url: str) -> str:
    """Extract the ``image_id`` from a chatbot visual image URL.

    Raises :class:`PortraitFormatError` for anything that is not a local
    chatbot image reference (external portrait URLs are not supported by
    the avatar subsystem — the bytes live outside chatbot's database and
    cannot be ownership-checked).
    """
    url = avatar_url.strip()
    if url.startswith(_VISUAL_IMAGE_PREFIX):
        image_id = url[len(_VISUAL_IMAGE_PREFIX):].split("?", 1)[0].strip("/")
        if image_id:
            return image_id
        raise PortraitFormatError("Portrait URL has an empty image id")
    if "://" in url or url.startswith("//"):
        raise PortraitFormatError(
            "External portrait URLs are not supported by the avatar subsystem; "
            "the companion portrait must reference a stored chatbot image"
        )
    raise PortraitFormatError(f"Unrecognized portrait URL: {avatar_url[:120]!r}")


def _user_uuid(user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise PortraitAccessError("Invalid user identity") from exc


class PortraitService:
    """Resolve, validate, prepare, and cache the selected companion's portrait.

    Parameters
    ----------
    session_factory:
        Async session factory (e.g. ``chatbot.db.async_session_factory``)
        used for all database access. Injectable for tests.
    cache:
        Optional :class:`PortraitCache`; a default instance is created per
        service. Pass a shared instance to coordinate multiple consumers.
    min_dimension:
        Smallest acceptable edge length for an animatable portrait.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        cache: PortraitCache | None = None,
        min_dimension: int = MIN_PORTRAIT_DIMENSION,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache if cache is not None else PortraitCache()
        self._min_dimension = min_dimension

    # -- public API ---------------------------------------------------------

    async def get_portrait(
        self,
        user_id: str,
        *,
        session_id: str | None = None,
        companion_id: str | None = None,
        refresh: bool = False,
    ) -> PreparedPortrait:
        """Resolve the selected companion's portrait and return prepared PNG bytes.

        ``session_id`` selects the companion currently attached to that
        chat session; ``companion_id`` (when given) takes precedence. With
        neither, the user's first companion is used, matching the visual
        subsystem's existing behavior.

        ``refresh=True`` bypasses the cache and re-fetches/re-prepares the
        image (use after a known portrait update).

        Raises :class:`PortraitNotFoundError` (nothing to animate for this
        user), :class:`PortraitAccessError` (ownership mismatch), or
        :class:`PortraitFormatError` (corrupt/unsupported/unsuitable).
        No other exception type is raised for expected failure modes.
        """
        async with self._session_factory() as db:
            companion, avatar_url = await self._resolve_companion(
                db, user_id, session_id=session_id, companion_id=companion_id
            )
            cache_key = f"{user_id}:{companion.companion_id}:{avatar_url}"
            if not refresh:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached
            image_row = await self._load_portrait_row(db, companion, user_id, avatar_url)
            try:
                prepared = self.prepare_portrait(
                    image_id=image_row.image_id,
                    companion_id=companion.companion_id,
                    mime_type=image_row.mime_type,
                    image_data=image_row.image_data or b"",
                    created_at=image_row.created_at,
                )
            except PortraitError:
                raise
            except Exception as exc:  # noqa: BLE001 — never crash the app on a bad portrait
                raise PortraitFormatError(
                    "Portrait preparation failed unexpectedly",
                    code="preparation_failed",
                ) from exc
        self._cache.put(cache_key, prepared)
        return prepared

    def invalidate(self, user_id: str, companion_id: str | None = None) -> None:
        """Drop cached portraits for a user (or one companion).

        Callers that know a portrait changed can call this instead of
        waiting for the TTL; ``get_portrait(refresh=True)`` is the other
        option. Safe to call at any time.
        """
        if companion_id is None:
            prefix = f"{user_id}:"
            for key in [k for k in self._cache.keys() if k.startswith(prefix)]:
                self._cache.invalidate(key)
        else:
            # Exact keys are not known without the avatar_url; clearing the
            # user's entries is cheap and always correct.
            self.invalidate(user_id)

    # -- resolution ----------------------------------------------------------

    async def _resolve_companion(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        session_id: str | None,
        companion_id: str | None,
    ) -> tuple[Companion, str]:
        """Return ``(companion, avatar_url)`` for the selected companion.

        Every path filters on the authenticated ``user_id``; a companion
        or session owned by someone else is indistinguishable from a
        missing one (no existence leak across users).
        """
        user_uuid = _user_uuid(user_id)

        if companion_id is not None:
            companion = await self._get_companion(db, companion_id, user_uuid)
            return companion, companion.avatar_url or ""

        if session_id is not None:
            try:
                session_uuid = uuid.UUID(str(session_id))
            except (ValueError, AttributeError, TypeError):
                raise PortraitNotFoundError(
                    "Session not found for this user", code="session_not_found"
                ) from None
            result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_uuid,
                    ChatSession.user_id == user_uuid,
                )
            )
            chat_session = result.scalar_one_or_none()
            if chat_session is None:
                raise PortraitNotFoundError(
                    "Session not found for this user",
                    code="session_not_found",
                )
            if chat_session.companion_id:
                companion = await self._get_companion(
                    db, chat_session.companion_id, user_uuid
                )
                return companion, companion.avatar_url or ""

        companion = await self._get_first_companion(db, user_uuid)
        return companion, companion.avatar_url or ""

    @staticmethod
    async def _get_companion(
        db: AsyncSession, companion_id: str, user_uuid: uuid.UUID
    ) -> Companion:
        result = await db.execute(
            select(Companion).where(
                Companion.companion_id == companion_id,
                Companion.user_id == user_uuid,
            )
        )
        companion = result.scalar_one_or_none()
        if companion is None:
            raise PortraitNotFoundError(
                "Companion not found for this user", code="companion_not_found"
            )
        return companion

    @staticmethod
    async def _get_first_companion(
        db: AsyncSession, user_uuid: uuid.UUID
    ) -> Companion:
        result = await db.execute(
            select(Companion)
            .where(Companion.user_id == user_uuid)
            .order_by(Companion.created_at)
            .limit(1)
        )
        companion = result.scalar_one_or_none()
        if companion is None:
            raise PortraitNotFoundError(
                "User has no companion", code="companion_not_found"
            )
        return companion

    # -- image retrieval ------------------------------------------------------

    async def _load_portrait_row(
        self,
        db: AsyncSession,
        companion: Companion,
        user_id: str,
        avatar_url: str,
    ) -> ImageMemory:
        if not avatar_url:
            raise PortraitNotFoundError(
                f"Companion '{companion.name}' has no portrait", code="no_portrait"
            )
        image_id = _parse_visual_image_id(avatar_url)
        user_uuid = _user_uuid(user_id)

        result = await db.execute(
            select(ImageMemory).where(
                ImageMemory.image_id == image_id,
                ImageMemory.user_id == user_uuid,
            )
        )
        image = result.scalar_one_or_none()
        if image is None:
            raise PortraitNotFoundError(
                "Portrait image not found", code="image_not_found"
            )
        # Defense in depth. The user filter in the query already prevents
        # cross-user retrieval (another user's image is 'not found', no
        # existence leak); the loaded row is re-verified so a future code
        # path that relaxes the query cannot leak another user's bytes.
        # The companion association is checked on the row itself: an image
        # owned by this user but attached to a different companion is an
        # association error, not a missing portrait.
        row_user = getattr(image, "user_id", None)
        if row_user is not None and row_user != user_uuid:
            raise PortraitAccessError("Portrait image is not owned by this user")
        row_companion = getattr(image, "companion_id", None)
        if row_companion is not None and row_companion != companion.companion_id:
            raise PortraitAccessError(
                "Portrait image is not associated with this companion"
            )
        if not image.image_data:
            raise PortraitNotFoundError(
                "Portrait image has no stored data", code="image_not_found"
            )
        return image

    # -- preparation ----------------------------------------------------------

    def prepare_portrait(
        self,
        *,
        image_id: str,
        companion_id: str,
        mime_type: str,
        image_data: bytes,
        created_at: datetime | None,
    ) -> PreparedPortrait:
        """Validate and normalize raw image bytes into AVTR-1-ready PNG.

        Pure function (no DB, no GPU, no face detection) so callers can
        reuse it directly. Raises :class:`PortraitFormatError` for corrupt,
        unsupported, or animation-unsuitable images; never lets an
        unexpected decoder failure escape as anything else.
        """
        mime = (mime_type or "").strip().lower()
        if mime.split("/", 1)[-1] not in ("png", "jpeg", "jpg", "webp"):
            raise PortraitFormatError(
                f"Unsupported portrait media type: {mime or '(none)'}"
            )

        try:
            with Image.open(BytesIO(image_data)) as img:
                actual_format = (img.format or "").upper()
                if actual_format not in _SUPPORTED_SOURCE_FORMATS:
                    raise PortraitFormatError(
                        f"Unsupported portrait image format: {actual_format or 'unknown'}"
                    )
                img.load()  # force full decode; truncation surfaces here
                width, height = img.size
                if (
                    width < self._min_dimension
                    or height < self._min_dimension
                ):
                    raise PortraitFormatError(
                        "Portrait is unsuitable for animation: image "
                        f"{width}x{height} is smaller than the minimum "
                        f"{self._min_dimension}px required for face detection"
                    )
                if img.mode == "RGBA":
                    normalized = img.copy()
                else:
                    normalized = img.convert("RGB")
        except PortraitFormatError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, EOFError) as exc:
            raise PortraitFormatError(
                "Portrait image data is corrupt or unreadable"
            ) from exc

        buffer = BytesIO()
        normalized.save(buffer, format="PNG")
        png_bytes = buffer.getvalue()

        return PreparedPortrait(
            image_id=image_id,
            companion_id=companion_id,
            source_mime_type=mime,
            width=width,
            height=height,
            png_bytes=png_bytes,
            source_created_at=created_at,
        )