"""V5: borrado lógico (papelera) y operaciones masivas de contactos

Revisión: c5d6e7f8a9b0
Anterior: b4c1d2e3f4a5 (índice de reconciliación)
Fecha: 2026-10-30

Migración ADITIVA (no toca datos existentes):
- campaigns:            deleted_at, deleted_by, archived_at (+ índices)
- contact_lists:        deleted_at, deleted_by (+ índice deleted_at)
- contacts:             deleted_at, deleted_by, deleted_by_operation (+ índices)
- bulk_operations:      tabla nueva (progreso persistente de eliminaciones
                        masivas por lotes, patrón import_jobs)

Compatibilidad con producción: todos los campos nuevos son NULLABLE, así que
el código V4 funciona sin cambios hasta el despliegue del código V5. El
rollback de código no exige downgrade de BD.

Ejecución (automática con el servicio 'migrate' de docker compose):
    alembic upgrade head
Reversión:
    alembic downgrade -1
"""
import sqlalchemy as sa
from alembic import op

revision = 'c5d6e7f8a9b0'
down_revision = 'b4c1d2e3f4a5'
branch_labels = None
depends_on = None


def _utc_datetime():
    # GUID/UTCDateTime viven en app.core.db; la importación se hace aquí para
    # no ejecutar código de la app en el arranque de Alembic innecesariamente.
    from app.core.db import GUID, UTCDateTime
    return GUID(), UTCDateTime()


def upgrade() -> None:
    guid, utc_dt = _utc_datetime()

    # --- campaigns ---------------------------------------------------------
    op.add_column('campaigns', sa.Column('deleted_at', utc_dt, nullable=True))
    op.add_column('campaigns', sa.Column('deleted_by', guid, nullable=True))
    op.add_column('campaigns', sa.Column('archived_at', utc_dt, nullable=True))
    op.create_index('ix_campaigns_deleted_at', 'campaigns', ['deleted_at'])
    op.create_index('ix_campaigns_archived_at', 'campaigns', ['archived_at'])

    # --- contact_lists -----------------------------------------------------
    op.add_column('contact_lists', sa.Column('deleted_at', utc_dt, nullable=True))
    op.add_column('contact_lists', sa.Column('deleted_by', guid, nullable=True))
    op.create_index('ix_contact_lists_deleted_at', 'contact_lists', ['deleted_at'])

    # --- contacts ----------------------------------------------------------
    op.add_column('contacts', sa.Column('deleted_at', utc_dt, nullable=True))
    op.add_column('contacts', sa.Column('deleted_by', guid, nullable=True))
    op.add_column('contacts', sa.Column('deleted_by_operation', guid, nullable=True))
    op.create_index('ix_contacts_deleted_at', 'contacts', ['deleted_at'])
    op.create_index('ix_contacts_deleted_by_operation', 'contacts', ['deleted_by_operation'])

    # --- bulk_operations ---------------------------------------------------
    op.create_table(
        'bulk_operations',
        sa.Column('id', guid, primary_key=True),
        sa.Column('tipo', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pendiente'),
        sa.Column('criterios', sa.JSON(), nullable=True),
        sa.Column('total_objetivo', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('procesados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('afectados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('omitidos', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('user_id', guid, nullable=True),
        sa.Column('user_email', sa.String(255), nullable=True),
        sa.Column('ip', sa.String(64), nullable=True),
        sa.Column('created_at', utc_dt, nullable=False),
        sa.Column('updated_at', utc_dt, nullable=False),
        sa.Column('started_at', utc_dt, nullable=True),
        sa.Column('finished_at', utc_dt, nullable=True),
    )
    op.create_index('ix_bulk_operations_tipo', 'bulk_operations', ['tipo'])
    op.create_index('ix_bulk_operations_status', 'bulk_operations', ['status'])


def downgrade() -> None:
    op.drop_index('ix_bulk_operations_status', table_name='bulk_operations')
    op.drop_index('ix_bulk_operations_tipo', table_name='bulk_operations')
    op.drop_table('bulk_operations')

    op.drop_index('ix_contacts_deleted_by_operation', table_name='contacts')
    op.drop_index('ix_contacts_deleted_at', table_name='contacts')
    op.drop_column('contacts', 'deleted_by_operation')
    op.drop_column('contacts', 'deleted_by')
    op.drop_column('contacts', 'deleted_at')

    op.drop_index('ix_contact_lists_deleted_at', table_name='contact_lists')
    op.drop_column('contact_lists', 'deleted_by')
    op.drop_column('contact_lists', 'deleted_at')

    op.drop_index('ix_campaigns_archived_at', table_name='campaigns')
    op.drop_index('ix_campaigns_deleted_at', table_name='campaigns')
    op.drop_column('campaigns', 'archived_at')
    op.drop_column('campaigns', 'deleted_by')
    op.drop_column('campaigns', 'deleted_at')
