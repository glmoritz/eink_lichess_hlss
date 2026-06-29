"""Add pressed-strip columns to Frame

Stores the optional pressed-state top and bottom button strips alongside
each rendered frame. The device fetches them through the LLSS strip cache
and uses them to render local press feedback that matches the HLSS look.

Revision ID: 006_add_frame_pressed_strips
Revises: 005_add_local_backend_fields
Create Date: 2026-06-29

"""

import sqlalchemy as sa

from alembic import op


revision = "006_add_frame_pressed_strips"
down_revision = "005_add_local_backend_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.add_column(
        "frames",
        sa.Column("top_pressed_data", sa.LargeBinary(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "frames",
        sa.Column("bottom_pressed_data", sa.LargeBinary(), nullable=True),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts.get("schema", "lichess")

    op.drop_column("frames", "bottom_pressed_data", schema=schema)
    op.drop_column("frames", "top_pressed_data", schema=schema)
