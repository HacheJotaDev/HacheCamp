"""Modelos SQLAlchemy de HJ Campaigns.

Entidades: usuarios, auditoría, contactos (listas/etiquetas/segmentos),
supresión, plantillas (con versiones), campañas, mensajes de campaña,
eventos SES y ajustes de aplicación.

Estados clave:
- Contacto: suscripto | dado_baja | rebote | queja
- Mensaje: pendiente | enviando | aceptado | entregado | rebote_permanente |
  rebote_temporal | queja | error | cancelado | incierto
- Campaña: borrador | en_revision | programada | en_cola | enviando |
  pausada | completada | cancelada | fallida
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, GUID, UTCDateTime, new_id, utcnow

# ----------------------------------------------------------------------------- #
# Usuarios y auditoría
# ----------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="operador", nullable=False)  # admin|operador
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recovery_codes: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    user_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True, index=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


# ----------------------------------------------------------------------------- #
# Contactos
# ----------------------------------------------------------------------------- #


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    contacts: Mapped[List["Contact"]] = relationship(
        secondary="contact_lists_rel", back_populates="lists", viewonly=True
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(9), default="#10b981", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    attributes: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="suscripto", nullable=False, index=True)
    # Consentimiento
    consent_source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    consent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    consent_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    unsubscribe_campaign_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # manual|csv_import|api
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    lists: Mapped[List["ContactList"]] = relationship(
        secondary="contact_lists_rel", back_populates="contacts", viewonly=True
    )
    tags: Mapped[List["Tag"]] = relationship(
        secondary="contact_tags_rel", viewonly=True
    )
    consent_history: Mapped[List["ConsentEvent"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", order_by="ConsentEvent.created_at"
    )


class ConsentEvent(Base):
    """Historial de consentimiento y cambios de estado de suscripción."""
    __tablename__ = "consent_events"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)  # alta|consentimiento|baja|rebote|queja|reactivacion
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="consent_history")


# Tablas de asociación (muchos a muchos)
contact_lists_rel = Table(
    "contact_lists_rel",
    Base.metadata,
    Column("contact_id", GUID, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("list_id", GUID, ForeignKey("contact_lists.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", UTCDateTime, default=utcnow, nullable=False),
)

contact_tags_rel = Table(
    "contact_tags_rel",
    Base.metadata,
    Column("contact_id", GUID, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", GUID, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Segment(Base):
    """Segmento basado en reglas simples sobre contactos."""
    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rules: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    # regla: {"campo": "email|first_name|last_name|status|atributo:X", "op": "contiene|igual|empieza", "valor": "..."}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Suppression(Base):
    """Lista de supresión global: nunca se envía a estas direcciones."""
    __tablename__ = "suppressions"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)  # rebote_permanente|queja|baja|manual
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # sns|manual|baja
    campaign_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


# ----------------------------------------------------------------------------- #
# Plantillas
# ----------------------------------------------------------------------------- #


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(40), default="newsletter", nullable=False)
    # bloques | html
    mode: Mapped[str] = mapped_column(String(10), default="bloques", nullable=False)
    blocks: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # HTML fuente (mode=html)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    versions: Mapped[List["TemplateVersion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="TemplateVersion.version.desc()"
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    template_id: Mapped[str] = mapped_column(GUID, ForeignKey("templates.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), default="bloques", nullable=False)
    blocks: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    template: Mapped[Template] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)


# ----------------------------------------------------------------------------- #
# Campañas y envíos
# ----------------------------------------------------------------------------- #


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)           # nombre interno
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str] = mapped_column(String(120), nullable=False)
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(GUID, ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)
    # Copia congelada del contenido al crear la campaña (bloques o html)
    mode: Mapped[str] = mapped_column(String(10), default="bloques", nullable=False)
    blocks: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plain_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(20), default="promocional", nullable=False)  # promocional|transaccional
    list_id: Mapped[Optional[str]] = mapped_column(GUID, ForeignKey("contact_lists.id", ondelete="SET NULL"), nullable=True)
    segment_id: Mapped[Optional[str]] = mapped_column(GUID, ForeignKey("segments.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="borrador", nullable=False, index=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    enqueued_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    messages: Mapped[List["CampaignMessage"]] = relationship(back_populates="campaign")
    history: Mapped[List["CampaignHistory"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignHistory.created_at.desc()"
    )


class CampaignHistory(Base):
    """Historial de cambios y acciones de la campaña."""
    __tablename__ = "campaign_history"

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(GUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="history")


class CampaignMessage(Base):
    """Un mensaje por destinatario. Unicidad (campaña, contacto) = idempotencia."""
    __tablename__ = "campaign_messages"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_contact"),
        Index("ix_campaign_messages_status", "campaign_id", "status"),
    )

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(GUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False)
    contact_id: Mapped[str] = mapped_column(GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)  # copia al momento del envío
    status: Mapped[str] = mapped_column(String(30), default="pendiente", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ses_message_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="messages")


class SesEvent(Base):
    """Eventos de SES/SNS y de seguimiento (aperturas y clics)."""
    __tablename__ = "ses_events"
    __table_args__ = (
        UniqueConstraint("event_uid", name="uq_ses_event_uid"),
        Index("ix_ses_events_type_time", "type", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_id)
    event_uid: Mapped[str] = mapped_column(String(160), nullable=False)  # deduplicación
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    # tipos: send|delivery|bounce|complaint|reject|open|click|unsubscribe|envio_local
    campaign_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True, index=True)  # CampaignMessage.id
    ses_message_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # clics
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppSetting(Base):
    """Ajustes de aplicación clave-valor (JSON)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


# Estados válidos de campañas y transiciones permitidas
CAMPAIGN_STATES = [
    "borrador", "en_revision", "programada", "en_cola", "enviando",
    "pausada", "completada", "cancelada", "fallida",
]

CAMPAIGN_TRANSITIONS: Dict[str, List[str]] = {
    "borrador": ["en_revision", "programada", "en_cola", "cancelada"],
    "en_revision": ["borrador", "programada", "en_cola", "cancelada"],
    "programada": ["en_cola", "borrador", "cancelada"],
    "en_cola": ["enviando", "pausada", "fallida", "cancelada"],
    "enviando": ["pausada", "completada", "fallida", "cancelada"],
    "pausada": ["enviando", "en_cola", "cancelada"],
    "completada": [],
    "cancelada": [],
    "fallida": ["borrador"],
}

MESSAGE_STATUSES = [
    "pendiente", "enviando", "aceptado", "entregado", "rebote_permanente",
    "rebote_temporal", "queja", "error", "cancelado", "incierto",
]
