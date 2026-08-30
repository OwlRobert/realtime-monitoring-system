from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import UserRole
from app.schemas.user import UserRead


class UserList(BaseModel):
    """All users, using the same safe representation as /auth/me."""

    items: list[UserRead]
    total: int


class UserRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class AuditLogRead(BaseModel):
    """One audit entry. Carries no credentials of any kind."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    action: str
    resource_type: str
    resource_id: int | None
    detail: str | None
    ip_address: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
    pages: int


class ConnectionPoolStatus(BaseModel):
    """SQLAlchemy pool counters, when the configured pool exposes them."""

    size: int | None = None
    checked_in: int | None = None
    checked_out: int | None = None
    overflow: int | None = None


class DatabaseStatus(BaseModel):
    """Operational snapshot for administrators. Never includes credentials."""

    healthy: bool
    dialect: str = Field(description="SQLAlchemy dialect, e.g. mysql.")
    driver: str = Field(description="DBAPI driver, e.g. asyncmy.")
    database: str = Field(description="Database name only — no host, user or password.")
    pool: ConnectionPoolStatus
    users: int
    data_records: int
    realtime_records: int
    audit_logs: int
    latest_realtime_timestamp: datetime | None
