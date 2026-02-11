from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from shared.db import get_session, init_db
from shared.ingest import ingest_url
from shared.llm import answer_with_citations
from shared.models import Message
from shared.retrieval import retrieve
from shared.schemas import Citation
from shared.settings import get_settings

settings = get_settings()
_CIT_RE = re.compile(r"\[(\d{1,2})\]")
_NEED_MORE_RE = re.compile(r"(?im)^\s*need_more_info\s*=\s*(true|false)\s*$")


@shared_task(name="worker.tasks.init_db")
def init_db_task() -> dict[str, Any]:
    init_db()
    return {"ok": True}


@shared_task(name="worker.tasks.ingest_source")
def ingest_source(url: str, title: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    with get_session() as session:
        r = ingest_url(session, url=url, title=title, meta=meta or {})
        return {
            "source_id": str(r.source_id),
            "document_id": str(r.document_id),
            "chunks_upserted": r.chunks_upserted,
            "changed": r.changed,
        }


def _fmt_loc(path: str | None, heading: str | None) -> str:
    a = (heading or "").strip()
    b = (path or "").strip()
    if a and b and a != b:
        return f"{a} ({b})"
    return a or b


def _normalize_usage(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        try:
            dumped = raw.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(raw, "dict"):
        try:
            dumped = raw.dict()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if isinstance(raw, dict):
        return raw
    return {}


def _history_for_chat(session, chat_id: str, limit: int = 16) -> list[dict[str, str]]:
    try:
        chat_uuid = UUID(str(chat_id))
    except Exception:
        return []

    rows = session.execute(
        select(Message.role, Message.content)
        .where(Message.chat_id == chat_uuid)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    history = [{"role": str(role), "content": str(content)} for role, content in reversed(rows)]
    return history


def _extract_used_numbers(answer: str) -> list[int]:
    nums: list[int] = []
    for s in _CIT_RE.findall(answer or ""):
        n = int(s)
        if n not in nums:
            nums.append(n)
    return nums


def _filter_citations(citations: list[dict[str, Any]], used: list[int]) -> list[dict[str, Any]]:
    if not used:
        return []
    used_set = set(used)
    return [c for c in citations if int(c.get("n", 0)) in used_set]


def _dedup_key(hit: Any) -> tuple[str, str]:
    doc_id = str(getattr(hit, "document_id", "") or "")
    chunk_id = str(getattr(hit, "chunk_id", "") or "")
    if doc_id and chunk_id:
        return ("id", f"{doc_id}:{chunk_id}")

    url = (str(getattr(hit, "url", "") or "")).strip().lower()
    loc = str(getattr(hit, "heading", "") or getattr(hit, "path", "") or "").strip().lower()
    if url or loc:
        return ("url_loc", f"{url}|{loc}")

    text = (str(getattr(hit, "text", "") or "")).strip().lower()
    return ("text", hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest())


def _deduplicate_hits(hits: list[Any]) -> list[Any]:
    best_by_key: dict[tuple[str, str], Any] = {}
    order: list[tuple[str, str]] = []

    for h in hits:
        key = _dedup_key(h)
        if key not in best_by_key:
            best_by_key[key] = h
            order.append(key)
            continue

        prev = best_by_key[key]
        prev_score = float(getattr(prev, "score", 0.0) or 0.0)
        new_score = float(getattr(h, "score", 0.0) or 0.0)
        if new_score > prev_score:
            best_by_key[key] = h

    return [best_by_key[k] for k in order]


def _clean_service_markers(text: str) -> str:
    return re.sub(_NEED_MORE_RE, "", text or "").strip()


@shared_task(name="worker.tasks.answer_question")
def answer_question(
    user_external_id: int | None,
    chat_id: str,
    question: str,
    max_citations: int = 6,
    temperature: float = 0.2,
    mode: str = "consult",
) -> dict[str, Any]:
    max_citations = max(1, min(int(max_citations), 10))
    mode = mode if mode in {"brief", "consult"} else "consult"

    with get_session() as session:
        history = _history_for_chat(session, chat_id=chat_id, limit=16)
        hits = retrieve(session, question, k=max_citations)
        hits = _deduplicate_hits(hits)

        if not hits:
            base_answer = (
                "Недостатньо релевантних джерел у базі для надійної консультації. "
                "Будь ласка, додайте профільний НПА/роз'яснення за темою "
                "(посилання на zakon.rada.gov.ua, kmu.gov.ua, nbu.gov.ua тощо)."
            )
            return {
                "answer": base_answer,
                "citations": [],
                "usage": {},
            }

        context_blocks: List[str] = []
        citations_hint_lines: List[str] = []
        citations: List[Dict[str, Any]] = []

        for i, h in enumerate(hits, start=1):
            loc = _fmt_loc(h.path, h.heading)
            loc_line = f"\nЛокація: {loc}" if loc else ""

            context_blocks.append(
                f"[{i}] {h.title or 'Документ'}{loc_line}\nURL: {h.url or ''}\nФрагмент:\n{h.text}"
            )
            citations_hint_lines.append(f"[{i}] = {h.url or h.title or 'source'}" + (f" ({loc})" if loc else ""))

            citations.append(
                Citation(
                    n=i,
                    document_id=h.document_id,
                    chunk_id=h.chunk_id,
                    title=h.title,
                    url=h.url,
                    path=h.path,
                    heading=h.heading,
                    unit_type=h.unit_type,
                    unit_id=h.unit_id,
                    quote=h.text[:320] + ("…" if len(h.text) > 320 else ""),
                    score=float(h.score),
                ).model_dump(mode="json")
            )

        joined = "\n\n".join(context_blocks)
        if len(joined) > settings.max_context_chars:
            joined = joined[: settings.max_context_chars].rstrip() + "\n…"
            context_blocks = [joined]

        citations_hint = "\n".join(citations_hint_lines)

        llm_out: dict[str, Any] = {}
        answer_text = ""
        used_numbers: list[int] = []

        try:
            llm_out = answer_with_citations(
                question=question,
                context_blocks=context_blocks,
                citations_hint=citations_hint,
                chat_history=history,
                mode=mode,
                temperature=temperature,
            )
            answer_text = (llm_out.get("answer_markdown") or "").strip()
            used_numbers = [int(x) for x in llm_out.get("citations_used", []) if str(x).isdigit()]
        except Exception:
            answer_text = ""

        if not answer_text:
            preview = [f"[{c['n']}] {c['quote']}" for c in citations[:3] if c.get("quote")]
            answer_text = (
                "Не вдалося сформувати відповідь через LLM. Нижче — релевантні фрагменти для консультації:\n\n"
                + "\n\n".join(preview)
            ).strip()

        answer_text = _clean_service_markers(answer_text)

        if not used_numbers:
            used_numbers = _extract_used_numbers(answer_text)

        filtered = _filter_citations(citations, used_numbers)

        return {
            "answer": answer_text,
            "citations": filtered,
            "usage": _normalize_usage(llm_out.get("usage") if llm_out else {}),
        }
