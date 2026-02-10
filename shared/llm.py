from __future__ import annotations

from typing import Any, Dict, List, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .settings import get_settings

settings = get_settings()

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
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
        # OpenAI возвращает data в исходном порядке
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
    # совместимость со старым кодом
    return embed_texts([text], batch_size=1)[0]


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
        "Ты юридический ассистент. Отвечай строго и по делу, как сотрудник комплаенса/юротдела. "
        "Не фантазируй. Если данных недостаточно — прямо скажи что не хватает и что именно проверить. "
        "Всегда используй ссылки-цитаты вида [1], [2] строго из предоставленного контекста. "
        "Если вопрос про Украину — ориентация на законодательство Украины. "
        "Дисклеймер: это справочная информация, не замена адвокату."
    )

    context = "\n\n".join(context_blocks).strip()
    user = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Контекст (фрагменты источников):\n{context}\n\n"
        f"Формат цитирования:\n{citations_hint}\n\n"
        "Сначала дай краткий ответ (1-3 абзаца), потом 'Пояснение' пунктами, затем 'Источники' списком."
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
        "usage": getattr(resp, "usage", {}) or {},
        "model": settings.openai_model,
    }
