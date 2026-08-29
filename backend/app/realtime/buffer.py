import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_record import DataRecord, DataSource
from app.schemas.realtime import RealtimeReading

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


class RealtimePersistenceBuffer:
    """Holds generated readings until they are written to MariaDB in batches.

    Adding is a pure in-memory operation, so the generator never waits on the
    database. Flushing is driven by a separate task (see `run_flusher`).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        batch_size: int,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self.batch_size = batch_size
        self.interval_seconds = interval_seconds

        self._pending: list[RealtimeReading] = []
        self._lock = asyncio.Lock()
        # Set when the buffer reaches batch_size, so the flusher can act
        # immediately instead of waiting for the interval.
        self.batch_ready = asyncio.Event()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def add(self, reading: RealtimeReading) -> None:
        """Buffer one reading. Never touches the database."""
        async with self._lock:
            self._pending.append(reading)
            if len(self._pending) >= self.batch_size:
                self.batch_ready.set()

    async def flush(self) -> int:
        """Persist everything currently buffered; return the row count.

        The batch is removed from the buffer before the write and put back if
        the write fails, so nothing is lost and nothing is written twice.
        """
        async with self._lock:
            batch = self._pending
            self._pending = []
            self.batch_ready.clear()

        if not batch:
            return 0

        try:
            async with self._session_factory() as session:
                session.add_all([_to_record(reading) for reading in batch])
                await session.commit()
        except asyncio.CancelledError:
            # Shutdown landed mid-write. Put the batch back without awaiting
            # so the lifespan's final flush can retry it.
            self._pending[:0] = batch
            raise
        except Exception:  # noqa: BLE001 - keep the readings for the next attempt
            logger.exception("Failed to persist %d realtime readings", len(batch))
            async with self._lock:
                # Restore ahead of newer readings so ordering survives. The
                # ready flag is deliberately left clear: retrying is paced by
                # the flush interval instead of spinning on a failing write.
                self._pending[:0] = batch
                self.batch_ready.clear()
            return 0

        logger.info("Persisted %d realtime readings", len(batch))
        return len(batch)


def _to_record(reading: RealtimeReading) -> DataRecord:
    """Build an ORM row from a reading.

    This is a trusted internal path, so it bypasses DataRecordCreate, which
    deliberately refuses client-supplied REALTIME provenance.
    """
    return DataRecord(
        title=reading.title,
        value=reading.value,
        category=reading.category,
        timestamp=reading.timestamp,
        source=DataSource.REALTIME,
        is_anomaly=reading.is_anomaly,
        owner_id=None,
    )


async def run_flusher(buffer: RealtimePersistenceBuffer) -> None:
    """Flush the buffer on whichever trigger fires first.

    Waits for the size trigger, but never longer than the configured
    interval, so pending readings are written even when the batch is small.
    """
    logger.info(
        "Realtime flusher started (batch_size=%d interval=%ss)",
        buffer.batch_size,
        buffer.interval_seconds,
    )
    try:
        while True:
            try:
                await asyncio.wait_for(
                    buffer.batch_ready.wait(), timeout=buffer.interval_seconds
                )
            except (asyncio.TimeoutError, TimeoutError):
                pass  # interval elapsed; flush whatever is pending

            try:
                await buffer.flush()
            except Exception:  # noqa: BLE001 - the flusher must keep running
                logger.exception("Realtime flush cycle failed")
    except asyncio.CancelledError:
        logger.info("Realtime flusher stopped")
        raise
