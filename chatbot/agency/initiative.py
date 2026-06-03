from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone


class InitiativeLevel(enum.Enum):
    NEVER = "never_proactive"
    REMINDERS = "reminders_only"
    GENTLE = "gentle_checkins"
    FREQUENT = "frequent"
    HIGH_ENERGY = "high_energy"


@dataclass
class InitiativeSettings:
    user_id: str
    level: InitiativeLevel = InitiativeLevel.GENTLE
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


_default_level: InitiativeLevel = InitiativeLevel.GENTLE


def set_default_level(level: InitiativeLevel) -> None:
    global _default_level
    _default_level = level


def get_default_level() -> InitiativeLevel:
    return _default_level


_settings: dict[str, InitiativeSettings] = {}

_HH_MM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _validate_hh_mm(value: str | None, label: str) -> None:
    if value is None:
        return
    if not _HH_MM_RE.match(value):
        raise ValueError(f"{label} must be in HH:MM format (00:00-23:59), got {value!r}")


def get_initiative(user_id: str) -> InitiativeSettings:
    return _settings.get(user_id, InitiativeSettings(user_id=user_id))


def set_initiative(user_id: str, settings: InitiativeSettings) -> None:
    _validate_hh_mm(settings.quiet_hours_start, "quiet_hours_start")
    _validate_hh_mm(settings.quiet_hours_end, "quiet_hours_end")
    _settings[user_id] = settings


def get_effective_level(user_id: str) -> InitiativeLevel:
    settings = _settings.get(user_id)
    if settings is not None:
        return settings.level
    return _default_level


def _parse_hh_mm(value: str) -> time:
    return time(int(value[:2]), int(value[3:]))


def is_in_quiet_hours(user_id: str) -> bool:
    settings = _settings.get(user_id)
    if settings is None:
        return False
    if settings.quiet_hours_start is None or settings.quiet_hours_end is None:
        return False
    now = datetime.now(timezone.utc).time()
    start = _parse_hh_mm(settings.quiet_hours_start)
    end = _parse_hh_mm(settings.quiet_hours_end)
    if start <= end:
        return start <= now < end
    else:
        return now >= start or now < end


def can_initiate(user_id: str) -> bool:
    if get_effective_level(user_id) == InitiativeLevel.NEVER:
        return False
    if is_in_quiet_hours(user_id):
        return False
    return True


class ProjectInitiative:
    def get_initiative_level(self, user_id: str) -> str:
        return get_effective_level(user_id).value

    def should_initiate(self, user_id: str) -> bool:
        return can_initiate(user_id)

    def is_quiet_hours(self, user_id: str) -> bool:
        return is_in_quiet_hours(user_id)

    async def get_initiative(self, user_id: str) -> InitiativeSettings:
        return get_initiative(user_id)

    async def set_initiative(self, user_id: str, settings: InitiativeSettings) -> None:
        set_initiative(user_id, settings)

    async def get_effective_level_async(self, user_id: str) -> InitiativeLevel:
        return get_effective_level(user_id)

    async def is_in_quiet_hours_async(self, user_id: str) -> bool:
        return is_in_quiet_hours(user_id)

    async def can_initiate_async(self, user_id: str) -> bool:
        return can_initiate(user_id)


__all__ = [
    "InitiativeLevel",
    "InitiativeSettings",
    "ProjectInitiative",
    "can_initiate",
    "get_default_level",
    "get_effective_level",
    "get_initiative",
    "is_in_quiet_hours",
    "set_default_level",
    "set_initiative",
]