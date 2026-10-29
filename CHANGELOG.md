# Changelog

Todas las cambios notables de HJ Campaigns se documentan aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y versionado SemVer.

## [Sin publicar]

### Corregido

- **Auditoría V4 — envíos bajo control global (rate limiter fail-closed)**:
  - El limitador de velocidad ya NO falla abierto: si Redis cae, el mensaje
    se repone a `pendiente` y se reprograma (`RateLimiterUnavailable` +
    `RATE_LIMIT_FAIL_CLOSED=true`). Antes cada worker enviaba sin slot global
    (con 14 réplicas: hasta 14x la velocidad autorizada contra SES).
  - **Claim atómico** del mensaje (UPDATE con guarda de estado): si el mismo
    `mid` aparece dos veces en la cola (crash + reencolado, réplica escalada),
    solo una reclamación gana: imposible el doble envío.
  - **Puerta de cuota 24 h**: `cuota_24h_restante()` lee SOLO la caché Redis
    (cero llamadas AWS por mensaje); si la cuota restante baja de
    `QUOTA_24H_MIN_REMAINING` el mensaje se reencola en vez de recibir un
    error definitivo. "Daily message quota exceeded" es reintento programable.
  - `ses_configured()` cachea la sonda 60 s (antes: 1 `GetAccount` por envío).
  - Los envíos de prueba (plantillas y campañas) pasan por el MISMO limiter
    global; con Redis caído responden 503 y no envían.
- **Auditoría V4 — campañas escalables y sin huérfanos**:
  - `enqueue_campaign` por trozos de 1000 (1 consulta `IN` por trozo e
    inserción masiva con fallback fila a fila): 50k destinatarios dejaban
    ~100k consultas y una transacción larguísima.
  - Supresión resuelta en UNA consulta (`emails_suprimidos`) en vez de una
    por contacto; semántica idéntica.
  - `recover_orphaned_messages` (scheduler, cada 5 min): mensajes `pendiente`
    que perdieron su sitio en la cola Redis (crash entre BRPOP y envío) se
    reencolan cuando cola y reintentos están vacías; `incierto` queda
    excluido a propósito (reconciliación manual, evita duplicar aceptados).
- **Auditoría V4 — eventos SES con semántica completa**:
  - `DeliveryDelay` y `RenderingFailure` con ramas propias (antes caían en la
    rama genérica: estados ambiguos). El retraso NO cambia el estado; el
    fallo de render marca error con motivo explícito.
  - Rebote temporal: reintento programado con tope `RETRY_MAX_ATTEMPTS`
    (antes quedaba varado y la campaña terminaba con pendientes eternos).
  - Carrera webhook↔SQS capturada por `event_uid`: se cuenta como DUPLICADO
    en vez de error + rollback + reenvío de cola.
  - Clics repetidos al mismo enlace: todos se registran (el uid fijo por URL
    descartaba el 2º en adelante; métricas infra-contadas).
  - `/system/ses/diagnostico`: contador `sin_vincular_a_mensaje` (pipeline
    vivo sin correlación) para investigar "50.632 aceptados / 6 entregados".
- **Auditoría V4 — importación blindada**:
  - Lock Redis por trabajo (`SET NX EX 900 s` con token, renovación por
    lote, liberación atómica por Lua): si se escala el import-worker, solo
    UNO procesa; los demás omiten (antes: doble procesamiento y contadores
    inflados).
  - Recuperación PERIÓDICA (60 s, edad mínima 60 s) de trabajos estancados:
    `pendiente` que nadie tomó y `procesando` sin lock vivo se reencolan;
    los `procesando` con lock vigente no se tocan. Antes solo al arrancar.
- Regresión V4: 30 pruebas nuevas (`test_auditoria_v4.py`) cubriendo todo lo
  anterior, incluida la carga real de 50.656 contactos y la matriz de
  concurrencia con 14 réplicas.

