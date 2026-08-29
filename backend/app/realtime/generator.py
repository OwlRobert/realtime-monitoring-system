import asyncio
import logging
import random
from datetime import datetime, timezone

from app.core.config import get_settings
from app.realtime.manager import ConnectionManager, manager as default_manager
from app.schemas.realtime import RealtimeReading

logger = logging.getLogger(__name__)

CATEGORIES = ("cpu", "memory", "temperature")
MIN_VALUE = 0.0
MAX_VALUE = 100.0

_rng = random.Random()


def generate_reading(
    *, threshold: float, rng: random.Random | None = None
) -> RealtimeReading:
    """Produce one simulated reading, flagged against the anomaly threshold."""
    source = rng or _rng
    category = source.choice(CATEGORIES)
    value = round(source.uniform(MIN_VALUE, MAX_VALUE), 2)

    return RealtimeReading(
        title=f"{category} reading",
        value=value,
        category=category,
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        is_anomaly=value > threshold,
    )


async def run_generator(connections: ConnectionManager | None = None) -> None:
    """Emit one reading per interval and broadcast it immediately.

    Nothing is persisted here; batch persistence arrives in a later step.
    Runs until cancelled by the application lifespan.
    """
    settings = get_settings()
    connections = connections or default_manager

    logger.info(
        "Realtime generator started (interval=%ss threshold=%s)",
        settings.realtime_interval_seconds,
        settings.anomaly_threshold,
    )
    try:
        while True:
            try:
                reading = generate_reading(threshold=settings.anomaly_threshold)
                await connections.broadcast(reading.model_dump(mode="json"))
            except Exception:  # noqa: BLE001 - one bad tick must not stop the loop
                logger.exception("Realtime generator tick failed")

            await asyncio.sleep(settings.realtime_interval_seconds)
    except asyncio.CancelledError:
        logger.info("Realtime generator stopped")
        raise
