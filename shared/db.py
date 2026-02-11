from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, ensure_extra_indexes
from .settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

REQUIRED_TABLES = {"users", "chats", "messages", "sources", "documents", "chunks"}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Session:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    # Extension setup in AUTOCOMMIT so a failure doesn't poison the transactional DDL session.
    with engine.connect() as conn:
        ac_conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        ac_conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS public;"))
        ac_conn.execute(sa_text("SET search_path TO public;"))
        for ext in ("unaccent", "pg_trgm", "pgcrypto", "vector"):
            try:
                ac_conn.execute(sa_text(f"CREATE EXTENSION IF NOT EXISTS {ext};"))
            except Exception as exc:
                logger.warning("Skipping extension %s setup: %s", ext, exc)

    with engine.begin() as conn:
        # Force predictable schema for table creation.
        conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS public;"))
        conn.execute(sa_text("SET search_path TO public;"))

        Base.metadata.create_all(bind=conn)
        ensure_extra_indexes(conn)

        existing_tables = set(inspect(conn).get_table_names(schema="public"))
        missing_tables = REQUIRED_TABLES - existing_tables
        if missing_tables:
            logger.warning(
                "init_db first-pass table check missing: %s. Retrying create_all once.",
                ", ".join(sorted(missing_tables)),
            )
            Base.metadata.create_all(bind=engine)
            existing_tables = set(inspect(conn).get_table_names(schema="public"))
            missing_tables = REQUIRED_TABLES - existing_tables

        if missing_tables:
            raise RuntimeError(
                "Database initialization incomplete. Missing tables in public schema: "
                + ", ".join(sorted(missing_tables))
                + f". DATABASE_URL={settings.database_url}"
            )
