"""Companion API — CRUD endpoints for per-user companions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.companions.companion import (
    CompanionCreateRequest,
    CompanionListResponse,
    CompanionManager,
    CompanionResponse,
)
from chatbot.db import get_db

companion_router = APIRouter(prefix="/companions", tags=["companions"])


@companion_router.post(
    "/",
    response_model=CompanionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_companion(
    body: CompanionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    return await manager.create(current_user.user_id, body)


@companion_router.get("/", response_model=CompanionListResponse)
async def list_companions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionListResponse:
    manager = CompanionManager(db)
    return await manager.list_by_user(current_user.user_id)


@companion_router.get("/{companion_id}", response_model=CompanionResponse)
async def get_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    companion = await manager.get(current_user.user_id, companion_id)
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
    return companion


@companion_router.put("/{companion_id}", response_model=CompanionResponse)
async def update_companion(
    companion_id: str,
    body: CompanionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanionResponse:
    manager = CompanionManager(db)
    companion = await manager.update(current_user.user_id, companion_id, body)
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
    return companion


@companion_router.delete("/{companion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    manager = CompanionManager(db)
    deleted = await manager.delete(current_user.user_id, companion_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
