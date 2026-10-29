"""Configuración de pruebas: SQLite en memoria, fakeredis y SES simulado.

- El backend se prueba de extremo a extremo con TestClient de FastAPI.
- SES se simula con un monkeypatch (no se envían correos reales nunca).
- Los valores de entorno son DEFAULTS: exportando DATABASE_URL/REDIS_URL se
  puede ejecutar la MISMA suite contra PostgreSQL y Redis REALES (aislados),
  p. ej. con binarios embebidos (pgserver/redislite) en el sandbox.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Configura el entorno ANTES de importar la app (sin pisar lo ya definido)
os.environ.setdefault("ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///./.test_hj.db")
os.environ.setdefault("REDIS_URL", "fakeredis:///")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("COOKIE_SECURE", "false")  # TestClient usa http://
os.environ.setdefault("MFA_REQUIRED_FOR_ADMIN", "false")  # el flujo MFA se prueba aparte
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:3000")
os.environ.setdefault("API_BASE_URL", "http://localhost:8000")

_ES_SQLITE = os.environ["DATABASE_URL"].startswith("sqlite")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402  (el sys.path de arriba debe ejecutarse primero)
from fastapi.testclient import TestClient  # noqa: E402

# Limpia la BD de pruebas si existe (solo aplica al motor SQLite de fichero)
db_file = Path(".test_hj.db")
if _ES_SQLITE and db_file.exists():
    db_file.unlink()

from app.main import app  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if _ES_SQLITE and db_file.exists():
        db_file.unlink()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class FakeSES:
    """Simulador de SES API v2 para pruebas automatizadas."""

    def __init__(self):
        self.sent: list[dict] = []
        self.behavior = "ok"  # ok | throttling | rejected | error

    def reset(self):
        self.sent.clear()
        self.behavior = "ok"

    def __call__(self, *args, **kwargs):
        return self

    def send_email(self, **kwargs):
        import uuid as _uuid
        if self.behavior == "throttling":
            exc = Exception("ThrottlingException")
            exc.response = {"Error": {"Code": "ThrottlingException"}}
            raise exc
        if self.behavior == "rejected":
            exc = Exception("MessageRejected")
            exc.response = {"Error": {"Code": "MessageRejected"}}
            raise exc
        if self.behavior == "error":
            exc = Exception("InternalServerError")
            exc.response = {"Error": {"Code": "InternalServerError"}}
            raise exc
        if self.behavior == "quota":
            # Cuota diaria agotada (forma real de SES v2: MessageRejected con
            # mensaje 'Daily message quota exceeded')
            exc = Exception("Daily message quota exceeded")
            exc.response = {"Error": {"Code": "MessageRejected",
                                      "Message": "Daily message quota exceeded"}}
            raise exc
        self.sent.append(kwargs)
        return {"MessageId": f"ses-{_uuid.uuid4().hex[:16]}"}

    def get_account(self):
        # Forma real de la respuesta SESv2 GetAccount: cuotas anidadas en
        # SendQuota {Max24HourSend, SentLast24Hours, MaxSendRate}.
        return {
            "SendQuota": {
                "Max24HourSend": 50000.0,
                "SentLast24Hours": 0.0,
                "MaxSendRate": 14.0,
            },
            "SendingEnabled": True,
            "EnforcementStatus": "PROD",
            "DedicatedIpAutoWarmupEnabled": False,
        }

    def list_email_identities(self, **kwargs):
        return {"Identities": [
            {"IdentityName": "leisureparts4u.store", "IdentityType": "DOMAIN", "SendingEnabled": True},
            {"IdentityName": "notificaciones@leisureparts4u.store", "IdentityType": "EMAIL_ADDRESS", "SendingEnabled": True},
        ]}


@pytest.fixture(autouse=True)
def _clean_queue():
    """Vacía las colas de envíos, reintentos E importaciones tras cada prueba,
    además de las cachés SES (cuota, último dato válido y configured)."""
    yield
    from app.workers.redis_client import get_redis
    r = get_redis()
    r.delete("hj:q:outbound", "hj:q:retry", "hj:q:last_send_ns", "hj:q:import",
             "hj:q:bulk", "hj:ses:quota", "hj:ses:quota:last_good", "hj:ses:configured")


@pytest.fixture
def importar_csv(client, admin_auth):
    """Sube un CSV/TXT, procesa el trabajo (simula el worker importador) y
    devuelve el progreso final del trabajo.

    El flujo real es asíncrono (el API responde 202 y el worker dedicado
    procesa); en pruebas se invoca process_job directamente para no depender
    de un segundo proceso.
    """
    def _importar(contenido: bytes, filename: str = "contactos.csv",
                  media_type: str = "text/csv", procesar: bool = True, **form) -> dict:
        data = {"consent_source": "", "list_ids": "[]", "tag_ids": "[]"}
        data.update(form)
        r = client.post(
            "/api/v1/contacts/import",
            files={"archivo": (filename, contenido, media_type)},
            data=data,
        )
        assert r.status_code == 202, r.text
        subida = r.json()
        if procesar:
            from app.services import import_jobs as import_service
            db = SessionLocal()
            try:
                res = import_service.process_job(db, subida["job_id"])
                assert res["accion"] in ("completado", "cancelado"), res
            finally:
                db.close()
        prog = client.get(f"/api/v1/contacts/import/{subida['job_id']}")
        assert prog.status_code == 200, prog.text
        return prog.json()
    return _importar


@pytest.fixture
def fake_ses(monkeypatch):
    from app.services import ses as ses_service
    fake = FakeSES()
    monkeypatch.setattr(ses_service, "_client", lambda: fake)
    # Invalida la caché de cuotas (y el último dato válido) y la de credenciales
    from app.workers.redis_client import get_redis
    get_redis().delete("hj:ses:quota", "hj:ses:quota:last_good")
    ses_service._CHAIN_CACHE["resultado"] = (False, 0.0)
    return fake


@pytest.fixture
def admin_auth(client):
    """Crea un administrador con MFA deshabilitado (env de pruebas) y hace login."""
    import uuid
    from app.core import security
    from app.models import User

    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    db = SessionLocal()
    user = User(
        email=email, name="Admin de pruebas", role="admin",
        password_hash=security.hash_password("ClaveSegura123"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.id
    db.close()

    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveSegura123"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    csrf = data["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf})
    yield {"email": email, "csrf": csrf, "user_id": user_id, "user": data["user"]}
    client.headers.pop("X-CSRF-Token", None)


@pytest.fixture
def operador_auth(client):
    """Sesión de OPERADOR (no admin): para probar permisos de acciones V5."""
    import uuid
    from app.core import security
    from app.models import User

    email = f"operador-{uuid.uuid4().hex[:8]}@test.local"
    db = SessionLocal()
    user = User(
        email=email, name="Operador de pruebas", role="operador",
        password_hash=security.hash_password("ClaveSegura123"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()

    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveSegura123"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    csrf = data["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf})
    yield {"email": email, "csrf": csrf, "user": data["user"]}
    client.headers.pop("X-CSRF-Token", None)
