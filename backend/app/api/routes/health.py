import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    responses={503: {"model": HealthResponse, "description": "A dependency is unavailable."}},
)
async def health(
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Report service status, including a connectivity check against MariaDB."""
    try:
        await session.execute(select(literal(1)))
        database = "ok"
    except SQLAlchemyError:
        logger.exception("Database health check failed")
        database = "unavailable"

    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="degraded", database=database, version=settings.app_version
        )

    return HealthResponse(status="ok", database=database, version=settings.app_version)
