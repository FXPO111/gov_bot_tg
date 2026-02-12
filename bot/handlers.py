from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .api_client import APIClient
from .ui_nav import get_state, set_state
from .ui_screens import (
    TG_MSG_LIMIT,
    answer_markup,
    format_questions,
    format_sources,
    main_menu_markup,
    need_more_markup,
    sources_markup,
    template_text,
    trim_answer_ex,
)

api = APIClient()
log = logging.getLogger("bot.handlers")

CHAT_ID_KEY = "chat_id"
UI_MSG_ID_KEY = "ui_msg_id"
LAST_CITATIONS_KEY = "last_citations"
LAST_QUESTIONS_KEY = "last_questions"
LAST_ANSWER_KEY = "last_answer"
DRAFT_CASE_KEY = "draft_case"
BUSY_KEY = "busy"
FULL_SENT_KEY = "full_answer_sent"


def _new_question_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(CHAT_ID_KEY, None)
    context.user_data.pop(LAST_CITATIONS_KEY, None)
    context.user_data.pop(LAST_QUESTIONS_KEY, None)
    context.user_data.pop(LAST_ANSWER_KEY, None)
    context.user_data.pop(DRAFT_CASE_KEY, None)
    context.user_data.pop(BUSY_KEY, None)
    context.user_data.pop(FULL_SENT_KEY, None)


def _help_text() -> str:
    return (
        "Як написати запит, щоб відповідь була точнішою:\n\n"
        "1) Що сталося (коротко, простими словами).\n"
        "2) Коли це сталося.\n"
        "3) Які документи або відповіді від установ у вас є.\n"
        "4) Що саме ви хочете отримати в результаті.\n\n"
        "Приклад: Мені відмовили у виплаті ВПО 2 тижні тому. "
        "Є письмова відмова. Підкажіть, куди звернутися і які документи підготувати."
    )


def _split_for_tg(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    if len(clean) <= limit:
        return [clean]

    parts: list[str] = []
    for block in clean.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if len(block) <= limit:
            if not parts or (len(parts[-1]) + 2 + len(block) > limit):
                parts.append(block)
            else:
                parts[-1] += f"\n\n{block}"
            continue

        start = 0
        while start < len(block):
            parts.append(block[start : start + limit])
            start += limit

    return parts


async def _send_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to: bool = True,
) -> None:
    chat = update.effective_chat
    if chat is None:
        return

    reply_to_message_id: int | None = None
    if reply_to:
        if update.message:
            reply_to_message_id = update.message.message_id
        elif update.callback_query and update.callback_query.message:
            reply_to_message_id = update.callback_query.message.message_id

    chunks = _split_for_tg(text)
    if not chunks:
        return

    for idx, chunk in enumerate(chunks):
        await context.bot.send_message(
            chat_id=chat.id,
            text=chunk,
            reply_to_message_id=reply_to_message_id if idx == 0 else None,
            reply_markup=reply_markup if idx == len(chunks) - 1 else None,
        )


async def _ensure_ui_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return

    current_ui_id = context.user_data.get(UI_MSG_ID_KEY)

    update_msg_id: int | None = None
    if update.message:
        update_msg_id = update.message.message_id
    elif update.callback_query and update.callback_query.message:
        update_msg_id = update.callback_query.message.message_id

    must_reanchor = False
    if not current_ui_id:
        must_reanchor = True
    elif update_msg_id is not None and int(current_ui_id) != int(update_msg_id):
        must_reanchor = True

    if not must_reanchor:
        return

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Готовий допомогти.",
        reply_to_message_id=update_msg_id,
    )
    context.user_data[UI_MSG_ID_KEY] = msg.message_id


async def _render_ui(update: Update, context: ContextTypes.DEFAULT_TYPE, *, text: str, markup: InlineKeyboardMarkup | None) -> None:
    await _ensure_ui_message(update, context)
    chat = update.effective_chat
    msg_id = context.user_data.get(UI_MSG_ID_KEY)
    if chat is None or not msg_id:
        return

    try:
        await context.bot.edit_message_text(chat_id=chat.id, message_id=int(msg_id), text=text, reply_markup=markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        msg = await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=markup)
        context.user_data[UI_MSG_ID_KEY] = msg.message_id


async def _thinking_indicator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[asyncio.Event, asyncio.Task]:
    stop = asyncio.Event()

    async def _worker() -> None:
        dots = [".", "..", "..."]
        i = 0
        while not stop.is_set():
            chat = update.effective_chat
            if chat is not None:
                try:
                    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
                    await _render_ui(update, context, text=f"⏳ Думаю{dots[i % 3]}", markup=None)
                except Exception:
                    log.debug("thinking indicator error", exc_info=True)
            i += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.5)
            except asyncio.TimeoutError:
                pass

    return stop, asyncio.create_task(_worker())


async def _go_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "idle")
    text = (
        "Юридичний консультант ВПО\n\n"
        "Напишіть вашу ситуацію та питання одним повідомленням.\n"
        "Приклад: Мені відмовили у виплаті ВПО. Які кроки зробити зараз?\n\n"
        "Якщо треба уточнити — просто напишіть додаткові деталі одним повідомленням."
    )
    await _render_ui(update, context, text=text, markup=main_menu_markup())


