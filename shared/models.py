from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import text as sa_text

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from pgvector.sqlalchemy import Vector

EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_user_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    chats: Mapped[list["Chat"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chats.id"), nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    chat: Mapped["Chat"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_chat_created", "chat_id", "created_at"),
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="url")  # url|zakon_rada|kmu|...
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # canonical source url
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    documents: Mapped[list["Document"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)

    # конкретная версия/представление (например print + edition), а не обязательно permanent source url
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source: Mapped["Source"] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_documents_source_fetched", "source_id", "fetched_at"),
        Index("ix_documents_url", "url"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)

    idx: Mapped[int] = mapped_column(Integer, nullable=False)  # последовательный индекс в документе

    # структурные поля (для нормальных цитат)
    path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)          # напр. "Розділ I / Стаття 6"
    heading: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # напр. "Стаття 6. ..."
    unit_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # section|chapter|article|point|preamble|tail|chunk
    unit_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)    # "6", "I", "1", etc.
    part: Mapped[int] = mapped_column(Integer, nullable=False, default=0)     # если единица слишком длинная и режется на части

    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBED_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "idx", name="uq_chunk_document_idx"),
        Index("ix_chunks_document_idx", "document_id", "idx"),
        Index("ix_chunks_unit_lookup", "document_id", "unit_type", "unit_id"),
    )


def ensure_extra_indexes(conn) -> None:
    # extensions (если volume старый — db_init мог не отработать)
    for ext in ("unaccent", "pg_trgm", "pgcrypto", "vector"):
        try:
            conn.execute(sa_text(f"CREATE EXTENSION IF NOT EXISTS {ext};"))
        except Exception:
            pass

    # FTS gin index (сырым SQL, без REGCONFIG-compile в SQLAlchemy)
    try:
        conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_fts "
                "ON chunks USING gin (to_tsvector('simple'::regconfig, unaccent(text)));"
            )
        )
    except Exception:
        pass

    # Vector HNSW index (может не поддерживаться на старом pgvector — поэтому try)
    try:
        conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
                "ON chunks USING hnsw (embedding vector_cosine_ops);"
            )
        )
    except Exception:
        pass
