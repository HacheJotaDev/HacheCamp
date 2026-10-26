"""Conexión a base de datos SQLAlchemy con tipos portables (PostgreSQL/SQLite).

- UUIDStorage: UUID como String(36) para compatibilidad con pruebas en SQLite.
- UTCDateTime: DateTime timezone-aware normalizado a UTC en ambos motores.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, String, TypeDecorator

from app.core.config import settings


def utcnow() -> datetime:
    """Hora actual consciente de zona, siempre en UTC."""
    return datetime.now(timezone.utc)


class GUID(TypeDecorator):
    """Identificador único portable: String(36)."""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if not isinstance(value, str):
                value = str(value)
            return value
        return None

    def process_result_value(self, value, dialect):
        return value


class UTCDateTime(TypeDecorator):
    """DateTime con normalización UTC portable entre PostgreSQL y SQLite."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


def _make_engine() -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if settings.DATABASE_URL.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
        )
    return create_engine(settings.DATABASE_URL, **kwargs)


engine = _make_engine()

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(Engine, "connect")
    def _sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """Dependencia FastAPI: sesión por petición."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Session:
    """Sesión para uso fuera de FastAPI (worker, scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
