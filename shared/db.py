from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import text as sa_text
from .models import Base, ensure_extra_indexes

from .settings import get_settings

settings = get_settings()

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
    with engine.begin() as conn:
        for ext in ("unaccent", "pg_trgm", "pgcrypto", "vector"):
            conn.execute(sa_text(f"CREATE EXTENSION IF NOT EXISTS {ext};"))

        Base.metadata.create_all(bind=conn)
        ensure_extra_indexes(conn)

