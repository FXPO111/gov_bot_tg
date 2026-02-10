from __future__ import annotations

from typing import Any, Dict, List

from celery import shared_task

from shared.db import get_session, init_db
from shared.ingest import ingest_url
from shared.retrieval import retrieve
from shared.schemas import Citation
from shared.settings import get_settings
from shared.llm import answer_with_citations

settings = get_settings()


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


@shared_task(name="worker.tasks.answer_question")
def answer_question(
    user_external_id: int | None,
    chat_id: str,
    question: str,
    max_citations: int = 6,
    temperature: float = 0.2,
) -> dict[str, Any]:
    max_citations = max(1, min(int(max_citations), 10))
    with get_session() as session:
        hits = retrieve(session, question, k=max_citations)

        if not hits:
            return {
                "answer": (
                    "В базе нет релевантных источников под этот вопрос. "
                    "Нужно сначала загрузить (ingest) НПА/разъяснение по теме. "
                    "Дай ссылку на документ (ВРУ/КМУ/НБУ/суд и т.д.) — добавлю и отвечу с цитатами."
                ),
                "citations": [],
                "usage": {},
            }

        context_blocks: List[str] = []
        citations_hint_lines: List[str] = []
        citations: List[Dict[str, Any]] = []

        for i, h in enumerate(hits, start=1):
            loc = _fmt_loc(h.path, h.heading)
            loc_line = f"\nЛокация: {loc}" if loc else ""

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
                ).model_dump()
            )

        joined = "\n\n".join(context_blocks)
        if len(joined) > settings.max_context_chars:
            joined = joined[: settings.max_context_chars].rstrip() + "\n…"
            context_blocks = [joined]

        citations_hint = "\n".join(citations_hint_lines)

        llm_out = answer_with_citations(
            question=question,
            context_blocks=context_blocks,
            citations_hint=citations_hint,
            temperature=temperature,
        )

        text = (llm_out.get("text") or "").strip()
        if not text:
            text = "Не получилось сформировать ответ. Проверь источники/ключ OpenAI."

        return {
            "answer": text,
            "citations": citations,
            "usage": llm_out.get("usage", {}) or {},
        }
