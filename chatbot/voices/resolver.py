"""Turn-start voice resolution for the avatar runtime (database-backed)."""

from __future__ import annotations

from chatbot.voices.service import VoiceService, VoiceUnavailable
from chatbot.voices.storage import VoiceStorage


class DatabaseVoiceResolver:
    """Resolves who speaks in a conversation, fresh from the database for every turn."""

    def __init__(self, session_factory, storage: VoiceStorage, *, design_model: str, clone_model: str):
        self._session_factory = session_factory
        self._storage = storage
        self._design_model = design_model
        self._clone_model = clone_model

    async def resolve(self, binding) -> dict:
        """The voice spec for `binding`'s conversation, or VoiceUnavailable (never a substitute)."""
        async with self._session_factory() as db:
            service = VoiceService(db, self._storage, design_model=self._design_model,
                                   clone_model=self._clone_model)
            resolved = await service.resolve(binding.user_id, binding.session_id)
            return {**resolved.spec, "source": resolved.source, "name": resolved.voice.name}


__all__ = ["DatabaseVoiceResolver", "VoiceUnavailable"]
