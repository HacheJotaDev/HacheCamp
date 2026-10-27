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
      detail = typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((d: { msg?: string }) => d.msg).join("; ")
          : JSON.stringify(data.detail ?? data);
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
  post: <T,>(path: string, json?: unknown) => request<T>("POST", path, { json }),
  postForm: <T,>(path: string, formData: FormData) =>
    request<T>("POST", path, { formData }),
  put: <T,>(path: string, json?: unknown) => request<T>("PUT", path, { json }),
  patch: <T,>(path: string, json?: unknown) => request<T>("PATCH", path, { json }),
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
  tags?: Tag[];
  lists?: ContactList[];
}

export interface ContactList {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface Tag {
  id: string;
  name: string;
  color: string;
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
    success_rate: number | null;
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
  };
  nota_aperturas: string;
}
