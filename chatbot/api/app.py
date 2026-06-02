from fastapi import FastAPI

from chatbot import __version__
from chatbot.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="Chatbot API", version=__version__)
    app.include_router(health_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Chatbot API"}

    return app


app = create_app()
