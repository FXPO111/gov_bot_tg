from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .api_client import APIClient
from .ui_nav import get_state, pop_screen, push_screen, reset_stack, set_state
from .ui_screens import (
    TOPIC_HINTS,
    answer_markup,
    case_markup,
    format_questions,
    format_sources,
    main_menu_markup,
    need_more_markup,
    sources_markup,
    template_text,
    topics_markup,
    trim_answer,
)

api = APIClient()
log = logging.getLogger("bot.handlers")

CHAT_ID_KEY = "chat_id"
UI_MSG_ID_KEY = "ui_msg_id"
LAST_CITATIONS_KEY = "last_citations"
LAST_QUESTIONS_KEY = "last_questions"
LAST_TOPIC_KEY = "last_topic"
LAST_ANSWER_KEY = "last_answer"
DRAFT_CASE_KEY = "draft_case"
BUSY_KEY = "busy"


def _drop_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(DRAFT_CASE_KEY, None)
    context.user_data.pop(BUSY_KEY, None)


def _new_question_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    _drop_draft(context)
    context.user_data.pop(CHAT_ID_KEY, None)
    context.user_data.pop(LAST_CITATIONS_KEY, None)
    context.user_data.pop(LAST_QUESTIONS_KEY, None)
    context.user_data.pop(LAST_ANSWER_KEY, None)
    context.user_data.pop(LAST_TOPIC_KEY, None)


async def _ensure_ui_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get(UI_MSG_ID_KEY):
        return
    if update.effective_chat is None:
        return
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Завантаження…")
    context.user_data[UI_MSG_ID_KEY] = msg.message_id


async def _render_ui(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    await _ensure_ui_message(update, context)
    chat = update.effective_chat
    msg_id = context.user_data.get(UI_MSG_ID_KEY)
    if chat is None or not msg_id:
        return

    try:
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=int(msg_id),
            text=text,
            reply_markup=markup,
        )
    except BadRequest as e:
        # не плодим новые сообщения, если контент не изменился
        if "Message is not modified" in str(e):
            return
        msg = await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=markup)
        context.user_data[UI_MSG_ID_KEY] = msg.message_id


async def _go_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, push_current: bool = False) -> None:
    current = get_state(context.user_data)
    if push_current and current != "idle":
        push_screen(context.user_data, current)
    set_state(context.user_data, "idle")
    reset_stack(context.user_data)
    text = (
        "Опишіть, що сталося: хто, коли, де, суми, які документи є.\n"
        "Якщо не знаєте — пишіть як можете."
    )
    await _render_ui(update, context, text=text, markup=main_menu_markup())


async def _go_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_state(context.user_data)
    if current != "topic_select":
        push_screen(context.user_data, current)
    set_state(context.user_data, "topic_select")
    await _render_ui(update, context, text="Оберіть тему:", markup=topics_markup())


async def _go_case_input(update: Update, context: ContextTypes.DEFAULT_TYPE, *, topic_key: str | None = None) -> None:
    current = get_state(context.user_data)
    if current != "awaiting_case":
        push_screen(context.user_data, current)
    set_state(context.user_data, "awaiting_case")

    if topic_key and topic_key in TOPIC_HINTS:
        context.user_data[LAST_TOPIC_KEY] = topic_key

    draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    topic_name = TOPIC_HINTS.get(context.user_data.get(LAST_TOPIC_KEY), ("Інше", []))[0]
    ready = "✅ Чернетка готова" if draft else "Чернетка порожня"
    text = (
        f"Тема: {topic_name}\n\n"
        f"Шаблон:\n{template_text()}\n\n"
        f"Стан: {ready}.\n"
        f"Довжина: {len(draft)} символів.\n\n"
        "Надішліть текст повідомленням або натисніть кнопку нижче."
    )
    await _render_ui(update, context, text=text, markup=case_markup(has_draft=bool(draft)))


async def _go_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_state(context.user_data)
    push_screen(context.user_data, current)
    set_state(context.user_data, "template_info")
    await _render_ui(
        update,
        context,
        text=f"Як правильно написати:\n\n{template_text()}",
        markup=main_menu_markup(),
    )


async def _go_sources_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    push_screen(context.user_data, get_state(context.user_data))
    set_state(context.user_data, "sources_info")
    await _render_ui(
        update,
        context,
        text=(
            "«Джерела» — це документи і норми, на які спирається відповідь.\n"
            "Відкрийте екран «📚 Джерела», щоб побачити список посилань."
        ),
        markup=main_menu_markup(),
    )


async def _go_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "answer_ready")
    answer = trim_answer(str(context.user_data.get(LAST_ANSWER_KEY) or "Порожня відповідь."))
    citations = context.user_data.get(LAST_CITATIONS_KEY) or []
    questions = context.user_data.get(LAST_QUESTIONS_KEY) or []
    await _render_ui(
        update,
        context,
        text=f"Відповідь:\n\n{answer}",
        markup=answer_markup(has_sources=bool(citations), has_questions=bool(questions)),
    )


async def _go_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    push_screen(context.user_data, get_state(context.user_data))
    set_state(context.user_data, "sources_view")
    src = format_sources(context.user_data.get(LAST_CITATIONS_KEY) or [])
    await _render_ui(update, context, text=f"Джерела:\n\n{src}", markup=sources_markup())


