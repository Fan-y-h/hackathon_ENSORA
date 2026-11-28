from alembic import context
from sqlalchemy import create_engine

from backend.app.core.database import metadata, open_database


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        engine = create_engine(url)
    else:
        from backend.app.core.settings import Settings

        settings = Settings.load()
        settings.prepare()
        engine = open_database(settings.local_data_dir / "ensora.sqlite3")
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()
