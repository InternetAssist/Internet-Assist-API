"""remove local users and roles -- azure ad is now the identity source

Revision ID: f298e52b1957
Revises: 2951b6b5d103
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f298e52b1957'
down_revision = '2951b6b5d103'
branch_labels = None
depends_on = None

# Columns that reference users.id but are unused everywhere in the app
# (no route ever reads or writes them -- confirmed by grep across app/ and
# the frontend). Dropped outright rather than just unlinked, since keeping a
# dead FK-shaped column around serves no purpose.
_DEAD_ASSIGNEE_COLUMNS = [
    ('contacts', 'assigned_to'),
    ('quotes', 'assigned_to'),
    ('job_applications', 'assigned_to'),
    ('chat_sessions', 'user_id'),
]


def _fk_name(inspector, table, column):
    for fk in inspector.get_foreign_keys(table):
        if fk.get('constrained_columns') == [column]:
            return fk['name']
    return None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # All of these FK names are database-assigned (no explicit name was set
    # when the tables were created), so they must be discovered rather than
    # hardcoded -- they differ per database instance.
    for table, column in _DEAD_ASSIGNEE_COLUMNS:
        fk_name = _fk_name(inspector, table, column)
        if fk_name:
            op.drop_constraint(fk_name, table, type_='foreignkey')
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column(column)

    fk_name = _fk_name(inspector, 'audit_logs', 'actor_user_id')
    if fk_name:
        op.drop_constraint(fk_name, 'audit_logs', type_='foreignkey')

    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.alter_column(
            'actor_user_id',
            existing_type=sa.String(length=36),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    op.drop_table('user_roles')

    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_roles_name'))
    op.drop_table('roles')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))
    op.drop_table('users')


def downgrade():
    op.create_table('users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('roles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_roles_name'), ['name'], unique=True)

    op.create_table('user_roles',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('role_id', sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'role_id'),
    )

    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.alter_column(
            'actor_user_id',
            existing_type=sa.String(length=255),
            type_=sa.String(length=36),
            existing_nullable=True,
        )
        batch_op.create_foreign_key(
            'fk_audit_logs_actor_user_id_users', 'users', ['actor_user_id'], ['id'], ondelete='SET NULL',
        )

    for table, column in _DEAD_ASSIGNEE_COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(column, sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                f'fk_{table}_{column}_users', 'users', [column], ['id'], ondelete='SET NULL',
            )
