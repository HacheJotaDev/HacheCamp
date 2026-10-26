# HJ Campaigns

**Plataforma profesional de email marketing** con envíos mediante **Amazon SES** (región `us-east-2`), gestión de contactos con consentimiento, editor visual de correos, cola de envío con control de velocidad, procesamiento de rebotes y quejas, bajas compatibles con RFC 8058 y estadísticas reales.

> Estado: **v1.0.0** — lista para despliegue. El proyecto es totalmente independiente de otras instalaciones del servidor (no comparte base de datos, red, volúmenes ni puertos).

---

## ¿Qué incluye?

| Módulo | Descripción |
|---|---|
| **Panel administrativo** | Next.js 16 + TypeScript + Tailwind + shadcn/ui, tema oscuro/claro, responsive (Android incluido), 100 % en español |
| **API** | FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2, documentación OpenAPI en desarrollo |
| **Base de datos** | PostgreSQL 16 independiente + Redis 7 (colas y sesiones) |
| **Seguridad** | Argon2id, sesiones opacas en Redis, CSRF, MFA TOTP con códigos de recuperación, bloqueo por fuerza bruta, anti-enumeración, auditoría, sanitización HTML (nh3), cabeceras de seguridad |
| **Amazon SES** | API v2 con Boto3, MIME raw con `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058), cuotas reales, reintentos con backoff, idempotencia |
| **Contactos** | Alta manual, importación/exportación CSV (simulación incluida), listas, etiquetas, segmentos, historial de consentimiento, supresión global |
| **Editor** | Bloques arrastrables, HTML fuente, variables, vista previa escritorio/móvil en iframe aislado, versiones, envío de pruebas |
| **Campañas** | 9 estados con transiciones validadas, aprobación obligatoria, programación, pausa/reanudación/cancelación, progreso en tiempo real, historial |
| **Cola** | Worker independiente sobre Redis con velocidad configurable, reintentos exponenciales y recuperación tras reinicios |
| **Eventos** | Webhook SNS con verificación criptográfica de firma, aperturas y clics con tokens HMAC firmados, bajas públicas sin inicio de sesión |
| **Pruebas** | 70 pruebas automatizadas (pytest) con SES simulado: auth, roles, CSV, consentimiento, colas, pausas, reintentos, rebotes, quejas, seguridad y persistencia |

---

## Estructura del repositorio

```
hj-campaigns/
├── backend/            # API FastAPI, worker y scheduler (Python 3.12)
│   ├── app/            #   aplicación (core, api, services, workers)
│   ├── alembic/        #   migraciones de base de datos
│   ├── scripts/        #   bootstrap_admin.py, check_ses.py
│   └── tests/          #   suite de pruebas (70)
├── frontend/           # Panel Next.js 16 (App Router)
├── scripts/            # deploy.sh, update.sh, backup.sh, restore.sh, status.sh
├── docker-compose.yml  # postgres, redis, migrate, api, worker, scheduler, frontend
├── .env.example        # plantilla de configuración (nunca subir el .env real)
└── docs/               # documentación técnica y de operación
```

---

## Puesta en marcha rápida (VPS)

```bash
# 1. Clonar en la ruta prevista (NO toca /opt/campanas)
git clone https://github.com/HacheJotaDev/HacheCamp.git /opt/hj-campaigns
cd /opt/hj-campaigns

# 2. Configurar
cp .env.example .env
nano .env        # SECRET_KEY, POSTGRES_PASSWORD, credenciales SES, WEBHOOK_TOKEN...

# 3. Desplegar
bash scripts/deploy.sh

# 4. Crear el administrador (muestra una contraseña temporal una sola vez)
docker compose exec -T api python scripts/bootstrap_admin.py --email tu@dominio.com

# 5. Acceder por túnel SSH (recomendado hasta tener HTTPS)
ssh -L 8085:127.0.0.1:8085 root@169.58.36.6
# y abrir http://127.0.0.1:8085
```

Documentación completa:

- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — despliegue paso a paso, túnel SSH, acceso desde Android, webhook SNS y publicación por HTTPS.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — arquitectura, servicios, modelo de datos y flujo de envío.
- **[docs/SECURITY.md](docs/SECURITY.md)** — modelo de seguridad completo y amenazas cubiertas.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — operación diaria, colas, reconciliación, solución de problemas.
- **[docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)** — copias de seguridad y restauración.
- **[docs/API.md](docs/API.md)** — referencia de endpoints.

---

## Criterios de calidad respetados

- **Sin datos ficticios**: si no hay envíos, el panel muestra estados vacíos.
- **SES aceptado ≠ entregado**: el panel distingue aceptación por SES de entrega confirmada por evento SNS.
- **Aperturas aproximadas**: se advierte explícitamente el efecto de bloqueadores y proxies de imagen.
- **Consentimiento primero**: las campañas promocionales solo salen a contactos `suscripto` sin supresión; la baja funciona sin contraseña y bloquea futuros envíos.
- **Aprobación obligatoria**: ningún envío masivo sin aprobación de un administrador, y ninguno automático al desplegar.
- **Nada de credenciales en el código**: todo por `.env`/secretos; el repositorio no contiene claves.
- **Conservación**: el proyecto anterior (`/opt/campanas`) no se toca en ningún paso.

## Pruebas

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/ -v          # 70 pruebas, SES simulado, sin servicios externos
```

## Licencia

Uso interno. © 2026 HJ Campaigns.
