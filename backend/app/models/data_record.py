from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    """Naive UTC timestamp, matching how timestamps are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DataSource(str, Enum):
    """How a record entered the system."""

    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    REALTIME = "REALTIME"


class DataRecord(Base):
    """Manual, imported and generated data, distinguished by `source`."""

    __tablename__ = "data_records"

    # Generated data arrives once per second, so the key is widened up front.
    # SQLite only auto-assigns INTEGER primary keys, hence the variant used
    # by the test suite; MariaDB still gets BIGINT.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, name="data_source", validate_strings=True),
        nullable=False,
        server_default=DataSource.MANUAL.value,
    )
    is_anomaly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    # Null for REALTIME rows, which have no human creator.
    owner_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_data_records_timestamp", "timestamp"),
        Index("ix_data_records_category_timestamp", "category", "timestamp"),
        Index("ix_data_records_source_timestamp", "source", "timestamp"),
    )

    def __repr__(self) -> str:
        return (
            f"<DataRecord id={self.id} category={self.category!r} "
            f"value={self.value} source={self.source}>"
        )
