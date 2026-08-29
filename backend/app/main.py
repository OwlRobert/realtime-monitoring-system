import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal, engine
from app.realtime.buffer import RealtimePersistenceBuffer, run_flusher
from app.realtime.generator import run_generator

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)

    # One generator and one flusher per process; the app runs with a single
    # worker, so these are the only instances that exist.
    buffer = RealtimePersistenceBuffer(
        SessionLocal,
        batch_size=settings.batch_size,
        interval_seconds=settings.batch_interval_seconds,
    )
    app.state.realtime_buffer = buffer
    app.state.generator_task = asyncio.create_task(run_generator(buffer=buffer))
    app.state.flusher_task = asyncio.create_task(run_flusher(buffer))

    yield

    for task in (app.state.generator_task, app.state.flusher_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Final flush before the engine goes away, so buffered readings are not
    # lost on a graceful shutdown.
    if buffer.pending_count:
        logger.info("Flushing %d buffered readings before shutdown", buffer.pending_count)
        try:
            await buffer.flush()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.exception("Final realtime flush failed")

    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.include_router(api_router)
