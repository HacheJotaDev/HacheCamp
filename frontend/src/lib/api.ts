"use client";

/**
 * Cliente API de HJ Campaigns.
 * - Cookies de sesión (credentials: include implícito en mismo origen).
 * - Token CSRF inyectado en todas las mutaciones.
 * - Redirección a login ante 401.
 */

let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  method: string,
  path: string,
  options: {
    json?: unknown;
    formData?: FormData;
    raw?: Response;
    skipCsrf?: boolean;
  } = {}
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  if (!options.skipCsrf && method !== "GET" && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const res = await fetch(path, {
    method,
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.formData,
    credentials: "same-origin",
  });

  if (res.status === 401 && !path.includes("/auth/login") && !path.includes("/auth/me")) {
    onUnauthorized?.();
    throw new ApiError(401, "Sesión expirada");
  }
  if (!res.ok) {
    let detail = `Error ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") {
        detail = data.detail;
      } else if (Array.isArray(data.detail)) {
        // Errores de validación FastAPI (422): incluye el campo afectado
        // para que el mensaje sea comprensible (p. ej. "campo 'archivo': Field required").
        detail = data.detail
          .map((d: { loc?: unknown[]; msg?: string }) => {
            const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body") : [];
            const campo = loc.length ? `campo '${loc.join(".")}'` : "";
            return campo ? `${campo}: ${d.msg ?? "error"}` : (d.msg ?? "error");
          })
          .join("; ");
      } else {
        detail = JSON.stringify(data.detail ?? data);
      }
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return res.text() as unknown as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  // `json` exige un objeto plano: evita pasar por error un FormData
  // (que se serializaría como "{}" y provocaría "Field required" en la API).
  // Para archivos usa api.postForm(path, formData).
  post: <T,>(path: string, json?: Record<string, unknown>) => request<T>("POST", path, { json }),
  postForm: <T,>(path: string, formData: FormData) =>
    request<T>("POST", path, { formData }),
  put: <T,>(path: string, json?: Record<string, unknown>) => request<T>("PUT", path, { json }),
  patch: <T,>(path: string, json?: Record<string, unknown>) => request<T>("PATCH", path, { json }),
  delete: <T,>(path: string) => request<T>("DELETE", path),
};

// --------------------------------------------------------------------------- //
// Tipos compartidos
// --------------------------------------------------------------------------- //

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "operador";
  is_active: boolean;
  totp_enabled: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Contact {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  attributes: Record<string, unknown> | null;
  status: "suscripto" | "dado_baja" | "rebote" | "queja";
  consent_source: string | null;
  consent_at: string | null;
  consent_text?: string | null;
  unsubscribed_at: string | null;
  source: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  tags?: Tag[];
  lists?: ContactList[];
}

export interface ContactList {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  deleted_at: string | null;
  contactos?: number | null;
}

/** V5: operación masiva de contactos (papelera/vaciado/purga). */
export interface BulkOperation {
  id: string;
  tipo: string;
  status: "pendiente" | "procesando" | "completada" | "fallida" | "cancelada" | "cancelada_parcial";
  total_objetivo: number;
  procesados: number;
  afectados: number;
  omitidos: number;
  progreso_pct: number;
  cancel_requested: boolean;
  last_error: string | null;
  user_email: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Tag {
  id: string;
  name: string;
  color: string;
}

// Importación asíncrona de contactos
export interface ImportSubida {
  job_id: string;
  estado: string;
  total_filas: number;
  filas_validas: number;
  filas_invalidas: number;
  duplicados_en_archivo: number;
  codificacion: string;
}

export interface ImportJob {
  job_id: string;
  filename: string;
  estado: "pendiente" | "procesando" | "completado" | "fallido" | "cancelado";
  progreso_pct: number;
  total_filas: number;
  filas_procesadas: number;
  filas_invalidas: number;
  duplicados_en_archivo: number;
  creados: number;
  actualizados: number;
  errores: number;
  notices: Record<string, number>;
  error_samples: { line?: number; email: string; error: string }[];
  last_error: string | null;
  attempts: number;
  cancel_requested: boolean;
  encoding: string | null;
  headers: string[] | null;
  user_email: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Segment {
  id: string;
  name: string;
  description: string | null;
  rules: { campo: string; op: string; valor: string }[];
  created_at: string;
}

export interface ConsentEventOut {
  id: string;
  event: string;
  source: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface Suppression {
  id: string;
  email: string;
  reason: string;
  detail: string | null;
  source: string | null;
  created_at: string;
}

export interface Template {
  id: string;
  name: string;
  description: string | null;
  category: string;
  mode: "bloques" | "html";
  blocks: EditorBlock[] | null;
  html: string | null;
  version: number;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

export interface EditorBlock {
  type: string;
  props: Record<string, any>;
}

export interface TemplateVersion {
  id: string;
  template_id: string;
  version: number;
  mode: "bloques" | "html";
  blocks: EditorBlock[] | null;
  html: string | null;
  created_by: string | null;
  created_at: string;
}

export interface Campaign {
  id: string;
  name: string;
  subject: string;
  from_name: string;
  from_email: string;
  reply_to: string | null;
  template_id: string | null;
  mode: "bloques" | "html";
  blocks: EditorBlock[] | null;
  html: string | null;
  plain_text?: string | null;
  category: "promocional" | "transaccional";
  list_id: string | null;
  segment_id: string | null;
  status:
    | "borrador"
    | "en_revision"
    | "programada"
    | "en_cola"
    | "enviando"
    | "pausada"
    | "completada"
    | "cancelada"
    | "fallida";
  scheduled_at: string | null;
  timezone: string;
  approved_at: string | null;
  enqueued_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  deleted_at: string | null;
  progreso?: {
    total: number;
    procesados: number;
    pendientes: number;
    errores: number;
    por_estado: Record<string, number>;
  };
}

export interface CampaignMessage {
  id: string;
  email: string;
  status: string;
  attempts: number;
  ses_message_id: string | null;
  last_error: string | null;
  sent_at: string | null;
  updated_at: string;
}

export interface Paged<T> {
  total: number;
  page: number;
  per_page: number;
  items: T[];
  por_estado?: Record<string, number>;
}

/** Configuración de correo (remitente y asunto predeterminados). */
export interface MailConfig {
  from_name: string;
  from_email: string;
  subject: string;
  dominio: string;
  guardado: {
    default_from_name: string | null;
    default_from_email: string | null;
    default_subject: string | null;
  };
}

export interface DashboardData {
  resumen: Record<string, number>;
  metricas?: {
    total: number;
    aceptados_ses: number;
    entregados: number;
    pendientes: number;
    fallidos: number;
    rebotes: number;
    rebotes_temporales: number;
    quejas: number;
    rechazados?: number;
    retrasos?: number;
    incierto?: number;
    success_rate: number | null;
    entrega_rate?: number | null;
    en_vuelo?: number;
    bounce_rate: number | null;
    open_rate: number | null;
    click_rate: number | null;
    denominadores?: Record<string, string>;
  };
  cuota_ses: {
    max_24h?: number;
    sent_last_24h?: number;
    max_per_second?: number;
    region?: string;
    error?: string;
    mensaje?: string;
    disponible?: boolean;
    desactualizado?: boolean;
    edad_s?: number;
    cacheado?: boolean;
    actualizado?: number;
    ultima_consulta_iso?: string | null;
    objetivo_por_segundo?: number;
  };
  nota_aperturas: string;
}
