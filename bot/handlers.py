from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .api_client import APIClient

api = APIClient()
CHAT_ID_KEY = "chat_id"
TG_MSG_LIMIT = 3800


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(CHAT_ID_KEY, None)
    await update.message.reply_text(
        "Опишіть ситуацію: хто/коли/що сталося/суми/документи. "
        "Я поставлю уточнювальні запитання та дам план дій із цитатами."
        "Опишіть ситуацію: хто/коли/що сталося/суми/документи.\n"
        "Надішліть питання — відповім за завантаженими джерелами з цитатами.\n"
        "Якщо база порожня — спочатку зробіть ingest через API адмінки."
    )


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(CHAT_ID_KEY, None)
    await update.message.reply_text(
        "Новий чат створено.\n"
        "Контекст попереднього діалогу очищено — ставте нове запитання."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — старт і скидання контексту\n"
        "/newchat — почати новий контекст\n"
        "/help — довідка\n"
        "Просто надішліть ваше запитання текстом."
    )


def _format_sources(citations: list[dict]) -> str:
    lines = []
    for c in citations[:6]:
        n = c.get("n")
        title = c.get("title") or "Джерело"
        url = c.get("url") or ""
        heading = c.get("heading") or c.get("path") or ""

        left = f"[{n}] {title}" if n is not None else title
        if heading:
            left += f" — {heading}"

        if url:
            lines.append(f"{left}\n{url}")
        else:
            lines.append(left)
    return "\n\n".join(lines).strip()


def _split_for_telegram(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    chunk = ""
    for line in text.splitlines(True):
        if len(chunk) + len(line) > limit:
            if chunk:
                parts.append(chunk)
                chunk = ""
            if len(line) > limit:
                for i in range(0, len(line), limit):
                    parts.append(line[i : i + limit])
                continue
        chunk += line

    if chunk:
        parts.append(chunk)

    return parts or [text[:limit]]


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    msg = (update.message.text or "").strip()
    if not msg:
        return

    chat_id = context.user_data.get(CHAT_ID_KEY)

    try:
        data = api.chat(
            msg,
            user_external_id=update.effective_user.id if update.effective_user else None,
            chat_id=chat_id,
        )
    except Exception as e:
        await update.message.reply_text(f"Помилка API: {e}")
        return

    resp_chat_id = data.get("chat_id")
    if resp_chat_id:
        context.user_data[CHAT_ID_KEY] = str(resp_chat_id)

    answer = (data.get("answer") or "").strip() or "Порожня відповідь від API."
    citations = data.get("citations") or []

    out = answer
    src = _format_sources(citations)
    if src:
        out = out + "\n\nДжерела:\n" + src

    for part in _split_for_telegram(out):
        await update.message.reply_text(part)
