"""Move move_state from instances to games

Revision ID: 003_move_move_state_to_game
Revises: 002_add_adversaries_and_new_match_state
Create Date: 2026-01-22

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "003_move_move_state_to_game"
down_revision = "002_add_adversaries_and_new_match_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "games",
        sa.Column("move_state", sa.Text(), nullable=True),
        schema=schema,
    )

    op.execute(
        f"""
        UPDATE {schema}.games
        SET move_state = inst.move_state
        FROM {schema}.instances AS inst
        WHERE games.id = inst.current_game_id AND inst.move_state IS NOT NULL
        """
    )

    op.drop_column("instances", "move_state", schema=schema)


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "instances",
        sa.Column("move_state", sa.Text(), nullable=True),
        schema=schema,
    )

    op.execute(
        f"""
        UPDATE {schema}.instances AS inst
        SET move_state = games.move_state
        FROM {schema}.games
        WHERE inst.current_game_id = games.id AND games.move_state IS NOT NULL
        """
    )

    op.drop_column("games", "move_state", schema=schema)
