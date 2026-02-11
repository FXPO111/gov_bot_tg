from __future__ import annotations

from contextlib import contextmanager
import logging
from sqlalchemy import inspect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import text as sa_text
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
    # Run extension setup in AUTOCOMMIT mode so a failure in one extension
    # does not poison the transactional DDL session used for table creation.
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
        conn.execute(sa_text("SET search_path TO public;"))
        Base.metadata.create_all(bind=conn)

    # Extra index creation can fail on specific DB/extension combos; execute it
    # outside the metadata transaction so caught SQL errors do not abort table DDL.
    with engine.connect() as conn:
        ac_conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        ensure_extra_indexes(ac_conn)

    with engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names(schema="public"))
        missing_tables = REQUIRED_TABLES - existing_tables
        if missing_tables:
            logger.warning(
                "init_db first-pass table check missing: %s. Retrying create_all once.",
                ", ".join(sorted(missing_tables)),
            )
            with engine.begin() as retry_conn:
                retry_conn.execute(sa_text("SET search_path TO public;"))
                Base.metadata.create_all(bind=retry_conn)

            with engine.connect() as check_conn:
                existing_tables = set(inspect(check_conn).get_table_names(schema="public"))
            missing_tables = REQUIRED_TABLES - existing_tables

        if missing_tables:
            raise RuntimeError(
                "Database initialization incomplete. Missing tables in public schema: "
                + ", ".join(sorted(missing_tables))
            )

