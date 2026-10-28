"""Índice de reconciliación y huérfanos en campaign_messages

Revisión: b4c1d2e3f4a5
Anterior: a1f2c3d4e5f6 (import_jobs asíncronos)
Fecha: 2026-10-29

Auditoría V4: la reconciliación de envíos estancados (reconcile_stale_sends)
y la recuperación de huérfanos (recover_orphaned_messages) filtran por
status + updated_at sobre campaign_messages. Sin índice, cada pasada del
scheduler escanea la tabla completa: con 50k+ mensajes la degradación crece
con el histórico. Migración ADITIVA: el código anterior funciona igual con el
índice presente (el rollback de código no exige downgrade de BD).

Ejecución (automática con el servicio 'migrate' de docker compose):
    alembic upgrade head
Reversión:
    alembic downgrade -1   # solo si se quisiera revertir también el índice
"""
from alembic import op

revision = 'b4c1d2e3f4a5'
down_revision = 'a1f2c3d4e5f6'
branch_labels = None
depends_on = None

NOMBRE = 'ix_campaign_messages_status_updated_at'


def upgrade() -> None:
    op.create_index(
        NOMBRE,
        'campaign_messages',
        ['status', 'updated_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(NOMBRE, table_name='campaign_messages')
