# Seguridad de HJ Campaigns

## 1. Modelo de amenazas cubierto

| Amenaza | Mitigación implementada |
|---|---|
| Robo de credenciales | Argon2id (64 MiB, resistente a GPU), política de contraseñas ≥12 con números y letras, sin credenciales por defecto: el administrador se crea por CLI con contraseña aleatoria mostrada una sola vez |
| Fuerza bruta en login | Contadores Redis por correo (5 fallos) y por IP (20), bloqueo temporal de 15 min, respuesta 429 |
| Enumeración de usuarios | Mensaje único «Credenciales inválidas» para correo inexistente, cuenta desactivada o contraseña errónea; misma latencia |
| Secuestro de sesión | Token opaco de 256 bits en cookie `HttpOnly`, `SameSite=Lax`, `Secure` (en HTTPS), TTL deslizante de 8 h, revocación total por usuario |
| CSRF | Token CSRF por sesión entregado en `/auth/me` y exigido en cabecera `X-CSRF-Token` para toda mutación autenticada (prueba automatizada incluida) |
| XSS en el panel | React (autoescape), sin `dangerouslySetInnerHTML` en el DOM del panel; el HTML de correos solo se muestra dentro de `iframe sandbox=""` servido por la API |
| XSS en correos | nh3 con lista blanca de etiquetas/atributos, sin scripts/iframes/eventos, esquemas http/https/mailto; sanitización en guardado y de nuevo por destinatario |
| Inyección de plantillas | Jinja2 `SandboxedEnvironment`, sin acceso a atributos peligrosos ni globals; validación de sintaxis antes de guardar |
| Open redirect | Los clics usan URLs firmadas HMAC-SHA256: solo redirige a URLs generadas por el sistema |
| SSRF | El webhook y el consumidor SQS validan que el certificado SNS venga de hosts `*.amazonaws.com` por HTTPS; las URLs de imágenes/botones requieren host público y bloquean `localhost`/`.local` |
| Suplantación de eventos SES | Verificación criptográfica obligatoria de la firma SNS (RSA-SHA1 con certificado oficial) tanto en el webhook como en la cola SQS, lista blanca opcional de TopicArn, cola SQS con política que solo acepta el tópico del proyecto |
| Endpoint público de eventos | **No existe por defecto**: los eventos llegan por SQS consumida saliente (long polling); sin puertos ni rutas expuestas a AWS |
| Reenvíos duplicados | Unicidad (campaña, contacto) en BD, comprobación de estado antes de enviar, estado `incierto` en vez de reenvío ciego tras interrupciones |
| Envío a no consentidos | Doble filtro (encolado + envío) por estado del contacto y supresión global; campañas promocionales bloqueadas si la dirección está suprimida |
| Uso no autorizado como spam relay | Aprobación administrativa obligatoria, sin envíos automáticos al desplegar, límites de velocidad y cuota, audit trail |
| Abuso del endpoint público | Tokens firmados y de uso único lógico, sin revelación de correo completo, idempotencia |
| Exposición de secretos | `.env` fuera del repositorio, `.gitignore` y `.dockerignore` configurados, credenciales IAM solo por entorno, logs con redacción automática de claves sensibles |
| Contenedores sobreprivilegiados | Usuarios sin privilegios (`hj`, uid 10001) en ambas imágenes, sin `--privileged`, límites de memoria por servicio |
| Enumeración de recursos internos | PostgreSQL y Redis sin puertos publicados; solo `frontend` y `api` (este último opcional y solo en localhost por defecto) |

## 2. Autenticación y autorización

- **Roles**: `admin` (todo: aprobación de campañas, usuarios, auditoría, supresión) y `operador` (contactos, plantillas, campañas sin aprobar/enviar). Verificación server-side en cada endpoint (`require_admin`).
- **MFA TOTP**: obligatorio configurable para administradores (`MFA_REQUIRED_FOR_ADMIN`). Flujo de login en dos pasos con token temporal de 5 min de un solo uso. Códigos de recuperación hasheados con Argon2id y de un solo uso.
- **Cambio de contraseña**: revoca todas las sesiones del usuario. El restablecimiento por administrador fuerza `must_change_password`.
- **Auditoría**: `audit_logs` registra login/fallos, altas y cambios de usuarios, aprobaciones, envíos, importaciones/exportaciones, supresiones y cambios de ajustes, con usuario, IP y detalle.

