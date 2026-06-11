"""Add Lichess challenges table

Revision ID: 004_add_lichess_challenges
Revises: 003_move_move_state_to_game
Create Date: 2026-02-02

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004_add_lichess_challenges"
down_revision = "003_move_move_state_to_game"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.create_table(
        "lichess_challenges",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("lichess_challenge_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("challenger_username", sa.String(length=255), nullable=True),
        sa.Column("challenger_title", sa.String(length=20), nullable=True),
        sa.Column("rated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("variant", sa.String(length=50), nullable=True),
        sa.Column("speed", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("time_control_type", sa.String(length=20), nullable=True),
        sa.Column("time_control_limit", sa.Integer(), nullable=True),
        sa.Column("time_control_increment", sa.Integer(), nullable=True),
        sa.Column("time_control_days", sa.Integer(), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], [f"{schema}.lichess_accounts.id"]),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")
    op.drop_table("lichess_challenges", schema=schema)
