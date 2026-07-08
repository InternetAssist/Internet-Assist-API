"""add companies and company_files tables

Revision ID: 97905672fb6b
Revises: f298e52b1957
Create Date: 2026-07-08 17:39:03.890798

"""
from alembic import op
import sqlalchemy as sa


revision = '97905672fb6b'
down_revision = 'f298e52b1957'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'companies',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_companies_name'), ['name'], unique=True)

    op.create_table(
        'company_files',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('company_id', sa.String(length=36), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('stored_file_id', sa.String(length=150), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('uploaded_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('company_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_company_files_company_id'), ['company_id'], unique=False)


def downgrade():
    with op.batch_alter_table('company_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_files_company_id'))
    op.drop_table('company_files')

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_companies_name'))
    op.drop_table('companies')
