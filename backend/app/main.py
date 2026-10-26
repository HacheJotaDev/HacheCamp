"""HJ Campaigns — API FastAPI.

Punto de entrada. Configura CORS, cabeceras de seguridad, migraciones al
arrancar (opcional) y monta la API v1.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging_conf import setup_logging

setup_logging()

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Las migraciones se ejecutan con Alembic (servicio "migrate" en Compose).
    yield


app = FastAPI(
    title="HJ Campaigns API",
    description="Plataforma profesional de email marketing con Amazon SES",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.ENV == "development" else None,
    redoc_url=None,
)

# CORS restringido: mismo origen por defecto (rewrites de Next.js)
if settings.allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Requested-With"],
    )

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/")
def root():
    return {"app": "HJ Campaigns API", "estado": "operativo"}
