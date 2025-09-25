# Create a new file: app/controllers/health_controller.py
from fastapi import APIRouter, status, Response, Depends
from app.services.auth_service import AuthService
from app.config.database.session import get_db
from app.config.redis_config import cache
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Dict, Any
from datetime import datetime

router = APIRouter(prefix="/api", tags=["Health"])

auth_service = AuthService()


# @router.get("/health")
# async def health_check(response: Response) -> Dict[str, Any]:
#     """
#     Comprehensive health check endpoint
#     """
#     health_status: Dict[str, Any] = {
#         "status": "healthy",
#         "timestamp": datetime.now().isoformat(),
#         "services": {},
#     }

#     # Check Keycloak
#     keycloak_healthy = auth_service.health_check()
#     health_status["services"]["keycloak"] = {
#         "status": "healthy" if keycloak_healthy else "unhealthy",
#         "url": auth_service.settings.keycloak_server_url,
#     }

#     # Overall status
#     if not keycloak_healthy:
#         health_status["status"] = "degraded"

#     # Set appropriate status code
#     if health_status["status"] != "healthy":
#         response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

#     return health_status


@router.get("/health")
async def health_check(response: Response) -> Dict[str, str]:
    """
    Simple health check endpoint that verifies Keycloak connectivity
    """
    # Check Keycloak
    keycloak_healthy = auth_service.health_check()

    if keycloak_healthy:
        return {"status": "healthy"}
    else:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unhealthy"}


@router.get("/health/ready")
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness check - determines if the application is ready to serve traffic
    """
    keycloak_ready = auth_service.health_check()

    return {
        "status": "ready" if keycloak_ready else "not ready",
        # "services": {"keycloak": keycloak_ready},
    }


@router.get("/health/live")
async def liveness_check() -> Dict[str, str]:
    """
    Liveness check - determines if the application is alive
    """
    return {"status": "alive", "timestamp": datetime.now().isoformat()}


@router.get("/health/database")
async def database_health_check(
    response: Response, db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Database-specific health check endpoint
    """
    try:
        await db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": {"status": "healthy"},
        }
    except Exception as e:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "database": {"status": "unhealthy", "error": str(e)},
        }


@router.get("/health/cache")
async def cache_health_check(response: Response) -> Dict[str, Any]:
    """
    Cache-specific health check endpoint
    """
    try:
        cache_healthy = await cache.health_check()
        if cache_healthy:
            return {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "cache": {"status": "healthy"},
            }
        else:
            raise Exception("Cache health check failed")
    except Exception as e:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "cache": {"status": "unhealthy", "error": str(e)},
        }