async def _go_need_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context.user_data, "need_more_info")
    q = format_questions(context.user_data.get(LAST_QUESTIONS_KEY) or [])
    draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    text = (
        "Щоб відповісти точно, уточніть, будь ласка:\n"
        f"{q or '• Додайте більше деталей.'}\n\n"
        f"Поточна чернетка: {len(draft)} символів."
    )
    await _render_ui(update, context, text=text, markup=need_more_markup())


async def _analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get(BUSY_KEY):
        await _render_ui(
            update,
            context,
            text="Вже виконується аналіз. Зачекайте кілька секунд.",
            markup=need_more_markup()
            if get_state(context.user_data) == "need_more_info"
            else case_markup(has_draft=True),
        )
        return

    draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    if not draft:
        await _go_case_input(update, context)
        return

    set_state(context.user_data, "analyzing")
    context.user_data[BUSY_KEY] = True
    await _render_ui(update, context, text="⏳ Аналізую…", markup=None)

    try:
        data = await asyncio.to_thread(
            api.chat,
            draft,
            user_external_id=update.effective_user.id if update.effective_user else None,
            chat_id=context.user_data.get(CHAT_ID_KEY),
        )
    except Exception as exc:
        log.exception("Analyze failed")
        context.user_data[BUSY_KEY] = False
        await _render_ui(update, context, text=f"Помилка API: {exc}", markup=case_markup(has_draft=True))
        set_state(context.user_data, "awaiting_case")
        return

    context.user_data[BUSY_KEY] = False

    if data.get("chat_id"):
        context.user_data[CHAT_ID_KEY] = str(data.get("chat_id"))

    context.user_data[LAST_ANSWER_KEY] = str(data.get("answer") or "")
    context.user_data[LAST_CITATIONS_KEY] = data.get("citations") or []
    questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
    context.user_data[LAST_QUESTIONS_KEY] = questions

    if bool(data.get("need_more_info", False)) and questions:
        await _go_need_more_info(update, context)
        return

    await _go_answer(update, context)


async def _go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prev = pop_screen(context.user_data)
    if not prev:
        await _go_menu(update, context)
        return

    screen = prev.get("screen")
    if screen == "topic_select":
        await _go_topics(update, context)
    elif screen == "awaiting_case":
        await _go_case_input(update, context)
    elif screen == "answer_ready":
        await _go_answer(update, context)
    elif screen == "need_more_info":
        await _go_need_more_info(update, context)
    else:
        await _go_menu(update, context)


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(context.user_data)
    if state in {"awaiting_case", "need_more_info", "analyzing"}:
        _drop_draft(context)
    await _go_menu(update, context)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _drop_draft(context)
    await _go_menu(update, context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _go_menu(update, context)


async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _go_back(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cancel(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _go_menu(update, context)


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _new_question_reset(context)
    await _go_case_input(update, context)


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

    ns, action, _param = _parse_callback(q.data or "")
    log.info("callback ns=%s action=%s state=%s", ns, action, get_state(context.user_data))

    if ns == "nav":
        if action == "menu":
            await _go_menu(update, context)
        elif action == "back":
            await _go_back(update, context)
        elif action == "cancel":
            await _cancel(update, context)
        return

    if ns == "main":
        if action == "template":
            await _go_template(update, context)
        elif action == "topics":
            await _go_topics(update, context)
        elif action == "newq":
            _new_question_reset(context)
            await _go_case_input(update, context)
        elif action == "sources_info":
            await _go_sources_info(update, context)
        return

    if ns == "topic" and action in TOPIC_HINTS:
        await _go_case_input(update, context, topic_key=action)
        return

    if ns == "case":
        if action == "template":
            draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
            tpl = template_text()
            context.user_data[DRAFT_CASE_KEY] = f"{draft}\n\n{tpl}".strip() if draft else tpl
            await _go_case_input(update, context)
        elif action == "clear":
            context.user_data[DRAFT_CASE_KEY] = ""
            await _go_case_input(update, context)
        elif action == "analyze":
            await _analyze(update, context)
        return

    if ns == "clarify" and action == "analyze":
        await _analyze(update, context)
        return

    if ns == "ans":
        if action == "sources":
            await _go_sources(update, context)
        elif action == "clarify":
            await _go_need_more_info(update, context)
        elif action == "back":
            await _go_answer(update, context)
        return

    await _render_ui(update, context, text="Невідома дія. Натисніть «Меню».", markup=main_menu_markup())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    msg = (update.message.text or "").strip()
    if not msg:
        return

    state = get_state(context.user_data)
    if state not in {"awaiting_case", "need_more_info"}:
        await _render_ui(
            update,
            context,
            text="Зараз ви в меню. Натисніть «🆕 Нове питання» або «📌 Обрати тему».",
            markup=main_menu_markup(),
        )
        return

    prev = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    context.user_data[DRAFT_CASE_KEY] = f"{prev}\n\n{msg}".strip() if prev else msg

    if state == "need_more_info":
        await _go_need_more_info(update, context)
    else:
        await _go_case_input(update, context)
