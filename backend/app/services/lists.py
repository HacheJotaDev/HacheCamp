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
from app.models import Campaign, ContactList, contact_lists_rel

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


def nombre_en_uso(db: Session, name: str, *, exclude_id: str | None = None) -> ContactList | None:
    """Lista (ACTIVA o en papelera) que ya reserva ese nombre.

    El nombre tiene restricción UNIQUE en BD (contact_lists.name): tanto las
    listas activas como las de la papelera reservan el nombre, así que crear,
    renombrar o restaurar debe comprobar AMBOS poblados. Devuelve la lista
    conflictiva o None.
    """
    query = db.query(ContactList).filter(ContactList.name == name)
    if exclude_id:
        query = query.filter(ContactList.id != exclude_id)
    return query.first()


def soft_delete_list(db: Session, lst, *, user_id=None, user_email=None) -> None:
    if lst.deleted_at is not None:
        raise ListError("La lista ya está en la papelera")
    _proteger(db, lst.id, "eliminar")
    lst.deleted_at = utcnow()
    lst.deleted_by = user_id


def restore_list(db: Session, lst, *, nuevo_nombre: str | None = None) -> str:
    """Restaura una lista desde la papelera gestionando la COLISIÓN de nombres.

    Entre el borrado y la restauración el nombre puede haber quedado
    reservado (p. ej. otra lista renombrada con ese nombre; el nombre es
    UNIQUE en BD). Reglas:
    - Sin colisión: se restaura con su nombre original.
    - Con colisión y `nuevo_nombre`: se valida (activo y papelera, excluyendo
      esta misma fila) y se restaura RENOMBRADA (nunca se pierde la lista ni
      su población de asociaciones).
    - Con colisión y sin `nuevo_nombre`: ListError con mensaje accionable
      (el API responde 409 y el frontend ofrece renombrar en el diálogo).
    Devuelve el nombre definitivo con el que queda la lista.
    """
    if lst.deleted_at is None:
        raise ListError("La lista no está en la papelera")

    colision = nombre_en_uso(db, lst.name, exclude_id=lst.id)
    if colision is None:
        lst.deleted_at = None
        lst.deleted_by = None
        return lst.name

    # Colisión: obligatorio un nombre alternativo válido
    candidato = (nuevo_nombre or "").strip()
    if not candidato:
        raise ListError(
            f"No se puede restaurar con el nombre «{lst.name}»: otra lista lo "
            "está usando ya. Indica un nombre nuevo para restaurarla."
        )
    if candidato == lst.name:
        raise ListError("El nombre indicado es el mismo que tiene la lista en la papelera")
    ocupado = nombre_en_uso(db, candidato, exclude_id=lst.id)
    if ocupado is not None:
        estado = "en la papelera" if ocupado.deleted_at is not None else "activa"
        raise ListError(
            f"Ya existe una lista {estado} con el nombre «{candidato}»"
        )
    lst.name = candidato
    lst.deleted_at = None
    lst.deleted_by = None
    return lst.name


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
