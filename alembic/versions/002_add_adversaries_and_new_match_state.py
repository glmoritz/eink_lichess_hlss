"""Add adversaries and new match state column

Revision ID: 002_add_adversaries_and_new_match_state
Revises: 001_add_instance_llss_fields
Create Date: 2026-01-19

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "002_add_adversaries_and_new_match_state"
down_revision = "001_add_instance_llss_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "instances",
        sa.Column("new_match_state", sa.Text(), nullable=True),
        schema=schema,
    )

    op.create_table(
        "adversaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("lichess_accounts.id"), nullable=False
        ),
        sa.Column("lichess_username", sa.String(255), nullable=False),
        sa.Column("friendly_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.drop_table("adversaries", schema=schema)
    op.drop_column("instances", "new_match_state", schema=schema)
