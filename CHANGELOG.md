# Changelog

Todas las cambios notables de HJ Campaigns se documentan aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y versionado SemVer.

## [2.0.0] — 2026-10-27

Versión PRO: plataforma de campañas profesional con flujo simplificado,
métricas correctas y visibilidad de entregabilidad honesta.

### Corregido (crítico)

- **HTML: los `<style>` dejan de destruirse** (`backend/app/services/sanitizer.py`).
  Causa: `nh3.clean()` trata `<style>` como *clean_content_tags* por defecto y
  eliminaba la etiqueta **y todo su CSS**. Ahora:
  1) `nh3` conserva los bloques `<style>` (`clean_content_tags={"script"}`) y el
     atributo `style`, manteniendo la protección contra JavaScript, atributos de
     eventos, URLs peligrosas (`javascript:`) y HTML activo;
  2) **Premailer** convierte el CSS a estilos inline (compatibilidad con Gmail y
     clientes que ignoran `<style>`) conservando las media queries en el bloque
     `<style>` para diseño responsive;
  3) endurecimiento CSS (`expression()`, `-moz-binding`, `@import`, `javascript:`
     eliminados de estilos inline y bloques).
  Canalización aplicada en vistas previas (`templates.py`), envío de pruebas,
  campañas, worker y envíos programados (`renderer.py`).

### Añadido (contactos)

- **Alta simplificada**: el formulario pide exclusivamente el correo electrónico.
  No se vuelve a pedir consentimiento a contactos ya suscriptos; los estados y
  bajas existentes se conservan sin inventar autorizaciones.
- **Importación CSV y TXT**: TXT con un correo por línea; deduplicación
  automática (en archivo y contra la base), validación de direcciones,
  codificaciones UTF-8/Latin-1 y asignación opcional a lista.
- **Selección múltiple + eliminación masiva con confirmación** (eliminar
  definitivamente o dar de baja + supresión), buscador y contador total.

### Añadido (campañas y envíos)

- **Asistente de creación en 5 pasos**: 1) destinatarios (con total de
  destinatarios elegibles mostrado antes del envío vía
  `POST /campaigns/recipients-preview`), 2) asunto, 3) plantilla o HTML pegado,
  4) enviar ahora o programar (fecha, hora, zona horaria), 5) confirmación.
  El remitente se toma automáticamente de la configuración
  (`default_from_name`/`default_from_email`).
- **Cola de envíos en tiempo real** (`GET /campaigns/queue/active` + página
  `/panel/cola`): por campaña muestra nombre, total, enviados, pendientes,
  entregados, fallidos, rebotes, reintentos, % avance, velocidad (mensajes/min),
  estado, hora de inicio/fin y último error, con barra de progreso y controles
  pausar/reanudar/cancelar. Los workers respetan pausa/cancelación y la
  unicidad (campaign_id, contact_id) evita envíos duplicados.
- **Programaciones**: modificar (`PUT /campaigns/{id}/schedule`), cancelar
  programación (`POST /campaigns/{id}/schedule/cancel`, vuelve a borrador),
  próximos e historial (`/campaigns/scheduled/upcoming|history`). Persisten en
  PostgreSQL, por lo que sobreviven a reinicios del VPS; zona horaria por
  campaña.
- **Métricas con denominadores explícitos** (`GET /stats/metrics`, global y por
  campaña): Success Rate = entregas / resultados definitivos; Bounce Rate =
  rebotes / aceptados por SES; Open Rate y Click Rate = destinatarios únicos /
  entregas (varias aperturas del mismo destinatario cuentan una vez); Failed =
  mensajes no enviados. Cuando una tasa no es calculable se muestra «—», nunca
  un cero inventado.

### Añadido (panel y monitorización)

- **Dashboard**: tarjetas Total / Sent / Delivered / Pending / Failed / Bounce /
  Success Rate / Bounce Rate / Open Rate / Click Rate / Complaints + gráfico de
  evolución diaria.
- **Página Estadísticas** con filtros por campaña y período (7/14/30/90 días) y
  explicación de cada denominador.
- **Página Entregabilidad**: Inbox confirmado y Spam confirmado aparecen como
  «no disponible» (Amazon SES no reporta la ubicación final; no se inventan
  cifras ni se confunde Delivered con Inbox), junto con entregado al servidor
  receptor, rebotado, rechazado, quejas y ubicación desconocida.
- **Monitor SES**: cuota diaria dinámica (consultada a la API, sin valores
  fijos), envíos de las últimas 24 h, velocidad máxima, reputación
  (`get_account`: estado de aplicación, envío habilitado) y errores de envío de
  las últimas 24 h. `get_sending_quota()` no se alteró.
- **Navegación completa** (Panel, Contactos, Listas, Plantillas, Campañas,
  Programadas, Cola de envíos, Estadísticas, Entregabilidad, Supresión,
  Ajustes) con modo claro/oscuro, menú móvil con todas las secciones y
  accesos rápidos inferiores en Android/móvil.