- **Importación masiva de contactos asíncrona** (el CSV de 50.656 correos
  bloqueaba el API, dejaba transacciones PostgreSQL "idle in transaction" y la
  base acababa con 0 contactos):
  - El API ya NO importa dentro del request: prevalida el archivo en un hilo
    (parse + validación + deduplicación), persiste el plan y responde **202**
    con un `job_id` en segundos. Un **worker importador separado** del worker
    de envíos (servicio `import-worker` en Docker Compose) procesa el trabajo
    **por lotes de 500 con commits cortos**: sin transacciones largas, sin
    bloquear INSERTs ajenos y con el login respondiendo todo el tiempo.
  - Progreso persistente en `import_jobs` (creados, actualizados, inválidos,
    duplicados, errores, avisos de supresión/estado) consultable desde el
    panel; el plan prevalidado va en `import_job_data` (tabla 1:1) para que
    las actualizaciones de progreso nunca reescriban megabytes.
  - **Reanudación tras reinicios**: al arrancar, el worker reencola los
    trabajos pendientes/interrumpidos y continúa desde el último lote
    confirmado. Idempotente: reintentar un lote no duplica contactos ni infla
    cifras (el conteo creado/actualizado se decide contra el estado real de
    la BD por lote); una fila problemática no tira el trabajo (ruta lenta
    fila a fila). Cancelación entre lotes conservando el progreso.
  - Invariantes intactos: validación CSV/TXT y codificación, deduplicación,
    asociaciones a listas/etiquetas, eventos de consentimiento, auditoría
    (solicitud en el API + completado en el worker) y **nunca reactiva**
    bajas, rebotes, quejas ni correos suprimidos.
  - Panel de contactos: subida asíncrona con barra de progreso sondeada,
    estadísticas finales, cancelación y "últimas importaciones" para consultar
    el progreso sin re-subir.
  - Regresión: alias `GET /system/ses/status`, `/system/audit` y
    `/system/settings[/{key}]` (la pestaña SES de Ajustes devolvía 404 porque
    el frontend llama con prefijo `/system` y el backend lo montaba sin él;
    las rutas antiguas siguen funcionando).
  - Lint del frontend a 0 errores: `useIsMobile` reescrito con
    `useSyncExternalStore`, estado inicial del carrusel diferido un tick y el
    editor de plantillas usa el patrón "ajustar estado durante el render".
  - Pruebas: 19 nuevas (flujo asíncrono completo, **50.656 contactos con el
    login respondiendo durante y después**, reimportación idempotente de 50k,
    reanudación tras crash, cancelación, supresión/baja/rebote/queja intactos,
    consentimiento+listas+etiquetas, auditoría, cola independiente,
    límite de filas). Suite: 185/185.

- **"Configuración de correo" es la única fuente de verdad para TODOS los envíos**
  (corrección de la integración anterior):
  - El worker (envíos inmediatos **y programados**), la prueba de campañas y la
    prueba de plantillas resuelven remitente y asunto desde la configuración
    global **en el momento del envío**, aunque la campaña antigua tenga otros
    valores almacenados; sus registros históricos nunca se reescriben.
  - El backend ignora cualquier `from_email`/`from_name`/`subject` enviado por
    el frontend (incluido el `from_name: "HJ Campaigns"` que la prueba de
    plantillas hardcodeaba y por eso Gmail mostraba el remitente antiguo).
  - El Subject enviado a SES es exactamente el configurado: se eliminaron los
    prefijos `[PRUEBA]` y el fallback inventado `[Prueba] HJ Campaigns`.
  - Formularios con valores globales en modo lectura: la creación de campañas
    ya no edita asunto (si no hay asunto configurado no se puede continuar) y
    los dos diálogos de prueba solo dejan editar el destinatario.
  - En "Configuración de correo" el dominio `@leisureparts4u.store` es fijo y
    visible: solo se edita la parte local del correo.
  - `GET /system/mail-config` responde con `Cache-Control: no-store` y las
    consultas React Query usan `staleTime: 0` + `refetchOnMount: "always"`:
    guardar se refleja al reabrir cualquier formulario sin reiniciar nada.
  - Compatibilidad: `POST /campaigns` sigue aceptando `subject`/`from_name`/
    `from_email` de clientes antiguos, pero solo como último recurso cuando no
    hay configuración guardada; con config guardada la config global siempre gana.

