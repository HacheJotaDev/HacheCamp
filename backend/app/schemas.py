"""Esquemas Pydantic de la API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

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


class CSVImportRequest(BaseModel):
    consent_source: Optional[str] = Field(default=None, max_length=60, description="Origen del consentimiento (opcional)")
    consent_text: Optional[str] = Field(default=None, max_length=2000)
    list_ids: List[str] = []
    tag_ids: List[str] = []
    dry_run: bool = False


class ImportResult(BaseModel):
    total_filas: int
    filas_validas: int
    filas_invalidas: int
    creados: int
    actualizados: int
    duplicados_en_archivo: int
    notices: Dict[str, int]
    simulacion: bool
    codificacion: str


class ListCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = None


class ListOut(ORMModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime


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
    to_email: str
    from_email: str
    from_name: str = "HJ Campaigns"
    reply_to: Optional[str] = None
    subject: str = "[Prueba] HJ Campaigns"
    mode: str = Field(default="bloques", pattern="^(bloques|html)$")
    blocks: Optional[List[Dict[str, Any]]] = None
    html: Optional[str] = None

    @field_validator("to_email", "from_email", "reply_to")
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
    subject: str = Field(min_length=1, max_length=255)
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


TokenResponse.model_rebuild()
ContactDetailOut.model_rebuild()