## 3. Transporte y despliegue

1. **Fase de puesta en marcha**: servicios vinculados a `127.0.0.1` (`BIND_IP=127.0.0.1`); administración por túnel SSH (`ssh -L 8085:127.0.0.1:8085`). El login **no** queda expuesto a Internet.
2. **Publicación**: detrás de proxy inverso con HTTPS (Caddy/Nginx + certificado) o VPN. Configurar entonces `PUBLIC_BASE_URL=https://…`, `API_BASE_URL=https://…` y `COOKIE_SECURE=true`.
3. **Eventos de SES (rebotes/quejas/entregas)**: llegan por SQS que el worker consume **salientemente** (long polling): no se publica ningún endpoint HTTP. La alternativa de webhook HTTP (token + firma) queda reservada para despliegues con HTTPS; su seguridad no depende del transporte, pero se recomienda HTTPS.

## 4. Gestión de secretos y credenciales

| Secreto | Dónde | Rotación |
|---|---|---|
| `SECRET_KEY` (firmas HMAC) | `.env` del proyecto | Con calendario propio; invalida tokens de baja/clic antiguos |
| Credenciales IAM SES | Recomendado: `/root/.aws` copiado por `deploy.sh` como secreto Docker de solo lectura (`secrets/aws/`, UID 10001, modo 400, fuera del repositorio). Alternativa: `.env` | Rotación periódica en IAM; usuario dedicado `ses-vps-leisureparts`; tras rotar, vuelve a ejecutar `sudo bash scripts/deploy.sh` (o actualiza `.env`) |
| `POSTGRES_PASSWORD`, claves BD | `.env` | Al rotarla, actualizar `DATABASE_URL` |
| `WEBHOOK_TOKEN` | `.env` y configuración del tópico SNS (solo modalidad webhook HTTPS) | Regenerable sin reenvíos (solo afecta al webhook) |

Reglas: nunca en el repositorio, nunca en logs (`logging_conf` redacta claves cuyo nombre casa con password/token/secret/key…), nunca en el frontend.

## 5. Cabeceras y políticas

La API añade: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` restrictivo y `Cache-Control: no-store`. CORS cerrado por defecto (mismo origen); si se separan dominios, listar orígenes exactos en `ALLOWED_ORIGINS`.

## 6. Datos personales y privacidad

- Correos enmascarados en páginas públicas (`m***@dominio`).
- Historial de consentimiento conservado como evidencia (fuente, fecha, texto).
- Exportación CSV auditada (acción `contactos_exportados`).
- Baja efectiva inmediata con supresión permanente.
- Los logs de la aplicación no contienen contenido de mensajes ni correos completos en errores (se truncan y enmascaran donde aplica).

## 7. Verificación automatizada

La suite (`backend/tests`, 80 pruebas) cubre: login y anti-enumeración, bloqueo por fuerza bruta, CSRF obligatorio, roles, Argon2id, MFA con recuperación, importación CSV (duplicados, inválidos, simulación), supresión, flujo completo de campaña con SES simulado, reintentos con backoff, errores permanentes, pausa/reanudación, cancelación, idempotencia, webhook SNS con **firma real RSA** y rechazo de firmas falsas, consumidor SQS (sobre firmado, eventos directos, duplicados, firma inválida retenida, mensajes venenosos, tópicos no autorizados), rebotes permanentes → supresión, quejas → supresión, aperturas/clics firmados, bajas públicas y one-click, y estadísticas.

Los scripts de despliegue se verifican con `bash -n` y pruebas funcionales: carga de `.env` **sin ejecutarlo** (rechaza líneas maliciosas e ignora variables protegidas del shell), validaciones con mensajes claros (`--check`), coherencia `DATABASE_URL`/`POSTGRES_PASSWORD` y preparación de secretos AWS.

## 8. Reporte de incidentes

Uso interno. Ante sospecha de compromiso: revocar sesiones (`POST /api/v1/auth/sessions/revoke`), rotar `SECRET_KEY` y credenciales IAM, revisar `audit_logs` y `ses_events`, y pausar el worker (`docker compose stop worker`) si se detectan envíos anómalos.