### Añadido

- **Configuración de correo** (remitente y asunto predeterminados centralizados):
  - Nuevo apartado independiente en el menú lateral (`/panel/config-correo`),
    al mismo nivel que Campañas, Listas y Plantillas, con tres campos editables
    (nombre del remitente, correo del remitente y asunto predeterminado),
    previsualización de la cabecera `From` y botón **Guardar configuración**.
  - Backend: `GET/PUT /api/v1/system/mail-config` reutiliza la tabla
    `app_settings` (sin migraciones). Solo administradores guardan (`403` para
    el resto); lectura para cualquier usuario autenticado.
  - Validación en frontend y backend (`app/services/mail_config.py`): el correo
    del remitente debe pertenecer exclusivamente al dominio verificado
    `leisureparts4u.store`; se rechazan saltos de línea y caracteres de control
    (anti inyección de cabeceras RFC 5322); longitudes acotadas. El endpoint
    genérico `PUT /settings/{key}` comparte las mismas reglas para las claves
    de correo.
  - Los envíos de prueba (plantillas y campañas) usan la configuración sin
    reintroducir datos; el destinatario sigue siendo editable e independiente
    (ver «Corregido»: la configuración es la única fuente de verdad).
  - 24 pruebas de regresión (`backend/tests/test_mail_config.py`), incluida la
    verificación de las cabeceras `From`/`Subject` construidas para el mensaje
    (decodificadas como las mostraría Gmail).

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
  **Protección anti-reactivación (revisión final)**: un correo que esté en la
  lista de supresión **nunca** pasa a «suscripto» — si no tiene contacto previo
  se crea como «dado_baja» (aviso `correo_suprimido`); si ya existe como
  suscripto, la supresión permanece y lo sigue excluyendo de envíos
  (`can_send_promotional` → `en_supresion`). Reimportar un suscripto no altera
  su suscripción ni duplica registros. Cubierto por 3 pruebas nuevas.
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
  **Estado: funcionalidad parcialmente implementada.** Lo que sí funciona y
  está conectado a datos reales de SES/SQS: entregas al servidor receptor,
  rebotes (hard/soft), rechazos, quejas (la única señal de spam que SES
  reporta) y ubicación desconocida. **Pendiente (NO declarar como terminado):**
  la medición real de Inbox vs Spam requiere pruebas con buzones semilla
  (seedlist) autorizados o un servicio externo de monitorización; mientras no
  se configure, la página lo muestra como «No disponible» con el motivo.
- **Monitor SES**: cuota diaria dinámica (consultada a la API, sin valores
  fijos), envíos de las últimas 24 h, velocidad máxima, reputación
  (`get_account`: estado de aplicación, envío habilitado) y errores de envío de
  las últimas 24 h. Cuotas vía `GetAccount` (corrección de producción: ver
  revisión 3).
- **Navegación completa** (Panel, Contactos, Listas, Plantillas, Campañas,
  Programadas, Cola de envíos, Estadísticas, Entregabilidad, Supresión,
  Ajustes) con modo claro/oscuro, menú móvil con todas las secciones y
  accesos rápidos inferiores en Android/móvil.

### Técnico

- `premailer>=3.10,<4.0` añadido a `requirements.txt` (sin cambios de esquema de
  base de datos: no se requieren migraciones Alembic).
- `docker-compose.yml` verificado de forma estática (YAML + anclas + redes):
  los 7 servicios (postgres, redis, migrate, api, worker, scheduler, frontend)
  resuelven en la red interna `hj_internal`. Los puertos usan
  `${BIND_IP:-127.0.0.1}`: **el acceso real lo define el `.env` del VPS y esta
  versión no lo cambia** (el procedimiento de `docs/ACTUALIZACION_V2.md` lo
  comprueba antes y después del despliegue).
- `frontend/tsconfig.json`: `typeRoots` fijado localmente (evita resolución de
  tipos desde directorios superiores).

