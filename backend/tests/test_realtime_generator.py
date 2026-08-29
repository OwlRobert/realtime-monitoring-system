"""Deterministic tests for payload creation, the manager and the loop.

Nothing here sleeps for a real second: the generator is driven with a tiny
interval and cancelled explicitly.
"""

import asyncio
import random

import pytest

from app.realtime.generator import CATEGORIES, generate_reading, run_generator
from app.realtime.manager import ConnectionManager


class FixedRandom(random.Random):
    """Random source returning one predetermined value."""

    def __init__(self, value: float, category: str = "cpu") -> None:
        super().__init__()
        self._value = value
        self._category = category

    def uniform(self, a: float, b: float) -> float:
        return self._value

    def choice(self, seq):
        return self._category


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.fail = fail

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("client is gone")
        self.sent.append(payload)


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def test_reading_has_the_expected_shape():
    reading = generate_reading(threshold=80.0, rng=random.Random(1))
    payload = reading.model_dump(mode="json")

    assert set(payload) == {
        "title",
        "value",
        "category",
        "timestamp",
        "source",
        "is_anomaly",
    }
    assert payload["source"] == "REALTIME"
    assert payload["category"] in CATEGORIES
    assert 0.0 <= payload["value"] <= 100.0


def test_title_matches_category():
    reading = generate_reading(threshold=80.0, rng=FixedRandom(10.0, "memory"))

    assert reading.category == "memory"
    assert reading.title == "memory reading"


@pytest.mark.parametrize(
    ("value", "threshold", "expected"),
    [
        (95.0, 80.0, True),
        (80.01, 80.0, True),
        (80.0, 80.0, False),  # strictly greater than
        (12.5, 80.0, False),
        (30.0, 20.0, True),  # threshold is configurable
    ],
)
def test_anomaly_follows_threshold(value, threshold, expected):
    reading = generate_reading(threshold=threshold, rng=FixedRandom(value))

    assert reading.value == value
    assert reading.is_anomaly is expected


# --------------------------------------------------------------------------
# Connection manager
# --------------------------------------------------------------------------


async def test_manager_tracks_connections():
    manager = ConnectionManager()
    socket = FakeWebSocket()

    await manager.connect(socket)
    assert socket.accepted is True
    assert manager.connection_count == 1

    manager.disconnect(socket)
    assert manager.connection_count == 0


async def test_manager_broadcasts_to_every_client():
    manager = ConnectionManager()
    first, second = FakeWebSocket(), FakeWebSocket()
    await manager.connect(first)
    await manager.connect(second)

    delivered = await manager.broadcast({"value": 1})

    assert delivered == 2
    assert first.sent == second.sent == [{"value": 1}]


async def test_broken_client_is_dropped_without_affecting_others():
    manager = ConnectionManager()
    healthy, broken = FakeWebSocket(), FakeWebSocket(fail=True)
    await manager.connect(healthy)
    await manager.connect(broken)

    delivered = await manager.broadcast({"value": 1})

    assert delivered == 1
    assert manager.connection_count == 1
    assert healthy.sent == [{"value": 1}]


async def test_disconnect_is_idempotent():
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket)

    manager.disconnect(socket)
    manager.disconnect(socket)

    assert manager.connection_count == 0


async def test_broadcast_with_no_clients_is_harmless():
    assert await ConnectionManager().broadcast({"value": 1}) == 0


# --------------------------------------------------------------------------
# Generator loop
# --------------------------------------------------------------------------


async def test_generator_broadcasts_repeatedly_then_stops_on_cancel():
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket)

    task = asyncio.create_task(run_generator(manager))
    while len(socket.sent) < 3:
        await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(socket.sent) >= 3
    assert all(payload["source"] == "REALTIME" for payload in socket.sent)
    assert task.done()


async def test_generator_survives_a_failing_client():
    """A dead client must not stop the loop."""
    manager = ConnectionManager()
    broken, healthy = FakeWebSocket(fail=True), FakeWebSocket()
    await manager.connect(broken)
    await manager.connect(healthy)

    task = asyncio.create_task(run_generator(manager))
    while len(healthy.sent) < 2:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.connection_count == 1  # only the broken one was dropped
    assert len(healthy.sent) >= 2
