"""replace password auth with microsoft sso

Revision ID: 2951b6b5d103
Revises: 8c371ae8f651
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '2951b6b5d103'
down_revision = '8c371ae8f651'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('ix_otp_tokens_session_hash', table_name='otp_tokens')
    op.drop_index('ix_otp_tokens_user_id', table_name='otp_tokens')
    op.drop_table('otp_tokens')

    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_token_hash'))
    op.drop_table('password_reset_tokens')

    op.drop_column('users', 'password_hash')


def downgrade():
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=False, server_default=''))

    op.create_table('password_reset_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('otp_tokens',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('session_hash', sa.String(64), nullable=False),
        sa.Column('otp_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_otp_tokens_session_hash', 'otp_tokens', ['session_hash'], unique=True)
    op.create_index('ix_otp_tokens_user_id', 'otp_tokens', ['user_id'], unique=False)
