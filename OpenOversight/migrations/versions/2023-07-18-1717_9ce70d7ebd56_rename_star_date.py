"""rename 'star_date'

Revision ID: 9ce70d7ebd56
Revises: 93fc3e074dcc
Create Date: 2023-07-18 17:17:02.018209

"""

from alembic import op


revision = "9ce70d7ebd56"
down_revision = "93fc3e074dcc"


def upgrade():
    with op.batch_alter_table("assignments", schema=None) as batch_op:
        batch_op.drop_index("ix_assignments_star_date")
        batch_op.alter_column("star_date", nullable=True, new_column_name="start_date")
        batch_op.create_index("ix_assignments_start_date", ["start_date"], unique=False)


def downgrade():
    with op.batch_alter_table("assignments", schema=None) as batch_op:
        batch_op.drop_index("ix_assignments_start_date")
        batch_op.alter_column("start_date", nullable=True, new_column_name="star_date")
        batch_op.create_index("ix_assignments_star_date", ["star_date"], unique=False)
