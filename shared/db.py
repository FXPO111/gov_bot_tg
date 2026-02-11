from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, ensure_extra_indexes
from .settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

REQUIRED_TABLES = {"users", "chats", "messages", "conversation_turns", "audit_logs", "sources", "documents", "chunks"}

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
    # 1) Extensions + schema in AUTOCOMMIT, чтобы сбой по одному extension не "отравлял" DDL-транзакцию.
    with engine.connect() as conn:
        ac = conn.execution_options(isolation_level="AUTOCOMMIT")
        ac.execute(sa_text("CREATE SCHEMA IF NOT EXISTS public;"))
        ac.execute(sa_text("SET search_path TO public;"))
        for ext in ("unaccent", "pg_trgm", "pgcrypto", "vector"):
            try:
                ac.execute(sa_text(f"CREATE EXTENSION IF NOT EXISTS {ext};"))
            except Exception as exc:
                logger.warning("Skipping extension %s setup: %s", ext, exc)

    # 2) Таблицы — в нормальной транзакции.
    with engine.begin() as conn:
        conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS public;"))
        conn.execute(sa_text("SET search_path TO public;"))
        Base.metadata.create_all(bind=conn)

    # 3) Доп. индексы — отдельно (часто падают на комбинациях расширений/версий; не ломаем DDL).
    try:
        with engine.connect() as conn:
            ac = conn.execution_options(isolation_level="AUTOCOMMIT")
            ensure_extra_indexes(ac)
    except Exception as exc:
        logger.warning("ensure_extra_indexes failed (non-fatal): %s", exc)

    # 4) Проверка наличия всех таблиц в public, при необходимости — повтор create_all один раз.
    def _tables_in_public() -> set[str]:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names(schema="public"))

    existing_tables = _tables_in_public()
    missing_tables = REQUIRED_TABLES - existing_tables

    if missing_tables:
        logger.warning(
            "init_db first-pass table check missing: %s. Retrying create_all once.",
            ", ".join(sorted(missing_tables)),
        )
        with engine.begin() as conn:
            conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS public;"))
            conn.execute(sa_text("SET search_path TO public;"))
            Base.metadata.create_all(bind=conn)

        existing_tables = _tables_in_public()
        missing_tables = REQUIRED_TABLES - existing_tables

    if missing_tables:
        raise RuntimeError(
            "Database initialization incomplete. Missing tables in public schema: "
            + ", ".join(sorted(missing_tables))
            + f". DATABASE_URL={settings.database_url}"
        )
