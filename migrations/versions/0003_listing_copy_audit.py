"""Add auditable listing copy result fields.

Revision ID: 0003_listing_copy_audit
Revises: 0002_task_leases
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0003_listing_copy_audit"
down_revision = "0002_task_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        "copy_source": sa.Column("copy_source", sa.String(length=64), nullable=False, server_default=""),
        "copy_reason": sa.Column("copy_reason", sa.String(length=500), nullable=False, server_default=""),
        "copy_checked_at": sa.Column("copy_checked_at", sa.DateTime(), nullable=True),
    }
    if context.is_offline_mode():
        existing = set()
    else:
        inspector = sa.inspect(op.get_bind())
        existing = {column["name"] for column in inspector.get_columns("listings")}
    for name, column in columns.items():
        if name not in existing:
            op.add_column("listings", column)


def downgrade() -> None:
    if context.is_offline_mode():
        existing = {"copy_checked_at", "copy_reason", "copy_source"}
    else:
        inspector = sa.inspect(op.get_bind())
        existing = {column["name"] for column in inspector.get_columns("listings")}
    for name in ("copy_checked_at", "copy_reason", "copy_source"):
        if name in existing:
            op.drop_column("listings", name)