### Pruebas

- Suite backend: **110 pruebas** (80 previas + 24 nuevas en `tests/test_pro_v2.py`
  + 3 de endurecimiento de seguridad: bloqueo SSRF de IPs internas/metadata, CSS
  `behavior:url` eliminado + 3 de contactos: suprimido no se activa, suscripto
  no se altera al reimportar, supresión previa se conserva). Todas en verde
  con SES/SQS simulados.
- Frontend: `next build` completo en verde con todas las páginas nuevas.

### Estado de las funciones (v2.0.0)

| Función | Estado |
|---|---|
| Contactos solo-email, import CSV/TXT, dedup, borrado masivo, protección anti-reactivación de suprimidos | ✅ Completa (probada) |
| Asistente de campañas en 5 pasos con conteo previo | ✅ Completa (probada) |
| CSS `<style>` conservado e inlineado (Premailer) | ✅ Completa (probada) |
| Cola en tiempo real con pausar/reanudar/cancelar | ✅ Completa (probada) |
| Programaciones persistentes (reprogramar/cancelar) | ✅ Completa (probada) |
| Métricas con denominadores (success/bounce/open/click) | ✅ Completa (probada) |
| Monitor SES (cuota dinámica, reputación, errores 24 h) | ✅ Completa (vía `GetAccount` SESv2) |
| Eventos SES: delivery, bounce, complaint | ⏳ **Código listo (SQS), medición NO verificada en producción**: requiere crear la cola + destino de eventos y verificar el flujo completo (`docs/Eventos_SES_SQS.md`); sin configurar, los contadores permanecen en 0 |
| **Medición real de Inbox vs Spam** | ⏳ **Pendiente**: requiere seedlist autorizada o servicio externo; la página muestra «No disponible» honestamente |
| Aperturas/clics reales (píxeles y enlaces) | ✅ Disponible vía tracking propio (requiere imágenes habilitadas en el cliente) |
| Despliegue real en VPS | ⏳ Pendiente de autorización (revisar PR #1 y seguir `docs/ACTUALIZACION_V2.md`)

### Corregido en la revisión final (respaldos y restauración)

- `docs/ACTUALIZACION_V2.md`: el respaldo obligatorio es **`pg_dump` con
  `set -o pipefail`** + verificación de integridad (tamaño no nulo, `gzip -t`,
  marcador «PostgreSQL database dump complete») + **restauración de prueba no
  destructiva** en contenedor desechable (demuestra que el dump es restaurable
  y cuenta registros reales, sin tocar la base de producción).
- El `tar` en caliente del volumen (inconsistente con PostgreSQL en marcha) se
  eliminó del procedimiento; la copia física es **opcional** con
  **`pg_basebackup`** (mecanismo consistente de PostgreSQL) y sus requisitos
  documentados (`-u postgres` porque `pg_basebackup` rechaza root, espacio en
  disco, replicación local por socket del contenedor).
- La restauración de datos ya **no incluye comandos destructivos automáticos**:
  exige confirmación explícita escrita (`RESTAURAR`), restaura en un **volumen
  nuevo** apuntado con override temporal y el volumen original queda intacto.

### Corregido en la revisión de producción 3 (healthchecks, SES y eventos)

- **Healthchecks worker/scheduler (unhealthy con FailingStreak 210)**: la
  imagen ejecutaba `curl 127.0.0.1:8000` en TODOS los contenedores, pero
  worker y scheduler no exponen HTTP. Nuevo módulo `app/workers/heartbeat.py`:
  cada ciclo del bucle registra un latido real en Redis; el healthcheck
  (`python -m app.workers.heartbeat <nombre> --max-age N`) comprueba Redis
  alcanzable + latido fresco (worker 120 s, scheduler 300 s), no mera
  existencia del proceso. Scheduler ahora también apaga limpio con SIGTERM.
  18 pruebas nuevas (128 en verde: 14 de heartbeat + 4 de cuota/envío SES).
- **Cuota SES vía `GetAccount`**: incorporada la corrección local del VPS —
  `get_account_sending_capabilities()` no existe en SESv2 y rompía la consulta
  en producción. Ahora `get_account()` lee `SendQuota.{Max24HourSend,
  SentLast24Hours, MaxSendRate}` + `SendingEnabled`, con la misma interfaz de
  salida (sin regresiones en monitor/estadísticas). Mock de pruebas ajustado a
  la forma real de la respuesta.
- **Separación de puertos como en producción**: `BIND_IP_API` (127.0.0.1,
  privada) y `BIND_IP_FRONTEND` (público en el VPS con `0.0.0.0:8085`) —
  variables explícitas por servicio; `deploy.sh --check` las valida.
- **Frontend**: `@tailwindcss/postcss` añadido a devDependencies (lo usaba
  `postcss.config.mjs` sin declararlo; el build Docker fallaría limpio).
  `next build` en verde.
- **Permisos IAM documentados** (`docs/SES_PERMISOS.md`): el código usa SESv2
  `SendEmail` (contenido raw) → acción `ses:SendEmail`, no `ses:SendRawEmail`
  (v1, que es la denegada). Política mínima + verificación con
  `simulate-principal-policy`; sin pedir credenciales ni ampliar permisos.
- **Eventos SES honestidad** (`docs/Eventos_SES_SQS.md`): sin
  `AWS_SQS_EVENTS_QUEUE_URL` configurada y sin verificación de punta a punta,
  entregas/rebotes/quejas NO se miden (contadores en 0). Log del worker ahora
  lo indica con la guía de conexión. La medición queda marcada como
  **pendiente de verificación**, no funcional.

### Corregido en la revisión de producción 4 (post-despliegue: preview, importación y eliminaciones)

- **Previsualización de plantillas HTML rota (500 NameError)**: `templates.py`
  llamaba a `inline_email_css()` sin importarla (`renderer.py` la importaba;
  `templates.py` no). El error solo aparecía al usar el endpoint
  `POST /templates/preview` con `mode=html`, por eso la suite no lo detectaba.
  Corregida la importación; el preview vuelve a aplicar el CSS inline.
- **Importación CSV/TXT con «Field required»**: el frontend llamaba a
  `api.post(path, formData)`; `api.post` serializa su argumento como JSON
  (un FormData se convierte en `{}` con `Content-Type: application/json`), de
  modo que la API nunca recibía el archivo y respondía 422 con el mensaje
  críptico «Field required» (campo `archivo`). El backend y el nombre de campo
  `archivo` eran correctos. Corregido con `api.postForm` (que no fija
  Content-Type y deja que el navegador ponga el boundary), el tipado de
  `api.post` ahora exige objetos planos (impide repetir el error) y los
  errores 422 se muestran con el campo afectado («campo 'archivo': …»).
- **Eliminación de contactos**: añadida la eliminación individual (botón en la
  fila y en el detalle) reutilizando `POST /contacts/bulk-delete` con
  confirmación explícita, junto a la masiva existente. Integridad verificada
  por pruebas: los mensajes de campaña y el historial de consentimiento se
  borran en cascada, la campaña no se rompe, las **supresiones del correo se
  conservan** (un correo suprimido nunca vuelve a «suscripto» tras
  reimportarse) y la **auditoría** registra la acción. El contador de la tabla
  se actualiza automáticamente.
- **Eliminación de plantillas mejorada**: confirmación con `AlertDialog` en
  lugar de `confirm()` nativo; las plantillas del sistema siguen protegidas
  (400); al eliminar una plantilla usada por campañas, la API responde con el
  número de campañas asociadas y la interfaz lo informa — no se rompe nada
  porque el contenido de la campaña se congela al crearse y la FK es
  `ON DELETE SET NULL` (verificado con pruebas).
- Sin cambios de esquema de base de datos (no se requieren migraciones).
- **Suite backend: 142 pruebas** en verde (128 previas + 14 nuevas de
  regresión en `tests/test_fixes_produccion.py`). `next build` en verde.

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
