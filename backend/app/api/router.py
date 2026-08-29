from fastapi import APIRouter

from app.api.routes import auth, health, records

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(records.router)