### Técnico

- `premailer>=3.10,<4.0` añadido a `requirements.txt` (sin cambios de esquema de
  base de datos: no se requieren migraciones Alembic).
- `docker-compose.yml` verificado de forma estática (YAML + anclas + redes):
  los 7 servicios (postgres, redis, migrate, api, worker, scheduler, frontend)
  resuelven en la red interna `hj_internal`; el panel permanece privado con
  `BIND_IP=127.0.0.1` y `PORT_FRONTEND=8085` (sin exposición HTTP pública).
- `frontend/tsconfig.json`: `typeRoots` fijado localmente (evita resolución de
  tipos desde directorios superiores).

### Pruebas

- Suite backend: **104 pruebas** (80 previas + 24 nuevas en
  `tests/test_pro_v2.py`): sanitizador/CSS inline (5), contactos solo-email e
  importación TXT/dedup/borrado masivo (8), preview de destinatarios y remitente
  automático (2), programar/reprogramar/cancelar/zona horaria (2), cola con
  pausa/reanudación (2), métricas con denominadores (3), entregabilidad (1) y
  serie filtrada (1). Todas en verde con SES/SQS simulados.
- Frontend: `next build` completo en verde con todas las páginas nuevas.

## [1.0.1] — 2026-10-27

Correcciones previas al despliegue en el VPS (reportadas tras clonar `ee845ee`).

### Corregido

- **`scripts/deploy.sh`**: la expresión `SECRET_LEN=${#SECRET_KEY:-0}` producía `bad substitution`; ahora se valida con `${#SECRET_KEY}` tras la carga y se muestran mensajes claros (longitud sin revelar el valor).
- **`.env` seguro**: ya no se hace `source .env` (ejecutaba cualquier contenido). Nuevo parser en `scripts/lib.sh` que solo acepta `CLAVE=valor` con nombres válidos, recorta comentarios en línea, ignora variables protegidas del shell (`PATH`, `IFS`, …) y rechaza líneas maliciosas sin ejecutarlas.
- **Redes Docker**: `migrate` (y el resto de servicios backend) quedan explícitamente en la red `hj_internal`; `migrate` ya puede alcanzar a PostgreSQL. Verificado con `docker compose config`.
- **`worker`**: llamada incorrecta `pop_message(timeout=3)` (la firma real es `timeout_seconds`) que rompería el procesamiento de la cola en cada ciclo.
- **`scripts/restore.sh`**: error de sintaxis por anidado de comillas en la consulta de terminación de conexiones; además la consulta usaba `current_setting('db')` (inválido) en lugar de `current_database()`.
- **Healthchecks sin suposiciones**: la imagen del backend instala `curl` explícitamente y falla el build si no estuviera; el frontend usa `node fetch` (sin depender de `wget`); los scripts usan respaldo con Python si `curl` no respondiera.
- **Backup**: el paquete ahora recibe `chmod 600` real (antes solo se sugería).

### Añadido

- **Eventos SES por SQS (canal privado)**: nuevo consumidor en el worker (long polling saliente, sin endpoint HTTP público) con verificación de firma SNS del sobre, idempotencia, lista blanca de tópicos, descarte de mensajes venenosos y soporte de eventos directos (raw). Script `scripts/setup_ses_events.py` (dry-run por defecto, `--aplicar`) que crea cola + DLQ + tópico + políticas mínimas + destino de eventos en SES de forma idempotente. El webhook HTTP se conserva como alternativa para despliegues con HTTPS.
- **Credenciales AWS como secretos Docker**: `api`, `worker` y `scheduler` reciben `/root/.aws` montado en `/run/secrets/` (UID 10001, modo 400) preparado por `deploy.sh`; Boto3 lo lee vía `AWS_SHARED_CREDENTIALS_FILE`/`AWS_CONFIG_FILE`/`AWS_PROFILE`. Las variables AWS vacías se sanezan para no romper la cadena de credenciales. Nada de esto entra en el repositorio.
- **`deploy.sh --check`**: validación completa sin desplegar (config, puertos, secretos, `docker compose config`).
- Consistencia `DATABASE_URL`/`POSTGRES_PASSWORD` validada en el despliegue (evita el fallo típico de contraseñas desincronizadas).

### Pruebas

- Suite backend: **80 pruebas** (70 previas + 10 nuevas del consumidor SQS), todas en verde con SES y SQS simulados.
- Scripts verificados con `bash -n` y pruebas funcionales (parser de `.env` ante inyección, variables protegidas, validaciones y secretos).

## [1.0.0] — 2026-10-26

Primera versión funcional completa.

### Añadido

