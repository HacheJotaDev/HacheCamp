# API de HJ Campaigns (v1)

Base: `/api/v1` · Autenticación por cookie de sesión (`hj_session`) · Mutaciones con cabecera `X-CSRF-Token` (valor entregado en `/auth/login` y `/auth/me`).

Errores: JSON `{"detail": "mensaje"}` con códigos HTTP convencionales (401 no autenticado, 403 sin permiso/CSRF, 404, 409 conflicto, 422 validación, 429 bloqueo por fuerza bruta, 502/503 problema de SES).

## Autenticación

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/login` | `{email, password}` → sesión + `csrf_token`, o `{mfa_required, mfa_token}` si hay TOTP |
| POST | `/auth/login/mfa` | `{mfa_token, code}` (código TOTP o de recuperación) → sesión |
| POST | `/auth/logout` | Cierra la sesión actual |
| GET | `/auth/me` | Usuario actual + `csrf_token` renovado |
| POST | `/auth/mfa/setup` | Genera secreto TOTP + `otpauth://` (paso 1) |
| POST | `/auth/mfa/enable` | `{secret, code}` → activa MFA y devuelve 8 códigos de recuperación (una sola vez) |
| POST | `/auth/mfa/disable` | Solo admin |
| POST | `/auth/password` | `{current_password, new_password}` → revoca sesiones |
| GET | `/auth/sessions` | Sesiones activas del usuario |
| POST | `/auth/sessions/revoke` | Revoca todas las sesiones del usuario |

## Usuarios (solo admin)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/users?q=` | Listar |
| POST | `/users` | Crear (`admin`/`operador`) |
| PATCH | `/users/{id}` | Nombre, rol, activo |
| POST | `/users/{id}/password` | Restablecer contraseña (fuerza cambio) |
| DELETE | `/users/{id}` | Desactivar |

## Contactos

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/contacts?q=&estado=&list_id=&tag_id=&page=&per_page=` | Búsqueda y filtros paginados |
| POST | `/contacts` | Alta/actualización con consentimiento `{email, first_name?, last_name?, consent_source, consent_text?, list_ids?, tag_ids?}` |
| GET | `/contacts/{id}` | Detalle (incluye listas y etiquetas) |
| PATCH | `/contacts/{id}` | Edición |
| DELETE | `/contacts/{id}` | Baja + supresión global |
| GET | `/contacts/{id}/consent` | Historial de consentimiento |
| POST | `/contacts/import` | multipart: `archivo`, `consent_source`, `consent_text?`, `list_ids?`, `tag_ids?`, `dry_run` → resumen (creados, actualizados, inválidos, duplicados) |
| POST | `/contacts/import/preview` | multipart: `archivo` → encabezados detectados y muestra |
| GET | `/contacts/export/csv` | Exportación auditada |

## Listas, etiquetas y segmentos

| Método | Ruta | Descripción |
|---|---|---|
| GET/POST | `/lists` · PATCH/DELETE `/lists/{id}` | Listas |
| GET/POST | `/tags` · DELETE `/tags/{id}` | Etiquetas |
| GET/POST | `/segments` · DELETE `/segments/{id}` | Segmentos con reglas `{campo, op: contiene|igual|empieza, valor}` |

## Supresión

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/suppression?q=&motivo=&page=` | Listado con totales por motivo |
| POST | `/suppression` | `{email, reason: rebote_permanente|queja|baja|manual, detail?}` |
| DELETE | `/suppression/{email}` | Quitar de la supresión |

## Plantillas

| Método | Ruta | Descripción |
|---|---|---|
| GET/POST | `/templates` | Listar / crear (`mode: bloques|html`) |
| GET/PUT/DELETE | `/templates/{id}` | Detalle / actualizar (crea versión) / eliminar |
| GET | `/templates/{id}/versions` | Historial |
| POST | `/templates/{id}/duplicate` | Duplicar |
| POST | `/templates/{id}/restore/{version}` | Restaurar versión |
| GET | `/templates/variables` | Catálogo de variables |
| POST | `/templates/preview` | `{mode, blocks?, html?}` → HTML sanitizado (para iframe aislado) |
| POST | `/templates/test-send` | `{to_email, from_email, subject, mode, blocks?/html?}` → envío real de prueba |

