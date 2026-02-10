from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select, text, literal_column
from sqlalchemy.orm import Session

from . import llm
from .models import Chunk, Document
from .utils import compact_quote, normalize_text


@dataclass
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    title: Optional[str]
    url: Optional[str]

    path: Optional[str]
    heading: Optional[str]
    unit_type: Optional[str]
    unit_id: Optional[str]

    text: str
    score: float


def _fts_rank_expr(query: str):
    q = normalize_text(query)
    return func.ts_rank_cd(
        func.to_tsvector(literal_column("'simple'"), func.unaccent(Chunk.text)),
        func.plainto_tsquery(literal_column("'simple'"), func.unaccent(q)),
    )


def retrieve(session: Session, query: str, k: int = 6) -> List[RetrievedChunk]:
    query = normalize_text(query)
    if not query:
        return []

    qvec = None
    try:
        qvec = llm.embed_text(query)
    except Exception:
        qvec = None

    candidates: dict[UUID, RetrievedChunk] = {}

    if qvec is not None:
        stmt_vec = (
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.text,
                Chunk.path,
                Chunk.heading,
                Chunk.unit_type,
                Chunk.unit_id,
                Chunk.embedding.cosine_distance(qvec).label("dist"),
                Document.title,
                Document.url,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.embedding.is_not(None))
            .order_by(text("dist ASC"))
            .limit(max(20, k * 4))
        )
        for cid, did, ctext, cpath, chead, utype, uid, dist, dtitle, durl in session.execute(stmt_vec):
            score = float(1.0 - dist)
            existing = candidates.get(cid)
            if existing is None or score > existing.score:
                candidates[cid] = RetrievedChunk(
                    chunk_id=cid,
                    document_id=did,
                    title=dtitle,
                    url=durl,
                    path=cpath,
                    heading=chead,
                    unit_type=utype,
                    unit_id=uid,
                    text=ctext,
                    score=score,
                )

    rank_expr = _fts_rank_expr(query).label("rank")
    stmt_fts = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.text,
            Chunk.path,
            Chunk.heading,
            Chunk.unit_type,
            Chunk.unit_id,
            rank_expr,
            Document.title,
            Document.url,
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(rank_expr > 0.0)
        .order_by(text("rank DESC"))
        .limit(max(20, k * 4))
    )
    for cid, did, ctext, cpath, chead, utype, uid, rank, dtitle, durl in session.execute(stmt_fts):
        score = float(rank)
        existing = candidates.get(cid)
        if existing is None or score > existing.score:
            candidates[cid] = RetrievedChunk(
                chunk_id=cid,
                document_id=did,
                title=dtitle,
                url=durl,
                path=cpath,
                heading=chead,
                unit_type=utype,
                unit_id=uid,
                text=ctext,
                score=score,
            )

    ranked = sorted(candidates.values(), key=lambda x: x.score, reverse=True)[:k]

    out: List[RetrievedChunk] = []
    for r in ranked:
        out.append(
            RetrievedChunk(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                title=r.title,
                url=r.url,
                path=r.path,
                heading=r.heading,
                unit_type=r.unit_type,
                unit_id=r.unit_id,
                text=compact_quote(r.text, 900),
                score=r.score,
            )
        )
    return out