async def _go_template_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Шаблон-підказка (за бажанням):\n\n"
        f"{template_text()}\n\n"
        "Не обов’язково заповнювати все. Достатньо 2–4 пунктів.\n"
        "Надішліть це одним повідомленням — бот одразу почне аналіз."
    )
    await _render_ui(update, context, text=text, markup=need_more_markup())


async def _go_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "answer_ready")
    answer_raw = str(context.user_data.get(LAST_ANSWER_KEY) or "Порожня відповідь.")
    short, cut = trim_answer_ex(answer_raw)
    citations = context.user_data.get(LAST_CITATIONS_KEY) or []

    footer = "\n\nНаступний крок: якщо треба уточнити — просто напишіть додаткові деталі одним повідомленням."
    if cut and context.user_data.get(FULL_SENT_KEY):
        footer = "\n\n✅ Повний текст уже надіслано повідомленнями нижче." + footer

    await _render_ui(
        update,
        context,
        text=f"Відповідь:\n\n{short}{footer}",
        markup=answer_markup(has_sources=bool(citations), show_full_button=cut and not context.user_data.get(FULL_SENT_KEY)),
    )


async def _go_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "sources_view")
    src = format_sources(context.user_data.get(LAST_CITATIONS_KEY) or [])
    text = "Джерела (офіційні посилання). Натисніть на посилання, щоб відкрити документ.\n\n" + src
    await _render_ui(update, context, text=text, markup=sources_markup())


async def _go_need_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "need_more_info")
    q = format_questions(context.user_data.get(LAST_QUESTIONS_KEY) or [])
    text = "Щоб відповісти точно, уточніть, будь ласка:\n" + (q or "• Додайте більше деталей.") + "\n\nВідповідайте простими словами одним повідомленням."
    await _render_ui(update, context, text=text, markup=need_more_markup())


async def _analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get(BUSY_KEY):
        await _send_reply(update, context, "⏳ Я ще думаю над попереднім повідомленням. Зачекайте...")
        return

    draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    if not draft:
        await _go_menu(update, context)
        return

    set_state(context.user_data, "analyzing")
    context.user_data[BUSY_KEY] = True
    context.user_data[FULL_SENT_KEY] = False
    stop, task = await _thinking_indicator(update, context)

    try:
        data = await asyncio.to_thread(
            api.chat,
            draft,
            user_external_id=update.effective_user.id if update.effective_user else None,
            chat_id=context.user_data.get(CHAT_ID_KEY),
        )
    except Exception as exc:
        log.exception("Analyze failed")
        await _render_ui(update, context, text=f"Сталася помилка під час обробки: {exc}", markup=need_more_markup())
        set_state(context.user_data, "need_more_info")
        return
    finally:
        context.user_data[BUSY_KEY] = False
        stop.set()
        await task

    if data.get("chat_id"):
        context.user_data[CHAT_ID_KEY] = str(data.get("chat_id"))

    context.user_data[LAST_ANSWER_KEY] = str(data.get("answer") or "").strip()
    context.user_data[LAST_CITATIONS_KEY] = data.get("citations") or []
    questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
    context.user_data[LAST_QUESTIONS_KEY] = questions

    if bool(data.get("need_more_info", False)) and questions:
        await _go_need_more_info(update, context)
        return

    await _go_answer(update, context)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _new_question_reset(context)
    await _go_menu(update, context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _go_menu(update, context)


async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if get_state(context.user_data) == "sources_view":
        await _go_answer(update, context)
    else:
        await _go_menu(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _new_question_reset(context)
    await _go_menu(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_reply(update, context, _help_text(), reply_to=bool(update.message))


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _new_question_reset(context)
    await _go_menu(update, context)


def _parse_callback(data: str) -> tuple[str, str, str | None]:
    parts = (data or "").split(":", 2)
    if len(parts) == 1:
        return parts[0], "", None
    if len(parts) == 2:
        return parts[0], parts[1], None
    return parts[0], parts[1], parts[2]


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    ns, action, _ = _parse_callback(q.data or "")

    if ns == "main":
        if action == "newq":
            _new_question_reset(context)
            await _go_menu(update, context)
        elif action == "template":
            await _go_template_hint(update, context)
        elif action == "help":
            await _render_ui(update, context, text=_help_text(), markup=need_more_markup())
        return

    if ns == "ans":
        if action == "sources":
            await _go_sources(update, context)
        elif action == "back":
            await _go_answer(update, context)
        elif action == "toggle_full":
            full_answer = str(context.user_data.get(LAST_ANSWER_KEY) or "").strip()
            await _send_reply(update, context, full_answer or "Порожня відповідь.", reply_to=False)
            context.user_data[FULL_SENT_KEY] = True
            await _go_answer(update, context)
        return

    await _go_menu(update, context)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    msg = (update.message.text or "").strip()
    if not msg:
        return

    # Будь-який новий текст — продовження консультації без перемикань між меню.
    prev = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    context.user_data[DRAFT_CASE_KEY] = (prev + "\n\n" + msg).strip() if prev else msg
    await _analyze(update, context)