## Campañas

| Método | Ruta | Descripción |
|---|---|---|
| GET/POST | `/campaigns` | Listar (con `por_estado`) / crear (valida lista o segmento) |
| GET/PUT | `/campaigns/{id}` | Detalle (incluye `progreso`) / editar (solo estados editables) |
| POST | `/campaigns/{id}/duplicate` | Duplicar |
| POST | `/campaigns/{id}/submit-review` | Borrador → en revisión |
| POST | `/campaigns/{id}/approve` | **Solo admin**: firma la aprobación |
| POST | `/campaigns/{id}/schedule` | `{scheduled_at, timezone}` (requiere aprobación) |
| POST | `/campaigns/{id}/send-now` | Encola el envío (requiere aprobación) |
| POST | `/campaigns/{id}/pause` `/resume` `/cancel` | Control en curso |
| GET | `/campaigns/{id}/recipients-estimate` | Nº de destinatarios válidos |
| GET | `/campaigns/{id}/messages` | Mensajes por destinatario (paginado, filtro `estado`) |
| GET | `/campaigns/{id}/events` | Eventos de la campaña |
| GET | `/campaigns/{id}/history` | Historial de acciones |
| GET | `/campaigns/{id}/plain-text` | Versión de texto plano |
| POST | `/campaigns/{id}/test-send` | `{to_email}` → prueba real de esta campaña |

## Estadísticas y sistema

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/stats/dashboard` | Resumen real + cuota SES + nota sobre aperturas |
| GET | `/stats/series?dias=30` | Evolución diaria (7–90 días) |
| GET | `/system/healthz` | Salud (público): api, db, redis, ses |
| GET | `/system/ses/status` | Región, cuota real, identidades verificadas |
| GET | `/system/audit` | Auditoría (solo admin, paginada) |
| GET/PUT | `/system/settings/{key}` | Ajustes editables (`default_from_name`, `default_from_email`, `default_reply_to`) |
| GET | `/system/mail-config` | Remitente y asunto predeterminados (lectura: cualquier usuario autenticado) |
| PUT | `/system/mail-config` | Guardar remitente y asunto predeterminados (solo admin; dominio exclusivo `leisureparts4u.store`, anti inyección de cabeceras) |

## Endpoints públicos (sin sesión, con protecciones propias)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/t/open/{token}.gif` | Píxel de apertura (token HMAC firmado) |
| GET | `/t/click/{token}` | Redirección de clic (URL firmada) |
| GET | `/public/unsubscribe/info?token=` | Datos para la página de baja (correo enmascarado) |
| POST | `/public/unsubscribe/confirm` | `{token}` → baja + supresión |
| POST | `/public/unsubscribe/one-click?token=` | RFC 8058 (idempotente) |
| POST | `/webhooks/sns?token=` | Receptor de eventos SES vía SNS con verificación de firma |

## Ejemplos (curl)

```bash
# Login
curl -c cookies.txt -X POST http://127.0.0.1:8086/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"tu@correo.com","password":"TuClaveLarga123"}'

# Crear contacto (con CSRF de la respuesta anterior)
curl -b cookies.txt -X POST http://127.0.0.1:8086/api/v1/contacts \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"email":"nuevo@dominio.com","consent_source":"formulario web"}'

# Importar CSV (simulación)
curl -b cookies.txt -X POST http://127.0.0.1:8086/api/v1/contacts/import \
  -H "X-CSRF-Token: $CSRF" \
  -F archivo=@contactos.csv -F consent_source="formulario web" -F dry_run=true
```
