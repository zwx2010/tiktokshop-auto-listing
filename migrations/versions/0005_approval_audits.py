"""append-only approval callback audit records"""
from alembic import op
import sqlalchemy as sa

revision = "0005_approval_audits"
down_revision = "0004_approval_runs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "approval_audits" not in inspector.get_table_names():
        op.create_table(
            "approval_audits",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("event", sa.String(length=32), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("result", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("context", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_approval_audits_run_id", "approval_audits", ["run_id"])


def downgrade():
    op.drop_index("ix_approval_audits_run_id", table_name="approval_audits")
    op.drop_table("approval_audits")
