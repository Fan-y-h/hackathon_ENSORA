"""Index source queries and enforce document version ownership in SQLite."""
from alembic import op

revision = "0002_p1_integrity"
down_revision = "0001_p1"

INDEXES = {
    "ix_consents_connection_purpose": ("consents", ["connection_id", "purpose"]),
    "ix_records_kind_connection": ("source_records", ["kind", "connection_id"]),
    "ix_records_parent": ("source_records", ["parent_id"]),
    "ix_runs_user_started": ("sync_runs", ["user_id", "started_at", "id"]),
    "ix_runs_user_status": ("sync_runs", ["user_id", "status"]),
}


def upgrade():
    for name, (table, columns) in INDEXES.items():
        op.create_index(name, table, columns)
    # SQLite cannot add a circular composite FK without rebuilding existing documents.
    op.execute("""UPDATE documents SET active_version_id = NULL
        WHERE active_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM document_versions v
            WHERE v.id = documents.active_version_id AND v.document_id = documents.id
        )""")
    for action in ("INSERT", "UPDATE OF active_version_id, id"):
        suffix = action.split()[0].lower()
        op.execute(f"""CREATE TRIGGER documents_active_version_{suffix}
            BEFORE {action} ON documents
            WHEN NEW.active_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM document_versions WHERE id = NEW.active_version_id AND document_id = NEW.id
            )
            BEGIN SELECT RAISE(ABORT, 'Active version must belong to its document'); END""")
    for action in ("DELETE", "UPDATE OF id, document_id"):
        suffix = action.split()[0].lower()
        op.execute(f"""CREATE TRIGGER document_versions_active_{suffix}
            BEFORE {action} ON document_versions
            WHEN EXISTS (SELECT 1 FROM documents WHERE active_version_id = OLD.id)
            BEGIN SELECT RAISE(ABORT, 'Cannot remove or move an active document version'); END""")


def downgrade():
    for trigger in ("documents_active_version_insert", "documents_active_version_update",
                    "document_versions_active_delete", "document_versions_active_update"):
        op.execute(f"DROP TRIGGER {trigger}")
    for name, (table, _) in reversed(INDEXES.items()):
        op.drop_index(name, table_name=table)
