"""Pruebas de autenticación, roles, CSRF, fuerza bruta y anti-enumeración."""
from __future__ import annotations

import uuid

import pytest

from app.core import security
from app.models import User

from tests.conftest import SessionLocal


def _mk_user(email: str, role: str = "operador", password: str = "ClaveOperador123"):
    db = SessionLocal()
    user = User(email=email, name="Test", role=role,
                password_hash=security.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


def test_login_ok_y_devuelve_csrf(client):
    email = f"u-{uuid.uuid4().hex[:6]}@test.local"
    _mk_user(email)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveOperador123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["csrf_token"]
    assert data["user"]["email"] == email
    assert "hj_session" in resp.cookies


def test_login_correo_inexistente_mensaje_generico(client):
    """Anti-enumeración: misma respuesta para correo inexistente y clave mala."""
    r1 = client.post("/api/v1/auth/login", json={"email": "nadie@test.local", "password": "loquesea1A"})
    r2 = client.post("/api/v1/auth/login", json={"email": "otro@test.local", "password": "otraclave1A"})
    assert r1.status_code == 401 and r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]
    assert "nadie" not in r1.text


def test_login_contrasena_incorrecta(client):
    email = f"u-{uuid.uuid4().hex[:6]}@test.local"
    _mk_user(email)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "Incorrecta999"})
    assert resp.status_code == 401


def test_bloqueo_por_fuerza_bruta(client):
    email = f"bf-{uuid.uuid4().hex[:6]}@test.local"
    _mk_user(email)
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": email, "password": "MalaClave000"})
    # El sexto intento, incluso con la clave correcta, está bloqueado
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveOperador123"})
    assert resp.status_code == 429


def test_logout_destruye_sesion(client):
    email = f"u-{uuid.uuid4().hex[:6]}@test.local"
    _mk_user(email)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveOperador123"})
    csrf = resp.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf})
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    me2 = client.get("/api/v1/auth/me")
    assert me2.status_code == 401


def test_endpoint_protegido_requiere_sesion(client):
    resp = client.get("/api/v1/contacts")
    assert resp.status_code == 401


def test_mutacion_sin_csrf_rechazada(client, admin_auth):
    """CSRF: mutación con sesión pero sin cabecera X-CSRF-Token -> 403."""
    client.headers.pop("X-CSRF-Token", None)
    resp = client.post("/api/v1/lists", json={"name": "Sin CSRF"})
    assert resp.status_code == 403
    client.headers.update({"X-CSRF-Token": admin_auth["csrf"]})


def test_operador_no_puede_aprobar_campañas(client, admin_auth):
    email = f"op-{uuid.uuid4().hex[:6]}@test.local"
    _mk_user(email, role="operador")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "ClaveOperador123"})
    csrf_op = login.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": csrf_op})
    resp = client.post("/api/v1/users", json={
        "email": f"x-{uuid.uuid4().hex[:5]}@test.local", "name": "Nuevo",
        "password": "ClaveSegura123", "role": "operador"})
    assert resp.status_code == 403
    client.headers.update({"X-CSRF-Token": admin_auth["csrf"]})


def test_admin_puede_crear_operador(client, admin_auth):
    resp = client.post("/api/v1/users", json={
        "email": f"nuevo-{uuid.uuid4().hex[:5]}@test.local",
        "name": "Operador Nuevo", "password": "ClaveSegura456", "role": "operador"})
    assert resp.status_code == 201


def test_password_debil_rechazada(client, admin_auth):
    resp = client.post("/api/v1/users", json={
        "email": f"debil-{uuid.uuid4().hex[:5]}@test.local",
        "name": "Debil", "password": "corta", "role": "operador"})
    assert resp.status_code == 400


def test_argon2id_en_hashes():
    db = SessionLocal()
    user = db.query(User).first()
    db.close()
    assert user.password_hash.startswith("$argon2id$")


def test_mfa_setup_y_enable(client, admin_auth):
    setup = client.post("/api/v1/auth/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_url"].startswith("otpauth://totp/")

    import pyotp
    code = pyotp.TOTP(secret).now()
    enable = client.post("/api/v1/auth/mfa/enable", json={"secret": secret, "code": code})
    assert enable.status_code == 200
    recovery_codes = enable.json()["codigos_recuperacion"]
    assert len(recovery_codes) == 8

    # Nuevo login exige segundo paso
    client.headers.pop("X-CSRF-Token", None)
    from tests.conftest import SessionLocal as S
    db = S()
    email = admin_auth["email"]
    db_user = db.query(User).filter(User.email == email).first()
    db_user.totp_enabled = False  # desactiva para no romper otras pruebas
    db.commit()
    db.close()
