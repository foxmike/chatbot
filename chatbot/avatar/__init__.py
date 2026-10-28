"""Avatar subsystem — character portrait preparation for AVTR-1 animation.

The portrait service is the only avatar-subsystem component in this package.
It resolves the selected companion's portrait from chatbot's database,
validates ownership, normalizes the image to the PNG format AVTR-1 expects,
and caches the prepared result. It deliberately knows nothing about LLM
turns, Qwen inference, browser audio, or the media runtime — consumers
(the media runtime / WebSocket coordinator) call `PortraitService`.
"""

from chatbot.avatar.portraits import (
    PreparedPortrait,
    PortraitAccessError,
    PortraitCache,
    PortraitError,
    PortraitFormatError,
    PortraitNotFoundError,
    PortraitService,
)

__all__ = [
    "PreparedPortrait",
    "PortraitAccessError",
    "PortraitCache",
    "PortraitError",
    "PortraitFormatError",
    "PortraitNotFoundError",
    "PortraitService",
]