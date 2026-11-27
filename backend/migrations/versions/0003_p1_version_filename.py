"""Store the display filename alongside versioned document media."""
from alembic import op
import sqlalchemy as sa

revision = "0003_p1_version_filename"
down_revision = "0002_p1_integrity"


def upgrade():
    op.add_column("document_versions", sa.Column("filename", sa.String(), nullable=True))


def downgrade():
    op.drop_column("document_versions", "filename")
