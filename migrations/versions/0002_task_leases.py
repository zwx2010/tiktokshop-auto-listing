"""Add task lease and lifecycle columns.

Revision ID: 0002_task_leases
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_task_leases"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("owner", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("tasks", sa.Column("lease_until", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "finished_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "lease_until")
    op.drop_column("tasks", "owner")
