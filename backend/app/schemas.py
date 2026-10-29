"""Esquemas Pydantic de la API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.contacts import is_valid_email, normalize_email


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Auth y usuarios
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_email(v)


class MFAVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=10)


class MFATokenResponse(BaseModel):
    mfa_required: bool = True
    mfa_token: str


class TokenResponse(BaseModel):
    csrf_token: str
    user: "UserOut"


class MFASetupResponse(BaseModel):
    secret: str
    otpauth_url: str


class MFAEnableRequest(BaseModel):
    secret: str
    code: str = Field(min_length=6, max_length=10)


class MFARecoveryRequest(BaseModel):
    code: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=200)


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


class UserCreate(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    role: str = Field(default="operador", pattern="^(admin|operador)$")

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    role: Optional[str] = Field(default=None, pattern="^(admin|operador)$")
    is_active: Optional[bool] = None


class UserOut(ORMModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    totp_enabled: bool
    must_change_password: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class SessionOut(BaseModel):
    created_at: int
    last_seen: int
    ip: str


# --------------------------------------------------------------------------- #
# Contactos
# --------------------------------------------------------------------------- #


class ContactCreate(BaseModel):
    """Alta simplificada: el correo electrónico es el único dato obligatorio.

    Los campos de consentimiento quedan opcionales para mantener la
    compatibilidad con integraciones existentes; el registro interno usa
    la fuente del propio alta (manual/importacion_csv) sin inventar
    autorizaciones.
    """
    email: str
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    attributes: Optional[Dict[str, Any]] = None
    consent_source: Optional[str] = Field(default=None, max_length=60)
    consent_text: Optional[str] = Field(default=None, max_length=2000)
    list_ids: List[str] = []
    tag_ids: List[str] = []

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)

    @field_validator("consent_source")
    @classmethod
    def _norm_consent(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == "":
            return None
        return v.strip()[:60]


class ContactBulkDelete(BaseModel):
    """Eliminación masiva de contactos con confirmación explícita."""
    ids: List[str] = Field(min_length=1, max_length=1000)
    modo: str = Field(default="eliminar", pattern="^(eliminar|dar_baja)$")
    confirmar: bool


class ContactUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    attributes: Optional[Dict[str, Any]] = None
    list_ids: Optional[List[str]] = None
    tag_ids: Optional[List[str]] = None


class ContactOut(ORMModel):
    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    status: str
    consent_source: Optional[str] = None
    consent_at: Optional[datetime] = None
    unsubscribed_at: Optional[datetime] = None
    source: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None  # V5: papelera de contactos


class ContactDetailOut(ContactOut):
    consent_text: Optional[str] = None
    tags: List["TagOut"] = []
    lists: List["ListOut"] = []


class ConsentEventOut(ORMModel):
    id: str
    event: str
    source: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    created_at: datetime





class ImportResult(BaseModel):
    """Respuesta al crear una importación asíncrona (202)."""
    job_id: str
    estado: str
    total_filas: int
    filas_validas: int
    filas_invalidas: int
    duplicados_en_archivo: int
    codificacion: str


class ImportJobProgress(BaseModel):
    """Estado y progreso de un trabajo de importación."""
    job_id: str
    filename: str
    estado: str
    progreso_pct: int
    total_filas: int
    filas_procesadas: int
    filas_invalidas: int
    duplicados_en_archivo: int
    creados: int
    actualizados: int
    errores: int
    notices: Dict[str, int]
    error_samples: List[Dict[str, Any]]
    last_error: Optional[str] = None
    attempts: int
    cancel_requested: bool
    encoding: Optional[str] = None
    headers: Optional[List[str]] = None
    user_email: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ListCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = None


class ListOut(ORMModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    deleted_at: Optional[datetime] = None  # V5: papelera de listas
    contactos: Optional[int] = None        # V5: nº de contactos asociados


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#10b981", pattern="^#[0-9a-fA-F]{6}$")


class TagOut(ORMModel):
    id: str
    name: str
    color: str


class SegmentRule(BaseModel):
    campo: str = Field(max_length=80)
    op: str = Field(pattern="^(contiene|igual|empieza)$")
    valor: str = Field(max_length=200)


class SegmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = None
    rules: List[SegmentRule] = []


class SegmentOut(ORMModel):
    id: str
    name: str
    description: Optional[str] = None
    rules: List[Dict[str, Any]]
    created_at: datetime


class SuppressionCreate(BaseModel):
    email: str
    reason: str = Field(default="manual", pattern="^(rebote_permanente|queja|baja|manual)$")
    detail: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)


class SuppressionOut(ORMModel):
    id: str
    email: str
    reason: str
    detail: Optional[str] = None
    source: Optional[str] = None
    campaign_id: Optional[str] = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Plantillas
# --------------------------------------------------------------------------- #


class Block(BaseModel):
    type: str = Field(max_length=40)
    props: Dict[str, Any] = {}


class TemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: Optional[str] = None
    category: str = Field(default="newsletter", max_length=40)
    mode: str = Field(default="bloques", pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=160)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=40)
    mode: Optional[str] = Field(default=None, pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None


class TemplateOut(ORMModel):
    id: str
    name: str
    description: Optional[str] = None
    category: str
    mode: str
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None
    version: int
    is_system: bool
    created_at: datetime
    updated_at: datetime


class TemplateVersionOut(ORMModel):
    id: str
    version: int
    mode: str
    created_at: datetime
    created_by: Optional[str] = None


class PreviewRequest(BaseModel):
    mode: str = Field(pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None
    # Datos de muestra para previsualizar variables
    sample_email: str = "ejemplo@destinatario.com"
    sample_name: str = "María"


class SendTestRequest(BaseModel):
    """Solicitud de envío de prueba (plantillas).

    Remitente y asunto NO se aceptan: siempre se resuelven en el backend
    desde "Configuración de correo" (única fuente de verdad para todos los
    envíos). Si un cliente antiguo los envía, Pydantic los ignora. Solo el
    destinatario es editable e independiente.
    """
    to_email: str
    reply_to: Optional[str] = None
    mode: str = Field(default="bloques", pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None

    @field_validator("to_email", "reply_to")
    @classmethod
    def _norm_emails(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)


# --------------------------------------------------------------------------- #
# Campañas
# --------------------------------------------------------------------------- #


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    # Asunto opcional: si se omite, se usa el asunto predeterminado de
    # "Configuración de correo" (ver create_campaign).
    subject: Optional[str] = Field(default=None, max_length=255)
    # Remitente opcional: si se omite se usa el configurado por defecto
    from_name: Optional[str] = Field(default=None, max_length=120)
    from_email: Optional[str] = None
    reply_to: Optional[str] = None
    template_id: Optional[str] = None
    mode: str = Field(default="bloques", pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None
    category: str = Field(default="promocional", pattern="^(promocional|transaccional)$")
    list_id: Optional[str] = None
    segment_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("from_email", "reply_to")
    @classmethod
    def _norm_emails(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)

    @field_validator("subject")
    @classmethod
    def _subject_seguro(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if "\r" in v or "\n" in v:
            raise ValueError("El asunto no puede contener saltos de línea")
        return v or None

    @field_validator("from_name")
    @classmethod
    def _from_name_seguro(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if "\r" in v or "\n" in v:
            raise ValueError("El nombre del remitente no puede contener saltos de línea")
        return v or None


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=160)
    subject: Optional[str] = Field(default=None, min_length=1, max_length=255)
    from_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    from_email: Optional[str] = None
    reply_to: Optional[str] = None
    mode: Optional[str] = Field(default=None, pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None
    category: Optional[str] = Field(default=None, pattern="^(promocional|transaccional)$")
    list_id: Optional[str] = None
    segment_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    timezone: Optional[str] = Field(default=None, max_length=64)

    @field_validator("from_email", "reply_to")
    @classmethod
    def _norm_emails(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v == "":
            return None
        if not is_valid_email(v):
            raise ValueError("Correo inválido")
        return normalize_email(v)

    @field_validator("subject")
    @classmethod
    def _subject_seguro(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if "\r" in v or "\n" in v:
            raise ValueError("El asunto no puede contener saltos de línea")
        return v

    @field_validator("from_name")
    @classmethod
    def _from_name_seguro(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if "\r" in v or "\n" in v:
            raise ValueError("El nombre del remitente no puede contener saltos de línea")
        return v


class CampaignOut(ORMModel):
    id: str
    name: str
    subject: str
    from_name: str
    from_email: str
    reply_to: Optional[str] = None
    template_id: Optional[str] = None
    mode: str
    category: str
    list_id: Optional[str] = None
    segment_id: Optional[str] = None
    status: str
    scheduled_at: Optional[datetime] = None
    timezone: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    enqueued_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None  # V5: archivo
    deleted_at: Optional[datetime] = None   # V5: papelera


class CampaignDetailOut(CampaignOut):
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None
    plain_text: Optional[str] = None
    progreso: Optional[Dict[str, Any]] = None
    metricas: Optional[Dict[str, Any]] = None


class CampaignAction(BaseModel):
    scheduled_at: Optional[datetime] = None
    timezone: Optional[str] = None


class CampaignMessageOut(ORMModel):
    id: str
    email: str
    status: str
    attempts: int
    ses_message_id: Optional[str] = None
    last_error: Optional[str] = None
    sent_at: Optional[datetime] = None
    updated_at: datetime


class CampaignHistoryOut(ORMModel):
    id: str
    action: str
    user_email: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    created_at: datetime


class RecipientsEstimate(BaseModel):
    destinatarios: int


class RecipientsPreview(BaseModel):
    """Solicitud de conteo de destinatarios antes de crear la campaña."""
    list_id: Optional[str] = None
    segment_id: Optional[str] = None
    category: str = Field(default="promocional", pattern="^(promocional|transaccional)$")


# --------------------------------------------------------------------------- #
# Sistema
# --------------------------------------------------------------------------- #


class AuditOut(ORMModel):
    id: str
    user_email: Optional[str] = None
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    ip: Optional[str] = None
    created_at: datetime


class SettingUpdate(BaseModel):
    value: Dict[str, Any]


# --------------------------------------------------------------------------- #
# V5: operaciones masivas de contactos (papelera, vaciado global, purga)
# --------------------------------------------------------------------------- #


class BulkDeleteFiltered(BaseModel):
    """Eliminación lógica de contactos según los filtros del listado."""
    q: Optional[str] = Field(default=None, max_length=200)
    estado: Optional[str] = Field(default=None, max_length=20)
    list_id: Optional[str] = None
    tag_id: Optional[str] = None
    confirmar: bool = False


class VaciarTodoRequest(BaseModel):
    """Vaciado global de contactos activos (admin, doble confirmación)."""
    confirmar: bool = False
    # Debe ser EXACTAMENTE "VACIAR CONTACTOS" (frase de confirmación escrita)
    confirmar_texto: str = Field(default="", max_length=40)


class PurgarRequest(BaseModel):
    """Purga física irreversible de contactos YA eliminados lógicamente.

    Autorización separada: exige frase exacta y omite contactos con mensajes
    de campaña (sus estadísticas/eventos se preservan).
    suprimir=True añade además los correos purgados a la lista de supresión
    (evita reimportaciones accidentales; implica conservar el correo en la BD).
    """
    confirmar: bool = False
    confirmar_texto: str = Field(default="", max_length=40)
    antiguedad_minima_dias: int = Field(default=30, ge=0, le=3650)
    suprimir: bool = False


class BulkOperationOut(ORMModel):
    id: str
    tipo: str
    status: str
    total_objetivo: int
    procesados: int
    afectados: int
    omitidos: int
    progreso_pct: int = 0
    cancel_requested: bool
    last_error: Optional[str] = None
    user_email: Optional[str] = None
    criterios: Optional[Dict[str, Any]] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


TokenResponse.model_rebuild()
ContactDetailOut.model_rebuild()
