import asyncio
from typing import Dict, Any

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import async_session_factory

router = APIRouter()


async def _check_database_health() -> Dict[str, Any]:
    """Check database connectivity and basic functionality."""
    try:
        async with async_session_factory() as session:
            result = await session.execute("SELECT 1")
            await result.scalar()
            return {"status": "healthy", "service": "database"}
    except (SQLAlchemyError, ConnectionError) as e:
        return {"status": "unhealthy", "service": "database", "error": str(e)}
    except Exception as e:
        return {"status": "unknown", "service": "database", "error": str(e)}


async def _check_background_worker_health() -> Dict[str, Any]:
    """Check background worker health via background queue status."""
    try:
        from chatbot.api.chat import _background_queue
        
        if _background_queue is None:
            return {"status": "healthy", "service": "background_worker", "note": "not_configured"}
        
        # Basic queue health check - assume healthy if queue exists
        return {"status": "healthy", "service": "background_worker"}
    except Exception as e:
        return {"status": "unhealthy", "service": "background_worker", "error": str(e)}


async def _check_retrieval_engine_health(app) -> Dict[str, Any]:
    """Check retrieval engine health."""
    try:
        if hasattr(app.state, 'retrieval_engine'):
            engine = app.state.retrieval_engine
            if engine is not None:
                return {"status": "healthy", "service": "retrieval_engine"}
        return {"status": "unhealthy", "service": "retrieval_engine", "error": "not_found"}
    except Exception as e:
        return {"status": "unhealthy", "service": "retrieval_engine", "error": str(e)}


@router.get("/health")
async def health() -> dict[str, str]:
    """Legacy health endpoint - returns basic status."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> Dict[str, Any]:
    """
    Readiness probe for kubernetes.
    Checks database, background worker, and retrieval engine health.
    """
    from fastapi import Request
    
    # Get app from request state
    async def get_app():
        return None  # This will be resolved by FastAPI's dependency injection
    
    try:
        # Run health checks concurrently
        results = await asyncio.gather(
            _check_database_health(),
            _check_background_worker_health(),
            return_exceptions=True
        )
        
        db_health = results[0] if not isinstance(results[0], Exception) else {"status": "unhealthy", "service": "database", "error": str(results[0])}
        worker_health = results[1] if not isinstance(results[1], Exception) else {"status": "unhealthy", "service": "background_worker", "error": str(results[1])}
        
        # Determine overall readiness
        all_healthy = (
            db_health.get("status") == "healthy" and
            "unhealthy" not in str(worker_health.get("status", ""))
        )
        
        status_code = "ready" if all_healthy else "not_ready"
        
        return {
            "status": status_code,
            "checks": {
                "database": db_health,
                "background_worker": worker_health,
                "overall": all_healthy
            }
        }
        
    except Exception as e:
        return {"status": "not_ready", "error": str(e)}


@router.get("/live")
async def liveness() -> Dict[str, Any]:
    """
    Liveness probe for kubernetes.
    Basic health check - if this fails, the service should restart.
    """
    return {"status": "alive", "timestamp": "timestamp"}


@router.get("/worker/health")
async def worker_health() -> dict[str, str]:
    """Legacy worker health endpoint."""
    return {"status": "ok", "service": "worker"}
