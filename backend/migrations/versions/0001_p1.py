"""Initial P1 persistence schema."""
from alembic import op
from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint

revision = "0001_p1"
down_revision = None
metadata = MetaData()



def table(name, *columns):
    return Table(name, metadata, Column("id", String, primary_key=True), *columns)


def reference(name, target, nullable=False):
    return Column(name, String, ForeignKey(f"{target}.id"), nullable=nullable)


users = table("users", Column("profile", JSON, nullable=False), Column("preferences", JSON, nullable=False),
              Column("onboarded", Boolean, nullable=False))
connections = table("connections", reference("user_id", "users"), Column("source", String, nullable=False),
                    Column("status", String, nullable=False), Column("cursor", String),
                    Column("last_success_at", String), UniqueConstraint("user_id", "source"))
consents = table("consents", reference("connection_id", "connections"), Column("purpose", String, nullable=False),
                 Column("granted", Boolean, nullable=False), Column("recorded_at", String, nullable=False))
documents = table("documents", reference("connection_id", "connections"),
                  Column("external_id", String, nullable=False), Column("metadata_json", JSON, nullable=False),
                  Column("remote_revision", String), Column("active_version_id", String),
                  UniqueConstraint("connection_id", "external_id"))
versions = table("document_versions", reference("document_id", "documents"), Column("sha256", String, nullable=False),
                 Column("mime_type", String, nullable=False), Column("size_bytes", Integer, nullable=False),
                 Column("status", String, nullable=False), Column("parser_version", String),
                 UniqueConstraint("document_id", "sha256"))
chunks = table("chunks", reference("version_id", "document_versions"), Column("ordinal", Integer, nullable=False),
               Column("payload", JSON, nullable=False), UniqueConstraint("version_id", "ordinal"))
records = table("source_records", reference("connection_id", "connections"), Column("external_id", String, nullable=False),
                reference("parent_id", "source_records", True), Column("kind", String, nullable=False),
                Column("payload", JSON, nullable=False), UniqueConstraint("connection_id", "external_id"))
record_history = table("source_record_history", reference("record_id", "source_records"),
                       Column("payload", JSON, nullable=False), Column("recorded_at", String, nullable=False))
runs = table("sync_runs", reference("user_id", "users"), Column("status", String, nullable=False),
             Column("scope", JSON, nullable=False), Column("report", JSON, nullable=False),
             Column("started_at", String, nullable=False), Column("finished_at", String),
             Column("idempotency_key", String), UniqueConstraint("user_id", "idempotency_key"))
availability = table("availability", reference("user_id", "users"), Column("payload", JSON, nullable=False))


def upgrade():
    metadata.create_all(op.get_bind())

def downgrade():
    metadata.drop_all(op.get_bind())
