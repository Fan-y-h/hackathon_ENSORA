"""Add indexes for measured document and history access paths."""
from alembic import op
import sqlalchemy as sa

revision = "0005_p1_read_indexes"
down_revision = "0004_p1_acceptance"


def upgrade():
    op.create_index("ix_documents_active_version", "documents", ["active_version_id"])
    op.create_index("ix_documents_conn_active_version", "documents",
                    ["connection_id", "active_version_id"])
    # Match SQLAlchemy's SQLite JSON path rendering in GET /documents?subject_id=.
    op.create_index(
        "ix_documents_conn_subject",
        "documents",
        ["connection_id", sa.text("json_extract(metadata_json, '$.\"subject_id\"')")],
    )
    op.create_index("ix_source_record_history_recorded", "source_record_history",
                    ["record_id", "recorded_at"])
    op.create_index("ix_availability_user", "availability", ["user_id"])


def downgrade():
    op.drop_index("ix_availability_user", table_name="availability")
    op.drop_index("ix_source_record_history_recorded", table_name="source_record_history")
    op.drop_index("ix_documents_conn_subject", table_name="documents")
    op.drop_index("ix_documents_conn_active_version", table_name="documents")
    op.drop_index("ix_documents_active_version", table_name="documents")
