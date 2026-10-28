"""Gestión de contactos: normalización, validación, supresión e historial."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import ConsentEvent, Contact, Suppression, contact_lists_rel, contact_tags_rel

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

VALID_STATUSES = {"suscripto", "dado_baja", "rebote", "queja"}


def normalize_email(email: str) -> str:
    """Normaliza una dirección: minúsculas, sin espacios, sin punto final."""
    return (email or "").strip().lower().rstrip(".")


def is_valid_email(email: str) -> bool:
    e = normalize_email(email)
    if len(e) > 254 or len(e) < 6:
        return False
    return bool(EMAIL_RE.match(e))


def mask_email(email: str) -> str:
    """Enmascara un correo para páginas públicas: j***@dominio.com."""
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        return "***"
    visible = local[0] if local else "*"
    return f"{visible}***@{domain}"


def is_suppressed(db: Session, email: str) -> bool:
    return db.query(Suppression).filter(Suppression.email == normalize_email(email)).first() is not None


def emails_suprimidos(db: Session, emails: List[str]) -> set:
    """Correos (normalizados) presentes en la lista de supresión.

    UNA consulta para N correos: encolar 50k destinatarios hacía 1 SELECT por
    contacto (50k consultas solo de supresión); ahora es 1 consulta por trozo.
    """
    limpios = [normalize_email(e) for e in emails if e]
    if not limpios:
        return set()
    return {
        row[0]
        for row in db.query(Suppression.email).filter(Suppression.email.in_(limpios)).all()
    }


def can_send_promotional(db: Session, contact: Contact) -> Tuple[bool, str]:
    """Un envío promocional exige consentimiento válido y ausencia de supresión."""
    if contact.status != "suscripto":
        return False, f"estado={contact.status}"
    if is_suppressed(db, contact.email):
        return False, "en_supresion"
    return True, ""


def add_consent_event(
    db: Session,
    contact: Contact,
    event: str,
    *,
    source: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(ConsentEvent(
        contact_id=contact.id,
        event=event,
        source=source,
        detail=detail or {},
    ))


def upsert_contact(
    db: Session,
    email: str,
    *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    consent_source: Optional[str] = None,
    consent_at=None,
    consent_text: Optional[str] = None,
    source: str = "manual",
    list_ids: Optional[List[str]] = None,
    tag_ids: Optional[List[str]] = None,
) -> Tuple[Contact, bool, str]:
    """Crea o actualiza un contacto. Devuelve (contacto, creado, aviso).

    - Duplicados: si el correo ya existe, se actualizan datos y consentimiento.
    - Nunca reactiva un contacto dado de baja por rebote/queja/baja manual:
      si existe, su estado no cambia (aviso existente_estado_*).
    - Un correo en la lista de supresión sin contacto previo se crea como
      dado_baja (aviso correo_suprimido): jamás pasa a suscripto.
    """
    email = normalize_email(email)
    existing = db.query(Contact).filter(Contact.email == email).first()
    created = existing is None
    notice = ""

    if created:
        # Direcciones en la lista de supresión NUNCA se activan: entran como
        # dado_baja (mismo par estado+supresión que la baja manual). La
        # supresión sigue siendo la autoridad: la fila queda visible para
        # auditoría pero no es elegible para ninguna campaña.
        suprimido = is_suppressed(db, email)
        contact = Contact(
            id=None, email=email,
            first_name=first_name, last_name=last_name,
            attributes=attributes or {},
            status="dado_baja" if suprimido else "suscripto",
            consent_source=consent_source or source,
            consent_at=consent_at or utcnow(),
            consent_text=consent_text,
            source=source,
        )
        db.add(contact)
        db.flush()
        add_consent_event(db, contact, "alta", source=source,
                          detail={"consent_source": consent_source or source,
                                  **({"suprimido": True} if suprimido else {})})
        if suprimido:
            notice = "correo_suprimido"
        if consent_source:
            add_consent_event(db, contact, "consentimiento", source=consent_source,
                              detail={"texto": (consent_text or "")[:500]})
    else:
        contact = existing
        if first_name:
            contact.first_name = first_name
        if last_name:
            contact.last_name = last_name
        if attributes:
            merged = dict(contact.attributes or {})
            merged.update(attributes)
            contact.attributes = merged
        if consent_source:
            contact.consent_source = consent_source
            contact.consent_at = consent_at or contact.consent_at or utcnow()
            if consent_text:
                contact.consent_text = consent_text
            add_consent_event(db, contact, "consentimiento", source=consent_source,
                              detail={"texto": (consent_text or "")[:500]})
        if contact.status != "suscripto":
            notice = f"existente_estado_{contact.status}"

    if list_ids:
        for lid in list_ids:
            exists = db.execute(
                select(contact_lists_rel).where(
                    contact_lists_rel.c.contact_id == contact.id,
                    contact_lists_rel.c.list_id == lid,
                )
            ).first()
            if not exists:
                db.execute(contact_lists_rel.insert().values(contact_id=contact.id, list_id=lid))
    if tag_ids:
        for tid in tag_ids:
            exists = db.execute(
                select(contact_tags_rel).where(
                    contact_tags_rel.c.contact_id == contact.id,
                    contact_tags_rel.c.tag_id == tid,
                )
            ).first()
            if not exists:
                db.execute(contact_tags_rel.insert().values(contact_id=contact.id, tag_id=tid))

    db.flush()
    return contact, created, notice


def suppress_email(
    db: Session,
    email: str,
    reason: str,
    *,
    source: str = "manual",
    detail: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> Suppression:
    email = normalize_email(email)
    existing = db.query(Suppression).filter(Suppression.email == email).first()
    if existing:
        return existing
    supp = Suppression(
        email=email, reason=reason, source=source, detail=detail, campaign_id=campaign_id
    )
    db.add(supp)
    return supp


def unsuppress_email(db: Session, email: str) -> bool:
    email = normalize_email(email)
    row = db.query(Suppression).filter(Suppression.email == email).first()
    if row:
        db.delete(row)
        return True
    return False


def count_contacts(db: Session) -> Dict[str, int]:
    rows = db.query(Contact.status, func.count(Contact.id)).group_by(Contact.status).all()
    counts = {status: 0 for status in VALID_STATUSES}
    for status, total in rows:
        counts[status] = total
    counts["total"] = sum(counts.values())
    return counts
