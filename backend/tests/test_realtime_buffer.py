"""Buffer and batch-persistence tests.

Timing-sensitive cases use millisecond intervals, never real seconds.
"""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.data_record import DataRecord, DataSource
from app.realtime.buffer import RealtimePersistenceBuffer, run_flusher
from app.realtime.generator import run_generator
from app.realtime.manager import ConnectionManager
from app.schemas.realtime import RealtimeReading
from tests.test_realtime_generator import FakeWebSocket

TIMESTAMP = datetime(2026, 8, 29, 12, 0, 0)


def reading(value: float = 10.0, *, is_anomaly: bool = False) -> RealtimeReading:
    return RealtimeReading(
        title="cpu reading",
        value=value,
        category="cpu",
        timestamp=TIMESTAMP,
        is_anomaly=is_anomaly,
    )


def make_buffer(session_factory, *, batch_size=10, interval=3600.0):
    return RealtimePersistenceBuffer(
        session_factory, batch_size=batch_size, interval_seconds=interval
    )


class BrokenSessionFactory:
    """Stands in for an unavailable database."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise RuntimeError("database is unavailable")


async def stored_records(db_session) -> list[DataRecord]:
    return list((await db_session.execute(select(DataRecord))).scalars().all())


async def wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until `predicate()` is true, instead of sleeping a fixed period."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def wait_for_persisted(session_factory, count: int, timeout: float = 2.0) -> None:
    """Wait until `count` rows are committed.

    Row count is the only unambiguous signal: `pending_count` briefly reads
    zero while a flush is in flight, before the write has committed.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with session_factory() as session:
            stored = len((await session.execute(select(DataRecord))).scalars().all())
        if stored >= count:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"only {stored} of {count} rows persisted in time")
        await asyncio.sleep(0.005)


# --------------------------------------------------------------------------
# Buffering
# --------------------------------------------------------------------------


async def test_reading_enters_the_buffer(session_factory):
    buffer = make_buffer(session_factory)
    await buffer.add(reading())

    assert buffer.pending_count == 1
    assert buffer.batch_ready.is_set() is False


async def test_size_threshold_marks_the_batch_ready(session_factory):
    buffer = make_buffer(session_factory, batch_size=3)

    for _ in range(2):
        await buffer.add(reading())
    assert buffer.batch_ready.is_set() is False

    await buffer.add(reading())
    assert buffer.batch_ready.is_set() is True


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


async def test_flush_persists_a_batch_in_one_go(session_factory, db_session):
    buffer = make_buffer(session_factory)
    for value in (1.0, 2.0, 3.0):
        await buffer.add(reading(value))

    written = await buffer.flush()

    assert written == 3
    records = await stored_records(db_session)
    assert sorted(record.value for record in records) == [1.0, 2.0, 3.0]


async def test_persisted_rows_carry_realtime_provenance(session_factory, db_session):
    buffer = make_buffer(session_factory)
    await buffer.add(reading(95.0, is_anomaly=True))
    await buffer.add(reading(12.0, is_anomaly=False))

    await buffer.flush()

    records = sorted(await stored_records(db_session), key=lambda r: r.value)
    assert all(record.source is DataSource.REALTIME for record in records)
    assert all(record.owner_id is None for record in records)
    assert [record.is_anomaly for record in records] == [False, True]
    assert [record.title for record in records] == ["cpu reading", "cpu reading"]
    assert all(record.timestamp == TIMESTAMP for record in records)


async def test_successful_flush_empties_the_buffer(session_factory):
    buffer = make_buffer(session_factory)
    await buffer.add(reading())

    await buffer.flush()

    assert buffer.pending_count == 0
    assert buffer.batch_ready.is_set() is False


async def test_flushing_an_empty_buffer_is_a_no_op(session_factory, db_session):
    assert await make_buffer(session_factory).flush() == 0
    assert await stored_records(db_session) == []


async def test_readings_are_not_persisted_twice(session_factory, db_session):
    buffer = make_buffer(session_factory)
    await buffer.add(reading(7.0))

    assert await buffer.flush() == 1
    assert await buffer.flush() == 0

    assert len(await stored_records(db_session)) == 1


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


