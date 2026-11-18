from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.config import settings
from app.database.db import init_database
from api.qr import router as qr_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    yield


app = FastAPI(
    title="Zyx Media API",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(qr_router)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "zyx-media-api",
        "environment": settings.environment,
    }
