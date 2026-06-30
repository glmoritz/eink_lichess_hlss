"""Add full_refresh hint to Frame

Server-side hint asking the device to drive a full e-ink refresh
(UI_CTX_SWITCH) on a frame instead of the partial default. HLSS sets
it for view-mode toggles and frames where the board position actually
changed; everything else stays partial.

Revision ID: 008_add_frame_full_refresh
Revises: 007_add_frame_enabled_mask
Create Date: 2026-06-30

"""

import sqlalchemy as sa

from alembic import op


revision = "008_add_frame_full_refresh"
down_revision = "007_add_frame_enabled_mask"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "frames",
        sa.Column(
            "full_refresh",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.drop_column("frames", "full_refresh", schema=schema)
