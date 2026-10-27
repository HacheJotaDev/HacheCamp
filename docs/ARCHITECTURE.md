# Arquitectura de HJ Campaigns

## 1. Visión general

HJ Campaigns es una aplicación monorepo con dos imágenes principales y cuatro servicios de ejecución, orquestados con Docker Compose en una red interna aislada:

```
                       ┌─────────────────────────────────────────────┐
  Navegador / móvil    │  VPS (Contabo, Ubuntu 24.04, 169.58.36.6)   │
  (túnel SSH o HTTPS) ─┤  127.0.0.1:8085        127.0.0.1:8086       │
                       │      │                        │             │
                       │  [frontend Next.js]  [api FastAPI]          │
                       │      │  rewrites /api/v1 →   │  │           │
                       │      │  http://api:8000      │  │           │
                       │      └──────────┐   ┌────────┘  │           │
                       │           red interna hj_campaigns_internal  │
                       │      ┌───────┴───────────────┴──────┐      │
                       │      │  postgres:16   redis:7        │     │
                       │      └──────────┬────────────────────┘      │
                       │   [worker]  [scheduler]  [import-worker]  │
                       └──────────────────┬──────────────────────────┘
                                          │ HTTPS (boto3)
                                          ▼
                              Amazon SES API v2 (us-east-2)
                              Amazon SNS → webhook de eventos
```

**Independencia garantizada**: nombre de proyecto `hj-campaigns`, red `hj_campaigns_internal`, volúmenes `hj_campaigns_pgdata` y `hj_campaigns_redisdata`. Ningún recurso comparte nombre con otros proyectos del servidor; no se publican puertos de PostgreSQL ni Redis.

## 2. Servicios

| Servicio | Imagen | Función | Notas |
|---|---|---|---|
| `frontend` | Node 20 (standalone Next.js) | Panel administrativo y página pública de baja | Reescribe `/api/v1/*` hacia el API: cookies sin CORS |
| `api` | Python 3.12 (uvicorn) | API REST, autenticación, render, webhooks | Healthcheck `/api/v1/healthz` |
| `worker` | Misma imagen que api | Consume la cola de envío con control de velocidad | Bucle BRPOP sobre Redis |
| `import-worker` | Misma imagen que api | Importaciones masivas de contactos: procesa `hj:q:import` por lotes de 500 con commits cortos; reanuda trabajos tras reinicios | Separado del worker de envíos para que una importación de 50k no compita con el envío |
| `scheduler` | Misma imagen que api | Campañas programadas, reintentos vencidos, reconciliación de envíos inciertos, refresco de cuota | Ciclo de 20 s |
| `postgres` | postgres:16-alpine | Persistencia única | Sin puertos públicos |
| `redis` | redis:7-alpine | Sesiones, cola, reintentos, caché de cuota, limitadores | AOF activado, sin puertos |
| `migrate` | Misma imagen que api | `alembic upgrade head` (one-shot antes de arrancar) | `service_completed_successfully` |

## 3. Modelo de datos (PostgreSQL)

| Tabla | Contenido clave |
|---|---|
| `users` | Email, hash Argon2id, rol (`admin`/`operador`), TOTP opcional, códigos de recuperación hasheados |
| `audit_logs` | Trazabilidad de acciones administrativas (quién, qué, cuándo, IP) |
| `contacts` | Email normalizado (único), estado (`suscripto`/`dado_baja`/`rebote`/`queja`), consentimiento (fuente, fecha, texto), atributos JSON |
| `contact_lists`, `contact_lists_rel`, `tags`, `contact_tags_rel`, `segments` | Listas, etiquetas y segmentos por reglas simples |
| `consent_events` | Historial inmutable de altas, consentimientos, bajas, rebotes y quejas |
| `suppressions` | Supresión global: rebotes permanentes, quejas, bajas y manuales |
| `templates`, `template_versions` | Contenido por bloques o HTML, con versionado al cambiar contenido |
| `campaigns`, `campaign_history` | Configuración congelada del contenido, estados, aprobación, programación, historial de acciones |
| `campaign_messages` | Un registro por destinatario; **unicidad (campaña, contacto) = idempotencia**; estados `pendiente→enviando→aceptado→entregado`, errores, `incierto` |
| `ses_events` | Todos los eventos (SNS, aperturas, clics, envío local) con `event_uid` único = deduplicación |
| `app_settings` | Ajustes clave-valor editables desde el panel |

Decisiones de portabilidad: identificadores `String(36)` y tipo `UTCDateTime` propio permiten pruebas en SQLite y producción en PostgreSQL sin divergencia semántica de tiempos (todo normalizado a UTC).

## 4. Flujo de envío (cola asíncrona)

