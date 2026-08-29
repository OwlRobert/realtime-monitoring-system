from fastapi import APIRouter

from app.api.routes import auth, health, records, ws

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(records.router)
api_router.include_router(ws.router)
