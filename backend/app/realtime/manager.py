import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """In-process registry of active WebSocket connections.

    Single FastAPI worker is assumed: connections live in this process only,
    so no broker is involved in fan-out.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("WebSocket connected (%d active)", self.connection_count)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        logger.info("WebSocket disconnected (%d active)", self.connection_count)

    async def broadcast(self, payload: dict[str, Any]) -> int:
        """Send `payload` to every client; drop the ones that fail.

        A failing client must never interrupt the generator, so send errors
        are swallowed and the connection is dropped instead.
        """
        delivered = 0
        for websocket in list(self._connections):
            try:
                await websocket.send_json(payload)
                delivered += 1
            except Exception:  # noqa: BLE001 - any failure means the client is gone
                logger.warning("Dropping unreachable WebSocket client", exc_info=True)
                self.disconnect(websocket)
        return delivered


# One manager per process.
manager = ConnectionManager()
