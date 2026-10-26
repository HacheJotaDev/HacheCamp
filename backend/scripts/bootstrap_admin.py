"""Bootstrap seguro del administrador inicial y plantillas del sistema.

Uso (dentro del contenedor o con el venv activado):
    python scripts/bootstrap_admin.py --email admin@dominio.com --name "Administrador"

- La contraseña NO se pasa por argumento (aparecería en el historial del shell):
  se lee de la variable HJ_BOOTSTRAP_PASSWORD o se genera aleatoriamente y se
  muestra UNA sola vez.
- Si ya existen administradores, el script se niega salvo --force.
- Siembra las plantillas del sistema si no existen.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

os.environ.setdefault("ENV", "development")  # el valor real lo da el .env del contenedor

from app.core import security  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.models import Template, User  # noqa: E402


SYSTEM_TEMPLATES = [
    {
        "name": "Bienvenida",
        "category": "bienvenida",
        "description": "Email de bienvenida para nuevos suscriptores",
        "blocks": [
            {"type": "encabezado", "props": {"text": "¡Bienvenido a bordo!", "style": "font-size:22px;font-weight:bold;color:#111827;"}},
            {"type": "texto", "props": {"content": "<p style='font-size:16px;'>Hola <b>{{contact.first_name}}</b>,</p><p style='font-size:15px;color:#4b5563;'>Gracias por suscribirte. A partir de ahora recibirás nuestras novedades, guías y ofertas seleccionadas para ti.</p>"}},
            {"type": "separador", "props": {}},
            {"type": "boton", "props": {"text": "Explorar ahora", "href": "https://leisureparts4u.store", "bg_color": "#10b981", "text_color": "#ffffff"}},
            {"type": "espaciador", "props": {"height": 16}},
            {"type": "pie", "props": {"text": "Recibes este correo porque te suscribiste en nuestro sitio.", "company": "HJ Campaigns"}},
        ],
    },
    {
        "name": "Newsletter",
        "category": "newsletter",
        "description": "Boletín mensual con noticias y contenidos",
        "blocks": [
            {"type": "encabezado", "props": {"text": "Nuestro boletín", "style": "font-size:20px;font-weight:bold;color:#111827;"}},
            {"type": "texto", "props": {"content": "<p style='font-size:15px;'>Hola <b>{{contact.first_name}}</b>, esto es lo que hemos preparado este mes:</p><ul><li>Noticia destacada uno</li><li>Contenido destacado dos</li><li>Recurso útil tres</li></ul>"}},
            {"type": "imagen", "props": {"src": "https://placehold.co/600x280/10b981/ffffff?text=Newsletter", "alt": "Imagen destacada"}},
            {"type": "titulo", "props": {"text": "Artículo principal", "level": 2}},
            {"type": "texto", "props": {"content": "<p style='font-size:15px;color:#4b5563;'>Escribe aquí el resumen del artículo principal.</p>"}},
            {"type": "pie", "props": {"text": "Publicación periódica de nuestro equipo.", "company": "HJ Campaigns"}},
        ],
    },
    {
        "name": "Promoción",
        "category": "promociones",
        "description": "Plantilla de campaña promocional con oferta destacada",
        "blocks": [
            {"type": "encabezado", "props": {"text": "OFERTA ESPECIAL", "style": "font-size:24px;font-weight:bold;color:#dc2626;"}},
            {"type": "titulo", "props": {"text": "Hasta 40% de descuento", "level": 1}},
            {"type": "texto", "props": {"content": "<p style='font-size:16px;'>Hola <b>{{contact.first_name}}</b>, por tiempo limitado aprovecha esta oferta exclusiva para suscriptores.</p>"}},
            {"type": "imagen", "props": {"src": "https://placehold.co/600x300/f59e0b/ffffff?text=Promocion", "alt": "Banner de promoción"}},
            {"type": "boton", "props": {"text": "Ver oferta", "href": "https://leisureparts4u.store", "bg_color": "#dc2626"}},
            {"type": "texto", "props": {"content": "<p style='font-size:12px;color:#6b7280;'>*Términos y condiciones aplican. Oferta válida hasta agotar existencias.</p>"}},
            {"type": "pie", "props": {"text": "Si prefieres no recibir promociones, puedes darte de baja con un clic.", "company": "HJ Campaigns"}},
        ],
    },
    {
        "name": "Aviso de servicio",
        "category": "avisos",
        "description": "Comunicación importante sobre el servicio (transaccional)",
        "blocks": [
            {"type": "encabezado", "props": {"text": "Aviso de servicio", "style": "font-size:20px;font-weight:bold;color:#111827;"}},
            {"type": "texto", "props": {"content": "<p style='font-size:15px;'>Hola <b>{{contact.first_name}}</b>,</p><p style='font-size:15px;color:#4b5563;'>Te informamos sobre una actualización importante de nuestro servicio.</p>"}},
            {"type": "separador", "props": {}},
            {"type": "texto", "props": {"content": "<p style='font-size:14px;'>Detalles del cambio: <i>describe aquí el cambio</i>.</p>"}},
            {"type": "pie", "props": {"text": "Aviso transaccional relativo a tu cuenta.", "company": "HJ Campaigns"}},
        ],
    },
    {
        "name": "Confirmación de pedido",
        "category": "confirmaciones",
        "description": "Confirmación de pedido o acción (transaccional)",
        "blocks": [
            {"type": "encabezado", "props": {"text": "Confirmación recibida", "style": "font-size:20px;font-weight:bold;color:#059669;"}},
            {"type": "texto", "props": {"content": "<p style='font-size:15px;'>Hola <b>{{contact.first_name}}</b>, hemos recibido tu solicitud correctamente.</p>"}},
            {"type": "separador", "props": {"color": "#d1fae5"}},
            {"type": "texto", "props": {"content": "<table style='width:100%;font-size:14px;'><tr><td><b>Referencia</b></td><td>#000000</td></tr><tr><td><b>Fecha</b></td><td>—</td></tr></table>"}},
            {"type": "texto", "props": {"content": "<p style='font-size:14px;color:#4b5563;'>Si no reconoces esta operación, contacta con nuestro soporte.</p>"}},
            {"type": "pie", "props": {"text": "Correo transaccional de tu cuenta.", "company": "HJ Campaigns"}},
        ],
    },
]


def seed_templates(db) -> int:
    created = 0
    for spec in SYSTEM_TEMPLATES:
        exists = db.query(Template).filter(Template.name == spec["name"]).first()
        if exists:
            continue
        template = Template(
            name=spec["name"], description=spec["description"], category=spec["category"],
            mode="bloques", blocks=spec["blocks"], is_system=True,
        )
        db.add(template)
        created += 1
    db.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap del administrador inicial")
    parser.add_argument("--email", required=True, help="Correo del administrador")
    parser.add_argument("--name", default="Administrador", help="Nombre visible")
    parser.add_argument("--force", action="store_true",
                        help="Permite crear el usuario aunque ya existan administradores")
    args = parser.parse_args()

    setup_logging()
    email = args.email.strip().lower().rstrip(".")
    if "@" not in email or "." not in email.split("@")[-1]:
        print("ERROR: correo inválido")
        return 2

    # Contraseña: variable de entorno o generada aleatoriamente
    password = os.environ.get("HJ_BOOTSTRAP_PASSWORD", "")
    generated = False
    if not password:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!#$%"
        password = "".join(secrets.choice(alphabet) for _ in range(20))
        generated = True

    issues = security.password_issues(password)
    if issues:
        print("ERROR: la contraseña no cumple la política:", "; ".join(issues))
        return 2

    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").count()
        if admins > 0 and not args.force:
            print(f"Ya existen {admins} administrador(es). Use --force para añadir otro.")
            return 1
        if db.query(User).filter(User.email == email).first():
            print("ERROR: ya existe un usuario con ese correo")
            return 1

        user = User(
            email=email, name=args.name, role="admin",
            password_hash=security.hash_password(password),
        )
        db.add(user)
        db.commit()

        seeded = seed_templates(db)

        print("=" * 60)
        print("Administrador creado correctamente")
        print(f"  Correo: {email}")
        if generated:
            print(f"  Contraseña temporal: {password}")
            print("  (guárdela AHORA; no volverá a mostrarse)")
        print(f"  Plantillas del sistema creadas: {seeded}")
        print("Próximo paso: inicie sesión y configure MFA TOTP (obligatorio).")
        print("=" * 60)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
