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
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("tasks")}
    columns = {
        "owner": sa.Column("owner", sa.String(length=100), nullable=False, server_default=""),
        "lease_until": sa.Column("lease_until", sa.DateTime(), nullable=True),
        "started_at": sa.Column("started_at", sa.DateTime(), nullable=True),
        "finished_at": sa.Column("finished_at", sa.DateTime(), nullable=True),
    }
    for name, column in columns.items():
        if name not in existing:
            op.add_column("tasks", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("tasks")}
    for name in ("finished_at", "started_at", "lease_until", "owner"):
        if name in existing:
            op.drop_column("tasks", name)
