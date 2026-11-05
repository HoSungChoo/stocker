"""FastAPI 앱. 프리픽스 /api, CORS는 로컬 프론트(3000, 5173)만."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from stocker.api.routes import ROUTERS

API_PREFIX = "/api"
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="stocker API",
        version="0.1.0",
        description=(
            "SOX 국면 유사도·설명·이벤트·포지션 읽기 전용 API (docs/PHASE5_CONTRACT.md 17절)"
        ),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    for r in ROUTERS:
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
