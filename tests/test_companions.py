"""Tests for real companion management logic."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.companions import companion as companion_module  # noqa: E402
from chatbot.companions.companion import CompanionCreateRequest, CompanionManager  # noqa: E402


class _Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def fetchone(self) -> _Row:
        return _Row(self._mapping)


class _Connection:
    def __init__(self) -> None:
        self.sql: str | None = None
        self.params: tuple[Any, ...] | None = None

    async def exec_driver_sql(self, sql: str, params: tuple[Any, ...]) -> _Result:
        self.sql = sql
        self.params = params
        return _Result(
            {
                "companion_id": params[0],
                "user_id": params[1],
                "name": params[2],
                "persona": params[3],
                "description": params[4],
                "avatar_url": params[5],
                "backstory": params[6],
                "personality_sliders": None,
                "humor_style": params[8],
                "communication_style": params[9],
                "favorite_topics": None,
                "created_at": "2026-06-07T00:00:00+00:00",
                "updated_at": None,
            }
        )


class _Session:
    def __init__(self) -> None:
        self.connection_obj = _Connection()
        self.commits = 0

    async def connection(self) -> _Connection:
        return self.connection_obj

    async def commit(self) -> None:
        self.commits += 1


class _AvatarService:
    def __init__(self, image_url: str) -> None:
        self.image_url = image_url
        self.calls: list[dict[str, Any]] = []

    async def generate_and_persist_with_reference(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(image_url=self.image_url)


def test_create_generates_and_stores_avatar_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    service = _AvatarService("/visual/images/generated-avatar")

    monkeypatch.setattr(
        companion_module,
        "create_image_generation_service",
        lambda _appearance_manager: service,
    )

    companion = asyncio.run(
        CompanionManager(session).create(
            "00000000-0000-0000-0000-000000000001",
            CompanionCreateRequest(
                name="Nova",
                persona="curious guide",
                description="warm and expressive",
            ),
        )
    )

    assert companion.avatar_url == "/visual/images/generated-avatar"
    assert session.connection_obj.params is not None
    assert session.connection_obj.params[5] == "/visual/images/generated-avatar"
    assert service.calls[0]["scene_description"] == "Companion avatar portrait of Nova"
    assert service.calls[0]["db_session"] is session


def test_create_keeps_explicit_avatar_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()

    def fail_if_called(_appearance_manager: Any) -> None:
        raise AssertionError("avatar generation should not run")

    monkeypatch.setattr(
        companion_module,
        "create_image_generation_service",
        fail_if_called,
    )

    companion = asyncio.run(
        CompanionManager(session).create(
            "00000000-0000-0000-0000-000000000001",
            CompanionCreateRequest(
                name="Nova",
                avatar_url="https://example.test/avatar.png",
            ),
        )
    )

    assert companion.avatar_url == "https://example.test/avatar.png"
    assert session.connection_obj.params is not None
    assert session.connection_obj.params[5] == "https://example.test/avatar.png"
