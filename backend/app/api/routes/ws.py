import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_user_from_token
from app.db.session import get_session
from app.realtime.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/realtime")
async def realtime_stream(
    websocket: WebSocket,
    token: str | None = Query(None, description="JWT access token."),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Stream generated readings to an authenticated client.

    The token travels as a query parameter because browsers cannot set
    headers on a WebSocket handshake. Any authenticated active user may
    subscribe, regardless of role.
    """
    user = await get_user_from_token(token, session)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.info("Rejected unauthenticated WebSocket connection")
        return

    await manager.connect(websocket)
    logger.info("User id=%s subscribed to realtime data", user.id)
    try:
        while True:
            # The stream is server-to-client only; this waits for the client
            # to go away. Anything it sends is ignored.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
