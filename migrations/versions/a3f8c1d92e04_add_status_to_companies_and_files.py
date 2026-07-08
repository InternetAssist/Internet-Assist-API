"""add status to companies and company_files (soft delete)

Revision ID: a3f8c1d92e04
Revises: 97905672fb6b
Create Date: 2026-07-08 19:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a3f8c1d92e04'
down_revision = '97905672fb6b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='active'))
        batch_op.create_index(batch_op.f('ix_companies_status'), ['status'], unique=False)

    with op.batch_alter_table('company_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='active'))
        batch_op.create_index(batch_op.f('ix_company_files_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('company_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_files_status'))
        batch_op.drop_column('status')

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_companies_status'))
        batch_op.drop_column('status')
