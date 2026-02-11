from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from shared.db import get_session, init_db
from shared.models import AuditLog, Chat, ConversationTurn, Message, User
from shared.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    IngestTaskResponse,
    TaskStatusResponse,
)
from shared.settings import get_settings
from shared.utils import normalize_text

from .celery_client import get_result, send_task

settings = get_settings()
router = APIRouter()


def _admin_guard(x_admin_token: Optional[str]) -> None:
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post("/admin/init-db")
def admin_init_db(x_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _admin_guard(x_admin_token)
    try:
        init_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"init-db failed: {exc}")
    return {"ok": True}


@router.post("/admin/ingest", response_model=IngestResponse | IngestTaskResponse)
def admin_ingest(
    req: IngestRequest,
    sync: bool = Query(default=False, description="If true, ingest in-process (no Celery)."),
    x_admin_token: Optional[str] = Header(default=None),
) -> IngestResponse | IngestTaskResponse:
    _admin_guard(x_admin_token)

    url = normalize_text(req.url)
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    if sync:
        # локальный импорт, чтобы не тянуть ingest-зависимости при импорте роутера
        from shared.ingest import ingest_url

        with get_session() as session:
            r = ingest_url(session, url=url, title=req.title, meta=req.meta)
            return IngestResponse(
                source_id=r.source_id,
                document_id=r.document_id,
                chunks_upserted=r.chunks_upserted,
                changed=r.changed,
            )

    task = send_task("worker.tasks.ingest_source", url, req.title, req.meta)
    return IngestTaskResponse(task_id=str(task.id))


@router.get("/admin/task/{task_id}", response_model=TaskStatusResponse)
def admin_task(task_id: str, x_admin_token: Optional[str] = Header(default=None)) -> TaskStatusResponse:
    _admin_guard(x_admin_token)
    res = get_result(task_id)
    if not res.ready():
        return TaskStatusResponse(task_id=task_id, ready=False)
    if res.successful():
        return TaskStatusResponse(task_id=task_id, ready=True, successful=True, result=res.result)
    return TaskStatusResponse(task_id=task_id, ready=True, successful=False, error=str(res.result))


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    question = normalize_text(req.question)
    if not question:
        raise HTTPException(status_code=400, detail="Empty question")

    with get_session() as session:
        # upsert user
        user: Optional[User] = None
        if req.user_external_id is not None:
            user = session.query(User).filter(User.tg_user_id == int(req.user_external_id)).one_or_none()
        if user is None:
            user = User(tg_user_id=int(req.user_external_id) if req.user_external_id is not None else None)
            session.add(user)
            session.flush()

        # get/create chat
        chat_obj: Optional[Chat] = None
        if req.chat_id is not None:
            chat_obj = session.query(Chat).filter(Chat.id == req.chat_id).one_or_none()
            if chat_obj is None:
                raise HTTPException(status_code=404, detail="chat_id not found")
            if chat_obj.user_id != user.id:
                raise HTTPException(status_code=403, detail="chat_id belongs to another user")
        else:
            chat_obj = Chat(user_id=user.id)
            session.add(chat_obj)
            session.flush()

        session.add(Message(chat_id=chat_obj.id, role="user", content=question))
        session.flush()

        # blocking call to worker
        task = send_task(
            "worker.tasks.answer_question",
            req.user_external_id,
            str(chat_obj.id),
            question,
            int(req.max_citations),
            float(req.temperature),
            req.mode,
        )
        try:
            result = task.get(timeout=70)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Worker timeout/error: {e}")

        answer_text = (result.get("answer") or "").strip()
        session.add(Message(chat_id=chat_obj.id, role="assistant", content=answer_text))

        questions = [str(q).strip() for q in (result.get("questions") or []) if str(q).strip()]
        need_more_info = bool(result.get("need_more_info", False))
        citations = result.get("citations", [])

        session.add(
            ConversationTurn(
                chat_id=chat_obj.id,
                user_id=user.id,
                question=question,
                answer=answer_text,
                need_more_info=need_more_info,
                questions_json=questions,
                citations_count=len(citations),
            )
        )
        session.add(
            AuditLog(
                user_id=user.id,
                chat_id=chat_obj.id,
                event="chat_answered",
                source="api",
                payload_json={
                    "need_more_info": need_more_info,
                    "questions_count": len(questions),
                    "citations_count": len(citations),
                    "mode": req.mode,
                },
            )
        )
        session.flush()

        return ChatResponse(
            chat_id=chat_obj.id,
            answer=answer_text,
            citations=citations,
            need_more_info=need_more_info,
            questions=questions,
            notes=[str(n).strip() for n in (result.get("notes") or []) if str(n).strip()],
            usage=result.get("usage", {}),
        )


@router.post("/billing/webhook")
def billing_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    # заглушка под оплату
    return {"ok": True}
