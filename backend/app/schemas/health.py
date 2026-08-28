from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness and dependency status of the backend."""

    status: Literal["ok", "degraded"] = Field(
        description="Overall service status."
    )
    database: Literal["ok", "unavailable"] = Field(
        description="Result of a connectivity check against MariaDB."
    )
    version: str = Field(description="Application version.")
