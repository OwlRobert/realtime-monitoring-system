from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    """Naive UTC timestamp, matching how timestamps are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditAction:
    """Stable action names, easy to filter on and read in the admin UI."""

    USER_REGISTER = "USER_REGISTER"
    USER_LOGIN = "USER_LOGIN"
    USER_ROLE_CHANGE = "USER_ROLE_CHANGE"
    USER_STATUS_CHANGE = "USER_STATUS_CHANGE"
    RECORD_CREATE = "RECORD_CREATE"
    RECORD_UPDATE = "RECORD_UPDATE"
    RECORD_DELETE = "RECORD_DELETE"
    RECORD_IMPORT = "RECORD_IMPORT"

    ALL = (
        USER_REGISTER,
        USER_LOGIN,
        USER_ROLE_CHANGE,
        USER_STATUS_CHANGE,
        RECORD_CREATE,
        RECORD_UPDATE,
        RECORD_DELETE,
        RECORD_IMPORT,
    )


class ResourceType:
    USER = "USER"
    RECORD = "RECORD"


class AuditLog(Base):
    """The queryable system log: who did what, to which resource, when.

    Deliberately holds no credentials of any kind — no passwords, hashes,
    tokens or headers ever reach `detail`.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # Null for system actions, and set to null if the actor is ever removed:
    # the history of what happened must outlive the account that did it.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
        Index("ix_audit_logs_user_created_at", "user_id", "created_at"),
        Index("ix_audit_logs_resource_created_at", "resource_type", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"user_id={self.user_id} resource={self.resource_type}:{self.resource_id}>"
        )