**Seguridad y usuarios**
- Inicio/cierre de sesión con sesiones opacas en Redis (TTL deslizante 8 h).
- Contraseñas Argon2id + política mínima (12 caracteres, números y letras).
- MFA TOTP para administradores con códigos de recuperación de un solo uso.
- Bloqueo temporal por fuerza bruta (por correo e IP) y respuestas anti-enumeración.
- Protección CSRF por cabecera en todas las mutaciones autenticadas.
- Roles `admin`/`operador` con verificación server-side.
- Registro de auditoría de acciones administrativas.
- Revocación total de sesiones por usuario y cambio de contraseña con invalidación.
- Bootstrap seguro del administrador por CLI (sin credenciales por defecto; contraseña mostrada una sola vez).

**Contactos**
- Alta/edición manual, importación CSV (UTF-8/Latin-1, detección de delimitador, hasta 5 MB, 100 k filas) y exportación auditada.
- Modo simulación (`dry_run`) de importación con resumen previo.
- Detección de duplicados (en archivo y contra la BD) y normalización de direcciones.
- Listas, etiquetas y segmentos por reglas simples.
- Estado de suscripción y **historial de consentimiento** (fuente, fecha, texto).
- Lista de supresión global con motivos y orígenes.

**Editor y plantillas**
- Editor de bloques (encabezado, título, texto enriquecido, imagen, botón, columnas, separador, espaciador, pie con baja) con reordenación drag & drop.
- Modo HTML fuente con validación de sintaxis Jinja2 y sanitización.
- Vista previa escritorio/móvil renderizada por la API y mostrada en iframe aislado.
- Variables: `{{contact.first_name}}`, `{{contact.last_name}}`, `{{contact.email}}`, `{{campaign.name}}`, `{{campaign.subject}}`, `{{unsubscribe_url}}`, `{{web_view_url}}`.
- Historial de versiones con restauración, duplicado de plantillas y envío de pruebas.
- 5 plantillas de sistema sembradas (bienvenida, newsletter, promoción, aviso de servicio, confirmación).
- Generación automática de versión de texto plano.

**Campañas y envío**
- 9 estados con máquina de transiciones validada y aprobación administrativa obligatoria.
- Programación con zona horaria; pausa, reanudación y cancelación con gestión de pendientes.
- Cola persistente en Redis (AOF) con control de velocidad global (mínimo entre configuración y `MaxPerSecond` real de SES).
- Worker independiente con reintentos exponenciales (máx. 4, hasta 30 min), idempotencia por (campaña, contacto) y estados persistentes.
- Detección de envíos inciertos tras interrupciones (sin reenvío ciego) y reconciliación manual.
- SES API v2 con Boto3 en región `us-east-2`, MIME raw con cabeceras `List-Unsubscribe` y `List-Unsubscribe-Post` (RFC 8058), `Feedback-ID`, etiquetas de mensaje y Reply-To configurable.
- Consulta real de cuotas y consumo de SES con caché de 10 minutos.

**Eventos y entregabilidad**
- Webhook SNS con verificación criptográfica de firma (RSA-SHA1 + certificado de Amazon, anti-SSRF), token en URL y lista blanca opcional de tópicos.
- Procesamiento idempotente de `delivery`, `bounce` (permanente/temporal), `complaint`, `send` y `reject`.
- Supresión automática ante rebote permanente o queja, con actualización del estado del contacto y registro del motivo.
- Seguimiento de aperturas (píxel 1×1) y clics (redirección con URL firmada HMAC) con deduplicación razonable.
- Página pública de baja `/baja` sin inicio de sesión (correo enmascarado) + endpoint one-click RFC 8058 idempotente.

**Panel y estadísticas**
- Dashboard con tarjetas de métricas reales, gráfico de evolución diaria (Recharts), consumo de cuota SES y estados vacíos honestos.
- Tema oscuro/claro, diseño responsive con navegación móvil inferior, todo en español.
- Gestión de usuarios, auditoría, estado de SES y ajustes por clave-valor.
- Vista de progreso en tiempo real por campaña con conteos por estado y eventos.

**Infraestructura**
- Docker Compose con 7 servicios (postgres, redis, migrate, api, worker, scheduler, frontend), red interna, volúmenes persistentes, healthchecks, límites de recursos, logs rotados y usuarios sin privilegios.
- Migraciones Alembic ejecutadas como paso previo obligatorio.
- Scripts: `deploy.sh`, `update.sh`, `backup.sh`, `restore.sh`, `status.sh`, `check_ses.py`, `bootstrap_admin.py`.
- Documentación completa: README, ARCHITECTURE, SECURITY, DEPLOYMENT, OPERATIONS, BACKUP_RESTORE, API, CHANGELOG y `.env.example`.

**Pruebas**
- 70 pruebas automatizadas con SES simulado y fakeredis: autenticación, roles, CSRF, fuerza bruta, MFA, CSV, consentimiento, supresión, render/sanitización, flujo completo de campaña, pausa/reanudación, reintentos, errores SES, idempotencia, webhook SNS con firma real, aperturas/clics, bajas y estadísticas.

### Notas de seguridad
- No incluye credenciales ni secretos; todo se inyecta por `.env` (ver `.env.example`).
- El login no se publica a Internet en la configuración por defecto (`BIND_IP=127.0.0.1`).
