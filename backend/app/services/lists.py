"""Operaciones de listas de contactos (V5): borrado lógico con papelera,
restauración y vaciado (solo asociaciones, sin tocar la base de contactos).

Reglas de protección:
- No se permite eliminar/vaciar una lista cuando campañas NO finalizadas la
  usan y su población destinataria aún no está materializada (borrador,
  en_revision, programada): cambiar la población de forma inesperada rompería
  la definición de esas campañas.
- Campañas ya en cola/enviando/pausada o finalizadas no se ven afectadas:
  sus mensajes ya existen (instantánea hecha al encolar) o ya terminaron.
- Vaciar una lista SOLO elimina las ASOCIACIONES (contact_lists_rel): los
  contactos globales y sus demás listas quedan intactos.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Campaign, Contact, contact_lists_rel

LOTE = 5000  # asociaciones por lote (commit corto: 50.656 filas sin bloquear)

# Estados de campaña cuya población destinataria depende de la lista y aún no
# se ha materializado en mensajes (encolar crea las filas campaign_messages).
ESTADOS_POBLACION_VIVA = ("borrador", "en_revision", "programada")


class ListError(ValueError):
    pass


def campanas_vivas_usando_lista(db: Session, list_id: str):
    """Campañas NO finalizadas (ni eliminadas) que apuntan a la lista."""
    return (
        db.query(Campaign)
        .filter(Campaign.list_id == list_id,
                Campaign.deleted_at.is_(None),
                Campaign.status.in_(ESTADOS_POBLACION_VIVA))
        .all()
    )


def _proteger(db: Session, list_id: str, accion: str) -> None:
    vivas = campanas_vivas_usando_lista(db, list_id)
    if vivas:
        nombres = ", ".join(f"«{c.name}» ({c.status})" for c in vivas[:3])
        raise ListError(
            f"No se puede {accion} la lista: las campañas {nombres} "
            "la usan y aún no han encolado sus destinatarios. "
            "Cancela o elimina esas campañas primero."
        )


def soft_delete_list(db: Session, lst, *, user_id=None, user_email=None) -> None:
    if lst.deleted_at is not None:
        raise ListError("La lista ya está en la papelera")
    _proteger(db, lst.id, "eliminar")
    lst.deleted_at = utcnow()
    lst.deleted_by = user_id


def restore_list(db: Session, lst) -> None:
    if lst.deleted_at is None:
        raise ListError("La lista no está en la papelera")
    # Si entre medias otra lista tomó el nombre (unique), no se puede restaurar
    # con el mismo nombre: el API lo informa para renombrar manualmente.
    lst.deleted_at = None
    lst.deleted_by = None


def empty_list(db: Session, lst, *, user_id=None, user_email=None) -> Dict[str, Any]:
    """Vacía la lista: elimina SOLO las asociaciones, por lotes.

    Los contactos globales NO se tocan (ni eliminados ni ocultos): si un
    contacto pertenece a otras listas, sigue perteneciendo a ellas.
    """
    _proteger(db, lst.id, "vaciar")
    total = (
        db.query(func.count(contact_lists_rel.c.contact_id))
        .filter(contact_lists_rel.c.list_id == lst.id)
        .scalar() or 0
    )
    quitadas = 0
    while True:
        ids = [
            row[0] for row in (
                db.query(contact_lists_rel.c.contact_id)
                .filter(contact_lists_rel.c.list_id == lst.id)
                .limit(LOTE)
                .all()
            )
        ]
        if not ids:
            break
        db.execute(
            contact_lists_rel.delete().where(
                contact_lists_rel.c.list_id == lst.id,
                contact_lists_rel.c.contact_id.in_(ids),
            )
        )
        db.commit()
        quitadas += len(ids)
    return {"asociaciones_eliminadas": quitadas, "contactos_antes": total}


def counts_por_lista(db: Session) -> Dict[str, int]:
    """Nº de contactos por lista en UNA consulta (GROUP BY), sin N+1."""
    rows = (
        db.query(contact_lists_rel.c.list_id, func.count(contact_lists_rel.c.contact_id))
        .group_by(contact_lists_rel.c.list_id)
        .all()
    )
    return {list_id: total for list_id, total in rows}
