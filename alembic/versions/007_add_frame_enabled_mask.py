"""Add per-band enabled-slot bitmask to Frame

Independent of the rendered strip image. Device uses it to gate local
press feedback so disabled / unpressable slots don't flash on touch.

Revision ID: 007_add_frame_enabled_mask
Revises: 006_add_frame_pressed_strips
Create Date: 2026-06-29

"""

import sqlalchemy as sa

from alembic import op


revision = "007_add_frame_enabled_mask"
down_revision = "006_add_frame_pressed_strips"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "frames",
        sa.Column("top_enabled_mask", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "frames",
        sa.Column("bottom_enabled_mask", sa.Integer(), nullable=True),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.drop_column("frames", "bottom_enabled_mask", schema=schema)
    op.drop_column("frames", "top_enabled_mask", schema=schema)
