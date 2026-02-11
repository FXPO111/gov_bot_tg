from __future__ import annotations

from typing import Any, Dict, List, Optional

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .settings import get_settings

settings = get_settings()

_client: Optional[OpenAI] = None


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
    temperature: float = 0.2,
) -> Dict[str, Any]:
    client = get_client()

    system = (
        "Ты юридический ассистент. Отвечай строго и по делу. "
        "Не выдумывай. Используй только факты из контекста и ссылки [1], [2]. "
        "Если данных недостаточно — скажи об этом явно."
    )

    context = "\n\n".join(context_blocks).strip()
    user = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Контекст:\n{context}\n\n"
        f"Подсказка для цитирования:\n{citations_hint}\n\n"
        "Сначала дай краткий ответ, затем блок 'Пояснение', затем блок 'Источники'."
    )

    resp = client.responses.create(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )

    text = getattr(resp, "output_text", None)
    if not text:
        try:
            text = resp.output[0].content[0].text
        except Exception:
            text = ""

    return {
        "text": text,
        "usage": _usage_to_dict(getattr(resp, "usage", None)),
        "model": settings.openai_model,
    }

