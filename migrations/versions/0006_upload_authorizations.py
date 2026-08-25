"""trusted robot upload authorizations"""
from alembic import op
import sqlalchemy as sa

revision = "0006_upload_authorizations"
down_revision = "0005_approval_audits"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "upload_authorizations" not in inspector.get_table_names():
        op.create_table(
            "upload_authorizations",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("batch_id", sa.String(length=64), nullable=False),
            sa.Column("command", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="feishu_robot"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id"),
        )


def downgrade():
    op.drop_table("upload_authorizations")
