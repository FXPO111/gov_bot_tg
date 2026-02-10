from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from .api_client import APIClient

api = APIClient()
CHAT_ID_KEY = "chat_id"

async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(CHAT_ID_KEY, None)
    await update.message.reply_text(
        "Юрбот готов. Пиши вопрос.\n"
        "Важно: ответы формируются по загруженным источникам. "
        "Если в базе нет документов — сначала сделай ingest в админке."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — старт\n"
        "/help — справка\n"
        "Просто отправь вопрос текстом.\n"
        "Если ответ без цитат — база пустая или нет релевантных документов."
    )


def _format_sources(citations: list[dict]) -> str:
    lines = []
    for c in citations[:6]:
        n = c.get("n")
        title = c.get("title") or "Источник"
        url = c.get("url") or ""
        if url:
            lines.append(f"[{n}] {title}\n{url}")
        else:
            lines.append(f"[{n}] {title}")
    return "\n\n".join(lines).strip()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (update.message.text or "").strip()
    if not msg:
        return
    chat_id = context.user_data.get(CHAT_ID_KEY)

    try:
        data = api.chat(
            msg,
            user_external_id=update.effective_user.id,
            chat_id=chat_id,
        )

    except Exception as e:
        await update.message.reply_text(f"API ошибка: {e}")
        return
        # сохранить chat_id из ответа
        resp_chat_id = data.get("chat_id")
        if resp_chat_id:
            context.user_data[CHAT_ID_KEY] = str(resp_chat_id)

    answer = (data.get("answer") or "").strip()
    citations = data.get("citations") or []

    out = answer
    src = _format_sources(citations)
    if src:
        out = out + "\n\nИсточники:\n" + src

    # лимит сообщений TG — режем
    if len(out) <= 3800:
        await update.message.reply_text(out)
        return

    parts = []
    chunk = ""
    for line in out.splitlines(True):
        if len(chunk) + len(line) > 3800:
            parts.append(chunk)
            chunk = ""
        chunk += line
    if chunk:
        parts.append(chunk)

    for p in parts:
        await update.message.reply_text(p)
