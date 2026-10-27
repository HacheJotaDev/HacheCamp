"""import_jobs asíncronos

Revision ID: a1f2c3d4e5f6
Revises: 078f3d08bba2
Create Date: 2026-10-28 00:00:00.000000

Tablas de la importación asíncrona de contactos:
- import_jobs: trabajo + progreso persistente (fila ligera, actualizada por lote).
- import_job_data: carga útil prevalidada (1:1, puede pesar varios MB);
  separada para que las actualizaciones de progreso nunca la reescriban.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import app.core.db  # tipos personalizados GUID y UTCDateTime


revision = 'a1f2c3d4e5f6'
down_revision = '078f3d08bba2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('import_jobs',
    sa.Column('id', app.core.db.GUID(length=36), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('total_rows', sa.Integer(), nullable=False),
    sa.Column('invalid_rows', sa.Integer(), nullable=False),
    sa.Column('duplicates_in_file', sa.Integer(), nullable=False),
    sa.Column('encoding', sa.String(length=30), nullable=True),
    sa.Column('headers', sa.JSON(), nullable=True),
    sa.Column('rows_processed', sa.Integer(), nullable=False),
    sa.Column('creados', sa.Integer(), nullable=False),
    sa.Column('actualizados', sa.Integer(), nullable=False),
    sa.Column('errores', sa.Integer(), nullable=False),
    sa.Column('notices', sa.JSON(), nullable=True),
    sa.Column('error_samples', sa.JSON(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('consent_source', sa.String(length=60), nullable=True),
    sa.Column('consent_text', sa.Text(), nullable=True),
    sa.Column('list_ids', sa.JSON(), nullable=True),
    sa.Column('tag_ids', sa.JSON(), nullable=True),
    sa.Column('user_id', app.core.db.GUID(length=36), nullable=True),
    sa.Column('user_email', sa.String(length=255), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('started_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('finished_at', app.core.db.UTCDateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_import_jobs_status'), 'import_jobs', ['status'], unique=False)

    op.create_table('import_job_data',
    sa.Column('job_id', app.core.db.GUID(length=36), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['import_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('job_id')
    )


def downgrade() -> None:
    op.drop_table('import_job_data')
    op.drop_index(op.f('ix_import_jobs_status'), table_name='import_jobs')
    op.drop_table('import_jobs')
