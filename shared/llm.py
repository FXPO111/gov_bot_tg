from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .settings import get_settings

settings = get_settings()

_client: Optional[OpenAI] = None
_CIT_RE = re.compile(r"\[(\d{1,2})\]")


def _is_openai_enabled() -> bool:
    key = (settings.openai_api_key or "").strip()
    return bool(key and key.lower() not in {"changeme", "change-me", "your-openai-key"})


def get_client() -> OpenAI:
    global _client
    if not _is_openai_enabled():
        raise RuntimeError("OPENAI_API_KEY is missing or placeholder. Set a valid key in .env.")
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_s)
    return _client


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.openai_max_retries),
    wait=wait_exponential(multiplier=1, min=1, max=12),
    retry=retry_if_exception_type(Exception),
)
def embed_texts(texts: List[str], *, batch_size: int = 32) -> List[List[float]]:
    client = get_client()
    out: List[List[float]] = []
    i = 0
    while i < len(texts):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=settings.openai_embed_model, input=batch)
        out.extend([d.embedding for d in resp.data])
        i += batch_size
    return out


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.openai_max_retries),
    wait=wait_exponential(multiplier=1, min=1, max=12),
    retry=retry_if_exception_type(Exception),
)
def embed_text(text: str) -> List[float]:
    return embed_texts([text], batch_size=1)[0]


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            dumped = usage.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(usage, "dict"):
        try:
            dumped = usage.dict()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if isinstance(usage, dict):
        return usage
    return {}


def _extract_text_from_response(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text)
    try:
        return str(resp.output[0].content[0].text)
    except Exception:
        return ""


def _extract_citation_numbers(text: str) -> list[int]:
    out: list[int] = []
    for m in _CIT_RE.findall(text or ""):
        n = int(m)
        if n not in out:
            out.append(n)
    return out


def _coerce_structured_payload(payload: Any, fallback_text: str) -> dict[str, Any]:
    obj: dict[str, Any]
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
        obj = parsed if isinstance(parsed, dict) else {}
    elif isinstance(payload, dict):
        obj = payload
    else:
        obj = {}

    answer_md = str(obj.get("answer_markdown") or fallback_text or "").strip()
    answer_md = re.sub(r"(?is)\n?#+\s*(Источники|Джерела|Источники|Джерела)\b.*$", "", answer_md).strip()
    answer_md = re.sub(r"(?is)\n?(Источники|Джерела)\s*:.*$", "", answer_md).strip()

    raw_used = obj.get("citations_used")
    used: list[int] = []
    if isinstance(raw_used, list):
        for x in raw_used:
            try:
                n = int(x)
            except Exception:
                continue
            if n not in used:
                used.append(n)

    if not used:
        used = _extract_citation_numbers(answer_md)

    raw_questions = obj.get("questions")
    questions = [str(q).strip() for q in raw_questions if str(q).strip()] if isinstance(raw_questions, list) else []

    raw_notes = obj.get("notes")
    notes = [str(q).strip() for q in raw_notes if str(q).strip()] if isinstance(raw_notes, list) else []

    need_more_info = bool(obj.get("need_more_info", False))

    return {
        "answer_markdown": answer_md,
        "citations_used": used,
        "need_more_info": need_more_info,
        "questions": questions,
        "notes": notes,
    }


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.openai_max_retries),
    wait=wait_exponential(multiplier=1, min=1, max=12),
    retry=retry_if_exception_type(Exception),
)
def answer_with_citations(
    *,
    question: str,
    context_blocks: List[str],
    citations_hint: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
    mode: str = "consult",
    temperature: float = 0.2,
) -> Dict[str, Any]:
    client = get_client()

    history_text = "\n".join(
        f"- {h.get('role', 'user')}: {(h.get('content', '') or '').strip()}" for h in (chat_history or [])
    ).strip()

    system = (
        "Ты юридический консультант. Нельзя выдумывать факты и нормы. "
        "Используй ТОЛЬКО предоставленный контекст и ссылки [n]. "
        "Никогда не добавляй в answer_markdown раздел 'Источники'/'Джерела' — источники выводятся отдельно системой. "
        "Если данных недостаточно, выстави need_more_info=true и задай уточняющие вопросы. "
        "Формат ответа: Висновок, Норма, Що це означає на практиці, Ризики/обмеження, Що робити далі. "
        "Если need_more_info=true, добавь Питання для уточнення. "
        "Язык ответа должен совпадать с языком вопроса."
    )

    style = "Консультационный, с планом действий и рисками." if mode == "consult" else "Короткий ответ по сути, но с цитатами."

    user = (
        f"Вопрос:\n{question}\n\n"
        f"Режим: {mode}. Стиль: {style}\n\n"
        f"История диалога:\n{history_text or '(пусто)'}\n\n"
        f"Контекст (фрагменты):\n{chr(10).join(context_blocks)}\n\n"
        f"Подсказка для цитирования:\n{citations_hint}\n"
    )

    try:
        resp = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "consultation_answer",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answer_markdown": {"type": "string"},
                            "citations_used": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 1, "maximum": 99},
                            },
                            "need_more_info": {"type": "boolean"},
                            "questions": {"type": "array", "items": {"type": "string"}},
                            "notes": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["answer_markdown", "citations_used", "need_more_info", "questions"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        )
    except Exception:
        resp = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )

    raw_text = _extract_text_from_response(resp)
    parsed = _coerce_structured_payload(raw_text, raw_text)
    parsed["usage"] = _usage_to_dict(getattr(resp, "usage", None))
    parsed["model"] = settings.openai_model
    return parsed
