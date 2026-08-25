"""bind upload authorization to robot-created Bitable records"""
from alembic import op
import sqlalchemy as sa

revision = "0007_upload_auth_records"
down_revision = "0006_upload_authorizations"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("upload_authorizations")}
    if "record_ids" not in columns:
        op.add_column("upload_authorizations", sa.Column("record_ids", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("upload_authorizations", "record_ids")
