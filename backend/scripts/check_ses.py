"""Verificación de SES desde el VPS sin revelar credenciales.

Uso dentro del contenedor del api:
    docker compose exec api python scripts/check_ses.py                # cuotas + identidades
    docker compose exec api python scripts/check_ses.py --send-to tu@correo.com
El envío real requiere confirmación explícita con --send-to.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services import ses as ses_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico de Amazon SES")
    parser.add_argument("--send-to", help="Envía un correo de prueba REAL a esta dirección")
    parser.add_argument("--from", dest="from_email",
                        default="notificaciones@leisureparts4u.store")
    args = parser.parse_args()

    print(f"Región configurada: {settings.AWS_REGION}")
    quota = ses_service.get_sending_quota()
    if quota.get("error"):
        print(f"✗ No se pudo consultar la cuota: {quota['error']}")
        print("  Revisa las credenciales IAM (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")
        return 1
    print(f"✓ Cuota 24 h: {quota['sent_last_24h']} / {quota['max_24h']}")
    print(f"✓ Máximo por segundo: {quota['max_per_second']}")

    identidades, err = ses_service.list_verified_identities()
    if err:
        print(f"✗ No se pudieron listar identidades: {err}")
    elif identidades:
        print("✓ Identidades verificadas:")
        for i in identidades:
            estado = "verificada" if i["verificado"] else "PENDIENTE"
            print(f"   - {i['identity']} ({i['tipo']}) [{estado}]")
    else:
        print("✗ Sin identidades: verifica el dominio leisureparts4u.store en la consola SES")

    if args.send_to:
        confirm = input(f"¿Enviar un correo de PRUEBA real a {args.send_to}? (escribe SI): ")
        if confirm.strip() != "SI":
            print("Cancelado.")
            return 0
        msg_id = ses_service.send_test_email(
            to_email=args.send_to,
            subject="[HJ Campaigns] Correo de prueba",
            html="<p>Prueba de envío de <b>HJ Campaigns</b> con Amazon SES.</p>",
            text="Prueba de envío de HJ Campaigns con Amazon SES.",
            from_email=args.from_email,
            from_name="HJ Campaigns",
        )
        print(f"✓ Enviado. MessageId: {msg_id}")
        print("  Recuerda: SES acepta el mensaje; la entrega real la confirman los eventos SNS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