async def test_failed_flush_keeps_the_readings(session_factory, db_session):
    broken = BrokenSessionFactory()
    buffer = make_buffer(broken)
    await buffer.add(reading(1.0))
    await buffer.add(reading(2.0))

    written = await buffer.flush()

    assert written == 0
    assert broken.calls == 1
    assert buffer.pending_count == 2  # nothing was lost
    assert await stored_records(db_session) == []


async def test_readings_survive_until_the_database_recovers(session_factory, db_session):
    """A failed batch is retried on the next flush."""
    broken = BrokenSessionFactory()
    buffer = make_buffer(broken)
    await buffer.add(reading(1.0))
    await buffer.flush()

    # Database comes back.
    buffer._session_factory = session_factory
    await buffer.add(reading(2.0))
    written = await buffer.flush()

    assert written == 2
    assert buffer.pending_count == 0
    assert sorted(r.value for r in await stored_records(db_session)) == [1.0, 2.0]


async def test_flusher_survives_persistence_failures(session_factory):
    buffer = make_buffer(BrokenSessionFactory(), batch_size=1, interval=0.01)
    task = asyncio.create_task(run_flusher(buffer))

    await buffer.add(reading())
    await asyncio.sleep(0.05)  # several failing cycles

    assert task.done() is False
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------
# Flush triggers driven by the flusher task
# --------------------------------------------------------------------------


async def test_size_trigger_flushes_before_the_interval(session_factory, db_session):
    """Ten-second interval, so only the size trigger can fire."""
    buffer = make_buffer(session_factory, batch_size=3, interval=10.0)
    task = asyncio.create_task(run_flusher(buffer))

    for value in (1.0, 2.0, 3.0):
        await buffer.add(reading(value))

    await wait_for_persisted(session_factory, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(await stored_records(db_session)) == 3
    assert buffer.pending_count == 0


async def test_time_trigger_flushes_an_incomplete_batch(session_factory, db_session):
    """One reading, batch size ten: only the interval can flush this."""
    buffer = make_buffer(session_factory, batch_size=10, interval=0.02)
    task = asyncio.create_task(run_flusher(buffer))

    await buffer.add(reading(42.0))

    await wait_for_persisted(session_factory, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    records = await stored_records(db_session)
    assert len(records) == 1
    assert records[0].value == 42.0
    assert buffer.pending_count == 0


# --------------------------------------------------------------------------
# Generator interaction
# --------------------------------------------------------------------------


async def test_generator_buffers_what_it_broadcasts(session_factory):
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket)
    buffer = make_buffer(session_factory)

    task = asyncio.create_task(run_generator(manager, buffer))
    await wait_for(lambda: len(socket.sent) >= 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert buffer.pending_count >= 3


async def test_broadcasting_continues_when_persistence_fails(session_factory):
    """Delivery must not depend on the database being available."""
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket)
    buffer = make_buffer(BrokenSessionFactory(), batch_size=1, interval=0.01)

    generator = asyncio.create_task(run_generator(manager, buffer))
    flusher = asyncio.create_task(run_flusher(buffer))

    await wait_for(lambda: len(socket.sent) >= 5)

    assert generator.done() is False
    assert flusher.done() is False
    assert all(payload["source"] == "REALTIME" for payload in socket.sent)

    for task in (generator, flusher):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_shutdown_flush_persists_what_is_left(session_factory, db_session):
    """Mirrors the lifespan: cancel the tasks, then flush what remains."""
    manager = ConnectionManager()
    buffer = make_buffer(session_factory, batch_size=1000, interval=3600.0)

    generator = asyncio.create_task(run_generator(manager, buffer))
    flusher = asyncio.create_task(run_flusher(buffer))
    await wait_for(lambda: buffer.pending_count >= 2)

    for task in (generator, flusher):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    pending = buffer.pending_count
    written = await buffer.flush()

    assert written == pending >= 2
    assert buffer.pending_count == 0
    assert len(await stored_records(db_session)) == written
