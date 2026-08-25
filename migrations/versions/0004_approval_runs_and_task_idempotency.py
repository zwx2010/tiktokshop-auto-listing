"""Persist approval runs and task idempotency keys.

Revision ID: 0004_approval_runs_and_task_idempotency
Revises: 0003_listing_copy_audit
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0004_approval_runs"
down_revision = "0003_listing_copy_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        existing = set()
    else:
        inspector = sa.inspect(op.get_bind())
        existing = {column["name"] for column in inspector.get_columns("tasks")}
    if "idempotency_key" not in existing:
        op.add_column("tasks", sa.Column("idempotency_key", sa.String(128), nullable=True))
        op.create_unique_constraint("uq_task_idempotency_key", "tasks", ["idempotency_key"])

    if context.is_offline_mode() or "approval_runs" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "approval_runs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("decision", sa.String(32), nullable=False, server_default=""),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("card_data", sa.JSON(), nullable=False),
            sa.Column("upload_status", sa.String(16), nullable=False, server_default=""),
            sa.Column("upload_result", sa.JSON(), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("run_id", name="uq_approval_run_id"),
        )
    else:
        op.alter_column("approval_runs", "created_at", existing_type=sa.DateTime(),
                        nullable=False, existing_server_default=sa.func.now())
        op.alter_column("approval_runs", "updated_at", existing_type=sa.DateTime(),
                        nullable=False, existing_server_default=sa.func.now())


def downgrade() -> None:
    if context.is_offline_mode() or "approval_runs" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("approval_runs")
    if context.is_offline_mode():
        op.drop_constraint("uq_task_idempotency_key", "tasks", type_="unique")
        op.drop_column("tasks", "idempotency_key")
    else:
        inspector = sa.inspect(op.get_bind())
        if "idempotency_key" in {c["name"] for c in inspector.get_columns("tasks")}:
            op.drop_constraint("uq_task_idempotency_key", "tasks", type_="unique")
            op.drop_column("tasks", "idempotency_key")