1. **Creación**: la campaña congela bloques/HTML y genera texto plano.
2. **Aprobación**: un administrador firma la campaña (`approved_at`); sin esto no hay envío ni programación.
3. **Encolado** (`send-now` o vencimiento de programación): se crean los `campaign_messages` (idempotente), se filtran suprimidos/bajas/rebotes y se publica cada mensaje en `hj:q:outbound` (lista Redis, AOF persistente).
4. **Worker**: por cada trabajo comprueba estado de campaña (pausa → reencola con retardo; cancelada → cancela), supresión vigente e idempotencia; aplica *token bucket* global (mínimo entre `SEND_RATE_PER_SECOND` y `MaxPerSecond` real de SES); renderiza con Jinja2 sandboxed + sanitizador nh3; construye MIME raw con cabeceras de baja; envía por SES v2.
5. **Resultados**:
   - `MessageId` → estado `aceptado` + evento `envio_local`.
   - Throttling → reintento en `hj:q:retry` (ZSET) con backoff exponencial (60→120→240…, máx. 30 min, 4 intentos).
   - Rechazo/`MessageRejected` → estado `error` con detalle (sin reintento).
6. **Eventos SNS** → webhook firmado: `delivery` marca `entregado`; `bounce` permanente y `complaint` suprimen la dirección globalmente; todo queda en `ses_events` (deduplicado).
7. **Cierre**: al no quedar pendientes, la campaña pasa a `completada` (o `fallida` si todo falló).

### Envíos inciertos y reconciliación

Si el proceso muere después de que SES acepte un mensaje pero antes de persistir la respuesta, el registro queda `enviando`. El scheduler marca como `incierto` los mensajes `enviando` con más de `STALE_SENDING_MINUTES` y el panel permite identificarlos. **Nunca se reenvía a ciegas**: la reconciliación manual decide según el `MessageId`. El estado incierto también es reenviable si el operador lo confirma (el mensaje nunca se duplica en la cola de destino por la unicidad campaña-contacto).

## 5. Render y seguridad de contenido

- **Bloques → HTML**: tabla de 600 px, atributos `role="presentation"`, estilos inline (compatibilidad con clientes de correo), pie con enlace de baja inyectado.
- **Variables**: Jinja2 `SandboxedEnvironment` con autoescape y `undefined` tolerante. Variables: `{{contact.*}}`, `{{campaign.*}}`, `{{unsubscribe_url}}`, `{{web_view_url}}`.
- **Sanitización**: nh3 (ammonia) en tres puntos: guardado de plantillas, guardado de campañas y render final por destinatario. Sin JavaScript, sin iframes, esquemas limitados a http/https/mailto.
- **Enlaces de clic** redirigidos con firma HMAC-SHA256 (`/api/v1/t/click/{token}`): imposible forjar redirecciones externas (anti open-redirect).
- **Vista previa** en `iframe sandbox=""` servida por el API (el HTML nunca se inyecta en el DOM del panel).

## 6. Bajas (RFC 8058) y supresión

- Cabeceras en cada correo promocional: `List-Unsubscribe` (URL de página + one-click) y `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.
- Página pública `/baja?token=...` (sin login, correo enmascarado) y endpoint `POST /api/v1/public/unsubscribe/one-click` para el cliente de correo.
- La baja: marca el contacto `dado_baja`, añade supresión global y registra el evento de consentimiento. El filtrado ocurre **dos veces**: al encolar y de nuevo justo antes de cada envío.

## 7. Seguridad (resumen — detalle en SECURITY.md)

Argon2id · sesiones opacas en Redis con TTL deslizante · CSRF por cabecera en mutaciones · bloqueo temporal por fuerza bruta · anti-enumeración en login · MFA TOTP + códigos de recuperación · auditoría · verificación criptográfica SNS con restricción de host de certificado (anti-SSRF) · CORS por lista · cabeceras `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` · secretos solo por entorno · logs estructurados con redacción de datos sensibles.

## 8. Decisiones técnicas y justificación

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| FastAPI + SQLAlchemy (sync) | NestJS/Django | Requisito del encargo; modo sincrónico simple y robusto con threadpool de Starlette |
| MIME raw en SES v2 | SMTP relay con Postfix | Control total de cabeceras RFC 8058 sin tocar el MTA existente del VPS |
| Cola propia sobre Redis | Celery/RQ | Menos dependencias, control fino de velocidad/idempotencia y estados, pausa por campaña trivial |
| Next.js rewrites hacia el API | CORS abierto | Mismo origen → cookies `SameSite=Lax` sin exposición |
| UUID como String(36) | UUID nativo PG | Pruebas idénticas en SQLite; el índice por PK text es suficiente al volumen previsto |
| nh3 | bleach (deprecado) | Mantenimiento activo, rendimiento y API clara |

## 9. Escalado posterior previsto

- Múltiples workers: la cola es una lista Redis; basta `docker compose up --scale worker=2` (el control de velocidad ya es global).
- Publicación HTTPS: proxy inverso (Caddy/Nginx) frente a `frontend`; variables `PUBLIC_BASE_URL`/`COOKIE_SECURE` listas para ello.
- Métricas avanzadas: `ses_events` admite agregaciones por campaña/fecha sin cambios de esquema.
