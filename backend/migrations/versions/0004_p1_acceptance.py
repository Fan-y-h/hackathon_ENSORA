"""Store academic corrections and version-specific source evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0004_p1_acceptance"
down_revision = "0003_p1_version_filename"


def upgrade():
    op.add_column("document_versions", sa.Column("source_metadata", sa.JSON(), nullable=True))
    op.create_table("academic_overrides",
                    sa.Column("record_id", sa.String(), sa.ForeignKey("source_records.id"), primary_key=True),
                    sa.Column("fields", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("academic_overrides")
    op.drop_column("document_versions", "source_metadata")
