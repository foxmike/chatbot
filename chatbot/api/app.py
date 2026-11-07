import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from chatbot import __version__
from chatbot.agency.notifications import Notification, NotificationQueue
from chatbot.agency.scheduler import ProactiveAction, Scheduler
from chatbot.api.config import config_router
from chatbot.api.health import router as health_router
from chatbot.api.chat import _background_queue, chat_router
from chatbot.api.memory import memory_router
from chatbot.api.models import models_router
from chatbot.api.voices import voices_router
from chatbot.api.sessions import sessions_router
from chatbot.api.agency import agency_router
from chatbot.api.admin import admin_router
from chatbot.api.auth import auth_router
from chatbot.api.rooms import rooms_router
from chatbot.api.streaming import streaming_router
from chatbot.api.continuity import narrative_router, rituals_router, timeline_router
from chatbot.api.companions import companion_router
from chatbot.api.visual import visual_router
from chatbot.avatar.coordinator import get_coordinator
from chatbot.avatar.router import avatar_router
from chatbot.avatar.runtime import AvatarRuntimeHost
from chatbot.config import get_settings
from chatbot.db import async_session_factory
from chatbot.perf.background import BackgroundTask
from chatbot.perf.handlers import consolidate_handler, memory_extract_handler
from chatbot.perf.worker import BackgroundWorker, start_worker
from chatbot.memory.retrieval import build_retrieval_engine
from chatbot.providers.bootstrap import build_default_registry

_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_SCHEDULER_POLLING_INTERVAL_SECONDS = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.provider_registry = build_default_registry(settings)
    app.state.default_model = settings.default_model
    app.state.default_provider = settings.default_provider
    app.state.retrieval_engine = build_retrieval_engine(
        async_session_factory,
        settings,
    )
    worker: BackgroundWorker | None = None
    scheduler_task: asyncio.Task[None] | None = None

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

    scheduler_task = await _start_scheduler(app)

    try:
        yield
    finally:
        await get_coordinator(app).close()
        host = getattr(app.state, "avatar_runtime_host", None)
        if host is not None:
            await host.aclose()
        if scheduler_task is not None:
            await _stop_scheduler(scheduler_task, app.state.scheduler)
        if hasattr(app.state, "_scheduler_db_session"):
            await app.state._scheduler_db_session.close()
        if worker is not None:
            await worker.shutdown(timeout_seconds=_WORKER_SHUTDOWN_TIMEOUT_SECONDS)


async def _start_scheduler(app: FastAPI) -> asyncio.Task[None]:
    """Create a DB-backed scheduler and start its runtime loop."""
    db_session = async_session_factory()
    app.state._scheduler_db_session = db_session

    scheduler = Scheduler(
        db_session=db_session,
        polling_interval_seconds=_SCHEDULER_POLLING_INTERVAL_SECONDS,
    )
    app.state.scheduler = scheduler

    async def _on_action(action: ProactiveAction) -> None:
        notification_queue = NotificationQueue(db_session)
        notification = Notification(
            notification_id=action.action_id,
            user_id=action.user_id,
            notif_type=action.action_type,
            title=action.message,
            body=action.message,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        initiative_level = scheduler.get_initiative_level(action.user_id)
        await notification_queue.enqueue_async(
            action.user_id,
            notification,
            initiative_level,
        )

    return asyncio.create_task(scheduler.run_loop(action_handler=_on_action))


async def _stop_scheduler(task: asyncio.Task[None], scheduler: Scheduler) -> None:
    scheduler.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
    app = FastAPI(title="Chatbot API", version=__version__, lifespan=lifespan, redirect_slashes=False)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(config_router, prefix="/config", tags=["config"])
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(streaming_router)
    app.include_router(models_router)
    app.include_router(voices_router)
    app.include_router(memory_router)
    app.include_router(agency_router)
    app.include_router(rooms_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(companion_router)
    app.include_router(visual_router)
    app.include_router(avatar_router)
    app.state.avatar_runtime_host = AvatarRuntimeHost()
    app.state.avatar_runtime_factory = app.state.avatar_runtime_host.factory
    app.include_router(timeline_router)
    app.include_router(narrative_router)
    app.include_router(rituals_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Chatbot API"}

    return app


app = create_app()
