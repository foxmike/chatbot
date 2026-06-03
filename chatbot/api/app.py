from contextlib import asynccontextmanager

from fastapi import FastAPI

from chatbot import __version__
from chatbot.api.config import config_router
from chatbot.api.health import router as health_router
from chatbot.api.chat import chat_router
from chatbot.api.memory import memory_router
from chatbot.api.sessions import sessions_router
from chatbot.api.agency import agency_router
from chatbot.api.streaming import streaming_router
from chatbot.config import get_settings
from chatbot.providers.bootstrap import build_default_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.provider_registry = build_default_registry(get_settings())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot API", version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(config_router, prefix="/api/config", tags=["config"])
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(streaming_router)
    app.include_router(memory_router)
    app.include_router(agency_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Chatbot API"}

    return app


app = create_app()
