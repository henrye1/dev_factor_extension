"""Database engine, session factory and schema creation."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import SCHEMA, Base


def make_engine(settings: Settings) -> Engine:
    url = settings.sqlalchemy_url
    if settings.is_sqlite:
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):            # SQLite ignores foreign keys unless asked
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine.execution_options(schema_translate_map={SCHEMA: None})
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    if settings.db_schema != SCHEMA:
        engine = engine.execution_options(schema_translate_map={SCHEMA: settings.db_schema})
    return engine


def init_db(engine: Engine, settings: Settings) -> None:
    """Create the schema and tables if they do not exist.

    On Postgres the tables are locked away from the Supabase Data API: row-level security
    is enabled with no policies, and the API roles get no rights on the schema.
    """
    if settings.is_sqlite:
        Base.metadata.create_all(engine)
        return
    schema = settings.db_schema
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            conn.execute(text(f'ALTER TABLE "{schema}"."{table.name}" ENABLE ROW LEVEL SECURITY'))
        conn.execute(text(f"""
            DO $$
            DECLARE r text;
            BEGIN
              FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                  EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', '{schema}', r);
                  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', '{schema}', r);
                END IF;
              END LOOP;
            END $$;
        """))


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
