"""Agregación de routers de la API v1."""
from fastapi import APIRouter

from app.api.v1 import (
    auth,
    campaigns,
    contacts,
    lists,
    stats,
    suppression,
    system,
    templates,
    tracking,
    unsubscribe,
    users,
    webhooks,
)

api_router = APIRouter()

# Autenticación (login es público)
api_router.include_router(auth.router)

# Autenticados
api_router.include_router(users.router)
api_router.include_router(contacts.router)
api_router.include_router(lists.router)
api_router.include_router(suppression.router)
api_router.include_router(templates.router)
api_router.include_router(campaigns.router)
api_router.include_router(stats.router)
api_router.include_router(system.router)

# Públicos con protecciones propias
api_router.include_router(webhooks.router)
api_router.include_router(tracking.router)
api_router.include_router(unsubscribe.router)
