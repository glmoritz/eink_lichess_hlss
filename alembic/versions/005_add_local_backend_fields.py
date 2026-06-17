"""Add local-backend fields (pluggable GameBackend)

Adds the discriminators that let a game/account be driven by the local engine
(python-chess + Stockfish) instead of Lichess, plus the linkage for local
human-vs-human matches.

Revision ID: 005_add_local_backend_fields
Revises: 004_add_lichess_challenges
Create Date: 2026-06-14

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "005_add_local_backend_fields"
down_revision = "004_add_lichess_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    # Accounts: a local account has no api_token; backend selects the integration.
    op.alter_column(
        "lichess_accounts",
        "api_token",
        existing_type=sa.Text(),
        nullable=True,
        schema=schema,
    )
    op.add_column(
        "lichess_accounts",
        sa.Column(
            "backend",
            sa.String(length=16),
            nullable=False,
            server_default="lichess",
        ),
        schema=schema,
    )

    # Games: backend selector + local human-vs-human match linkage.
    op.add_column(
        "games",
        sa.Column(
            "backend",
            sa.String(length=16),
            nullable=False,
            server_default="lichess",
        ),
        schema=schema,
    )
    op.add_column(
        "games",
        sa.Column("match_id", sa.String(length=36), nullable=True),
        schema=schema,
    )
    op.add_column(
        "games",
        sa.Column("instance_id", sa.String(length=36), nullable=True),
        schema=schema,
    )
    # Local games store a synthetic "local-<uuid>" id; widen the column.
    op.alter_column(
        "games",
        "lichess_game_id",
        existing_type=sa.String(length=20),
        type_=sa.String(length=64),
        existing_nullable=False,
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.alter_column(
        "games",
        "lichess_game_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=20),
        existing_nullable=False,
        schema=schema,
    )
    op.drop_column("games", "instance_id", schema=schema)
    op.drop_column("games", "match_id", schema=schema)
    op.drop_column("games", "backend", schema=schema)

    op.drop_column("lichess_accounts", "backend", schema=schema)
    op.alter_column(
        "lichess_accounts",
        "api_token",
        existing_type=sa.Text(),
        nullable=False,
        schema=schema,
    )
