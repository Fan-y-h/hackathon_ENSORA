"""SQLAlchemy metadata and local database lifecycle."""
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Boolean, Column, ForeignKey, Index, Integer, MetaData, String, Table, UniqueConstraint, func
from sqlalchemy import create_engine, event, literal_column, select
from sqlalchemy.engine import URL

from .calendar import interval_time
from .drive_scope import folder_scope

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
                 Column("filename", String), Column("source_metadata", JSON),
                 UniqueConstraint("document_id", "sha256"))
chunks = table("chunks", reference("version_id", "document_versions"), Column("ordinal", Integer, nullable=False),
               Column("payload", JSON, nullable=False), UniqueConstraint("version_id", "ordinal"))
records = table("source_records", reference("connection_id", "connections"), Column("external_id", String, nullable=False),
                reference("parent_id", "source_records", True), Column("kind", String, nullable=False),
                Column("payload", JSON, nullable=False), UniqueConstraint("connection_id", "external_id"))
academic_overrides = Table("academic_overrides", metadata,
                           Column("record_id", String, ForeignKey("source_records.id"), primary_key=True),
                           Column("fields", JSON, nullable=False))
record_history = table("source_record_history", reference("record_id", "source_records"),
                       Column("payload", JSON, nullable=False), Column("recorded_at", String, nullable=False))
runs = table("sync_runs", reference("user_id", "users"), Column("status", String, nullable=False),
             Column("scope", JSON, nullable=False), Column("report", JSON, nullable=False),
             Column("started_at", String, nullable=False), Column("finished_at", String),
             Column("idempotency_key", String), UniqueConstraint("user_id", "idempotency_key"))
availability = table("availability", reference("user_id", "users"), Column("payload", JSON, nullable=False))

Index("ix_consents_connection_purpose", consents.c.connection_id, consents.c.purpose)
Index("ix_records_kind_connection", records.c.kind, records.c.connection_id)
Index("ix_records_parent", records.c.parent_id)
Index("ix_runs_user_started", runs.c.user_id, runs.c.started_at, runs.c.id)
Index("ix_runs_user_status", runs.c.user_id, runs.c.status)
Index("ix_documents_active_version", documents.c.active_version_id)
Index("ix_documents_conn_active_version", documents.c.connection_id, documents.c.active_version_id)
Index("ix_documents_conn_subject", documents.c.connection_id,
      func.json_extract(documents.c.metadata_json, '$."subject_id"'))
Index("ix_source_record_history_recorded", record_history.c.record_id, record_history.c.recorded_at)
Index("ix_availability_user", availability.c.user_id)

LOCAL_USER = "e0aa03f9-37fc-455b-97bb-648a35868d19"


def new_id():
    return str(uuid4())


def open_database(path: Path):
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"timeout": 20})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(conn, _):
        conn.create_function("ensora_interval_time", 3, interval_time, deterministic=True)
        conn.create_function("ensora_drive_folder_allowed", 1,
                             lambda folder: int(folder_scope(folder) == "allowed"), deterministic=True)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")

    return engine


def latest_consent_granted(connection_id, purpose: str):
    """Return the latest append-only consent value for a connection and purpose."""
    return (
        select(consents.c.granted)
        .where(consents.c.connection_id == connection_id, consents.c.purpose == purpose)
        .order_by(literal_column("consents.rowid").desc())
        .limit(1)
        .scalar_subquery()
    )


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
