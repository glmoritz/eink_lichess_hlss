"""Add LLSS integration fields to instances table

Revision ID: 001_add_instance_llss_fields
Revises: 
Create Date: 2026-01-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_add_instance_llss_fields'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add LLSS callback URLs, display capabilities, and state fields to instances."""
    # Get the schema name from context (defaults to 'lichess')
    schema = op.get_context().opts.get('schema', 'lichess')
    
    # LLSS callback URLs
    op.add_column(
        'instances',
        sa.Column('callback_frames', sa.String(512), nullable=True),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('callback_inputs', sa.String(512), nullable=True),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('callback_notify', sa.String(512), nullable=True),
        schema=schema
    )
    
    # Display capabilities
    op.add_column(
        'instances',
        sa.Column('display_width', sa.Integer(), nullable=False, server_default='800'),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('display_height', sa.Integer(), nullable=False, server_default='480'),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('display_bit_depth', sa.Integer(), nullable=False, server_default='1'),
        schema=schema
    )
    
    # Instance state
    op.add_column(
        'instances',
        sa.Column('is_initialized', sa.Boolean(), nullable=False, server_default='false'),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('is_ready', sa.Boolean(), nullable=False, server_default='false'),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('needs_configuration', sa.Boolean(), nullable=False, server_default='true'),
        schema=schema
    )
    op.add_column(
        'instances',
        sa.Column('configuration_url', sa.String(512), nullable=True),
        schema=schema
    )
    
    # Linked account
    op.add_column(
        'instances',
        sa.Column('linked_account_id', sa.String(36), nullable=True),
        schema=schema
    )
    op.create_foreign_key(
        'fk_instances_linked_account',
        'instances',
        'lichess_accounts',
        ['linked_account_id'],
        ['id'],
        source_schema=schema,
        referent_schema=schema
    )
    
    # Last frame tracking
    op.add_column(
        'instances',
        sa.Column('last_frame_id', sa.String(36), nullable=True),
        schema=schema
    )


def downgrade() -> None:
    """Remove LLSS integration fields from instances."""
    schema = op.get_context().opts.get('schema', 'lichess')
    
    # Drop foreign key first
    op.drop_constraint('fk_instances_linked_account', 'instances', schema=schema, type_='foreignkey')
    
    # Drop columns
    op.drop_column('instances', 'last_frame_id', schema=schema)
    op.drop_column('instances', 'linked_account_id', schema=schema)
    op.drop_column('instances', 'configuration_url', schema=schema)
    op.drop_column('instances', 'needs_configuration', schema=schema)
    op.drop_column('instances', 'is_ready', schema=schema)
    op.drop_column('instances', 'is_initialized', schema=schema)
    op.drop_column('instances', 'display_bit_depth', schema=schema)
    op.drop_column('instances', 'display_height', schema=schema)
    op.drop_column('instances', 'display_width', schema=schema)
    op.drop_column('instances', 'callback_notify', schema=schema)
    op.drop_column('instances', 'callback_inputs', schema=schema)
    op.drop_column('instances', 'callback_frames', schema=schema)
