from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI

from chatbot import __version__
from chatbot.api.config import config_router
from chatbot.api.health import router as health_router
from chatbot.api.chat import _background_queue, chat_router
from chatbot.api.memory import memory_router
from chatbot.api.sessions import sessions_router
from chatbot.api.agency import agency_router
from chatbot.api.admin import admin_router
from chatbot.api.auth import auth_router
from chatbot.api.rooms import rooms_router
from chatbot.api.streaming import streaming_router
from chatbot.api.continuity import narrative_router, rituals_router, timeline_router
from chatbot.api.companions import companion_router
from chatbot.api.voice import voice_router
from chatbot.config import get_settings
from chatbot.db import async_session_factory
from chatbot.perf.background import BackgroundTask
from chatbot.perf.handlers import consolidate_handler, memory_extract_handler
from chatbot.perf.worker import BackgroundWorker, start_worker
from chatbot.providers.bootstrap import build_default_registry

_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 10.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.provider_registry = build_default_registry(get_settings())
    worker: BackgroundWorker | None = None

    if _background_queue is not None:
        worker = await start_worker(
            _background_queue,
            handlers={
                "memory_extract": _memory_extract_handler,
                "consolidate": consolidate_handler,
            },
        )
        app.state.background_queue = _background_queue
        app.state.background_worker = worker

    try:
        yield
    finally:
        if worker is not None:
            await worker.shutdown(timeout_seconds=_WORKER_SHUTDOWN_TIMEOUT_SECONDS)


async def _memory_extract_handler(task: BackgroundTask) -> None:
    if task.payload.get("database") is None:
        task.payload["database"] = async_session_factory

    turn_context = task.payload.get("turn_context")
    if isinstance(turn_context, Mapping):
        enriched_context = dict(turn_context)
        if enriched_context.get("database") is None:
            enriched_context["database"] = async_session_factory
        task.payload["turn_context"] = enriched_context
    await memory_extract_handler(task)


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot API", version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(config_router, prefix="/config", tags=["config"])
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(streaming_router)
    app.include_router(memory_router)
    app.include_router(agency_router)
    app.include_router(rooms_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(companion_router)
    app.include_router(voice_router)
    app.include_router(timeline_router)
    app.include_router(narrative_router)
    app.include_router(rituals_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Chatbot API"}

    return app


app = create_app()
