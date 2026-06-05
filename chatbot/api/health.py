from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/worker/health")
async def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}
