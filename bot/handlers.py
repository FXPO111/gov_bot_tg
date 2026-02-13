from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .api_client import APIClient
from .ui_nav import get_state, set_state
from .ui_screens import (
    TG_MSG_LIMIT,
    answer_inline_markup,
    bottom_keyboard,
    format_questions,
    template_text,
    topic_hint_text,
    topics_markup,
    trim_answer_ex,
)

api = APIClient()
log = logging.getLogger("bot.handlers")

# user_data keys
CHAT_ID_KEY = "chat_id"
LAST_CITATIONS_KEY = "last_citations"
LAST_QUESTIONS_KEY = "last_questions"
LAST_ANSWER_KEY = "last_answer"
DRAFT_CASE_KEY = "draft_case"

BUSY_KEY = "busy"
PENDING_MSGS_KEY = "pending_msgs"

REQUEST_ID_KEY = "request_id"
CANCEL_KEY = "cancel"

ANSWERS_KEY = "answers_store"
MAX_ANSWERS = 30

# Status animation
STATUS_MSG_ID_KEY = "status_msg_id"
STATUS_STOP_KEY = "status_stop_evt"
STATUS_TASK_KEY = "status_task"
STATUS_ACK_TS_KEY = "status_ack_ts"

# Anti-spam / dedupe
RATE_KEY = "rate_limit"
LAST_USER_TEXT_KEY = "last_user_text"
LAST_USER_TS_KEY = "last_user_ts"

MAX_PENDING = 10
ACK_THROTTLE_SEC = 1.8
DUPLICATE_IGNORE_SEC = 1.2
CONTROL_THROTTLE_SEC = 0.4  # user попросил ~0.4 сек

# ReplyKeyboard texts
BTN_NEW = "🆕 Нова справа"
BTN_TEMPLATE = "📋 Шаблон"
BTN_TOPICS = "🧭 Теми"
BTN_TOPICS_RU = "🧭 Темы"
BTN_HELP = "ℹ️ Допомога"

CONTROL_TEXTS = {
    BTN_NEW,
    BTN_TEMPLATE,
    BTN_TOPICS,
    BTN_TOPICS_RU,
    BTN_HELP,
    "Тема",
    "Темы",
    "тема",
    "темы",
    "Нова справа",
    "Новая справа",
    "🆕 Новая справа",
}


def _throttle(context: ContextTypes.DEFAULT_TYPE, key: str, interval: float = CONTROL_THROTTLE_SEC) -> bool:
    """
    True => можно обрабатывать.
    False => слишком часто, игнор.
    """
    now = time.time()
    d = context.user_data.get(RATE_KEY)
    if not isinstance(d, dict):
        d = {}
    last = float(d.get(key) or 0.0)
    if (now - last) < interval:
        return False
    d[key] = now
    context.user_data[RATE_KEY] = d
    return True


def _answers_store(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    raw = context.user_data.get(ANSWERS_KEY)
    if isinstance(raw, dict):
        return raw
    context.user_data[ANSWERS_KEY] = {}
    return context.user_data[ANSWERS_KEY]


def _save_answer(
    context: ContextTypes.DEFAULT_TYPE,
    actions_msg_id: int,
    answer_msg_id: int,
    answer: str,
    citations: list[dict],
    was_cut: bool,
) -> None:
    store = _answers_store(context)
    store[str(actions_msg_id)] = {
        "answer": (answer or "").strip(),
        "citations": citations or [],
        "was_cut": bool(was_cut),
        "full_sent": False,
        "answer_msg_id": int(answer_msg_id),
    }
    while len(store) > MAX_ANSWERS:
        first_key = next(iter(store.keys()))
        store.pop(first_key, None)


def _get_answer(context: ContextTypes.DEFAULT_TYPE, msg_id: int) -> dict | None:
    return _answers_store(context).get(str(msg_id))


def _drop_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(DRAFT_CASE_KEY, None)
    context.user_data.pop(BUSY_KEY, None)
    context.user_data.pop(PENDING_MSGS_KEY, None)
    context.user_data.pop(REQUEST_ID_KEY, None)
    context.user_data.pop(CANCEL_KEY, None)


def _new_question_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    _drop_draft(context)
    context.user_data.pop(CHAT_ID_KEY, None)
    context.user_data.pop(LAST_CITATIONS_KEY, None)
    context.user_data.pop(LAST_QUESTIONS_KEY, None)
    context.user_data.pop(LAST_ANSWER_KEY, None)


def _help_text() -> str:
    return (
        "Юридичний консультант ВПО\n\n"
        "Пишіть ситуацію одним повідомленням:\n"
        "• що сталося\n"
        "• коли і де\n"
        "• хто учасники\n"
        "• які документи/докази є\n"
        "• що ви вже робили\n"
        "• який результат вам потрібен\n\n"
        "Якщо треба уточнити — просто додайте деталі наступним повідомленням."
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


async def _send_long_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    reply_to_message_id: int | None,
    disable_preview: bool = True,
) -> None:
    chat = update.effective_chat
    if chat is None:
        return

    chunks = _split_for_tg(text)
    for i, chunk in enumerate(chunks):
        await context.bot.send_message(
            chat_id=chat.id,
            text=chunk,
            reply_to_message_id=reply_to_message_id if i == 0 else None,
            reply_markup=bottom_keyboard() if i == 0 else None,
            disable_web_page_preview=disable_preview,
        )


async def _typing_loop(update: Update, context: ContextTypes.DEFAULT_TYPE, stop: asyncio.Event) -> None:
    while not stop.is_set():
        chat = update.effective_chat
        if chat is not None:
            try:
                await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
            except Exception:
                pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass


def _format_sources(citations: list[dict]) -> str:
    items = citations or []
    if not items:
        return "Джерела відсутні."

    blocks: list[str] = []
    for c in items[:40]:
        n = c.get("n")
        title = c.get("title") or "Джерело"
        heading = c.get("heading") or c.get("path") or ""
        url = c.get("url") or ""

        line = f"[{n}] {title}" if n is not None else title
        if heading:
            line += f" — {heading}"
        if url:
            line += f"\n{url}"
        blocks.append(line)

    return "\n\n".join(blocks)


async def _stop_status(update: Update, context: ContextTypes.DEFAULT_TYPE, *, delete: bool = True) -> None:
    stop_evt = context.user_data.get(STATUS_STOP_KEY)
    task = context.user_data.get(STATUS_TASK_KEY)
    msg_id = context.user_data.get(STATUS_MSG_ID_KEY)

    if isinstance(stop_evt, asyncio.Event):
        stop_evt.set()

    if isinstance(task, asyncio.Task):
        task.cancel()
        try:
            await task
        except Exception:
            pass

    context.user_data.pop(STATUS_STOP_KEY, None)
    context.user_data.pop(STATUS_TASK_KEY, None)

    if msg_id and update.effective_chat is not None and delete:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=int(msg_id))
        except Exception:
            pass

    context.user_data.pop(STATUS_MSG_ID_KEY, None)
    context.user_data.pop(STATUS_ACK_TS_KEY, None)


async def _status_loop(update: Update, context: ContextTypes.DEFAULT_TYPE, stop: asyncio.Event, msg_id: int) -> None:
    frames = [
        "⏳ Аналізую запит.",
        "⏳ Аналізую запит..",
        "⏳ Аналізую запит...",
    ]
    i = 0
    chat = update.effective_chat
    if chat is None:
        return

    while not stop.is_set():
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=msg_id,
                text=frames[i % len(frames)],
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "message to edit not found" in str(e).lower():
                stop.set()
                break
        except Exception:
            pass

        i += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.9)
        except asyncio.TimeoutError:
            pass


async def _start_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _stop_status(update, context, delete=True)

    chat = update.effective_chat
    if chat is None:
        return

    reply_to_message_id = update.message.message_id if update.message else None
    msg = await context.bot.send_message(
        chat_id=chat.id,
        text="⏳ Аналізую запит.",
        reply_to_message_id=reply_to_message_id,
        disable_web_page_preview=True,
    )
    context.user_data[STATUS_MSG_ID_KEY] = msg.message_id

    stop_evt = asyncio.Event()
    context.user_data[STATUS_STOP_KEY] = stop_evt
    context.user_data[STATUS_TASK_KEY] = asyncio.create_task(_status_loop(update, context, stop_evt, msg.message_id))


async def _ack_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    msg_id = context.user_data.get(STATUS_MSG_ID_KEY)
    if chat is None or not msg_id:
        return

    now = time.time()
    last = float(context.user_data.get(STATUS_ACK_TS_KEY) or 0.0)
    if now - last < ACK_THROTTLE_SEC:
        return

    pending = context.user_data.get(PENDING_MSGS_KEY) or []
    if not isinstance(pending, list):
        return

    n = len(pending)
    if n <= 0:
        return

    try:
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=int(msg_id),
            text=f"⏳ Аналізую запит… (отримав уточнень: {n})",
            disable_web_page_preview=True,
        )
        context.user_data[STATUS_ACK_TS_KEY] = now
    except Exception:
        pass


async def _send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            "⚖️ Юридичний консультант ВПО\n\n"
            "Надішліть вашу ситуацію одним повідомленням.\n"
            "Кнопки знизу — навігація (шаблон/теми/нова справа/довідка)."
        ),
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )
    set_state(context.user_data, "idle")


async def _send_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text="📋 Шаблон:\n\n" + template_text(),
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )


async def _send_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text="🧭 Оберіть тему (необовʼязково). Або просто напишіть ситуацію текстом.",
        reply_markup=topics_markup(),
        disable_web_page_preview=True,
    )


async def _send_need_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = format_questions(context.user_data.get(LAST_QUESTIONS_KEY) or [])
    lines = [line for line in q.splitlines() if line.strip()][:6]
    questions_text = "\n".join(lines) if lines else "• Додайте більше деталей."

    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            "Щоб відповісти точно, уточніть, будь ласка:\n"
            f"{questions_text}\n\n"
            "Відповідайте одним повідомленням."
        ),
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )
    set_state(context.user_data, "need_more_info")


async def _send_answer_short(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    1) Відповідь — повідомлення з ReplyKeyboard (щоб нижні кнопки гарантовано зʼявлялись).
    2) Під відповіддю — коротке повідомлення “Дії” з inline-кнопками (джерела/повністю).
    """
    answer_raw = str(context.user_data.get(LAST_ANSWER_KEY) or "Порожня відповідь.").strip()
    citations = context.user_data.get(LAST_CITATIONS_KEY) or []

    answer_short, was_cut = trim_answer_ex(answer_raw)
    footer = "\n\nЯкщо треба уточнити — просто додайте деталі наступним повідомленням."
    text = f"✅ Відповідь:\n\n{answer_short}{footer}"

    chat = update.effective_chat
    if chat is None:
        return

    reply_to_message_id = update.message.message_id if update.message else None

    # Основна відповідь + нижні кнопки
    ans_msg = await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        reply_to_message_id=reply_to_message_id,
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )

    # Окремий блок дій з inline кнопками (не впливає на нижню клавіатуру)
    actions_markup = answer_inline_markup(has_sources=bool(citations), show_full_button=was_cut)
    if actions_markup.inline_keyboard:
        actions_msg = await context.bot.send_message(
            chat_id=chat.id,
            text="Дії по відповіді:",
            reply_to_message_id=ans_msg.message_id,
            reply_markup=actions_markup,
            disable_web_page_preview=True,
        )
        _save_answer(context, actions_msg.message_id, ans_msg.message_id, answer_raw, citations, was_cut)
    else:
        _save_answer(context, ans_msg.message_id, ans_msg.message_id, answer_raw, citations, was_cut)

    set_state(context.user_data, "answer_ready")


def _dedupe_should_ignore(context: ContextTypes.DEFAULT_TYPE, msg: str) -> bool:
    now = time.time()
    last_text = str(context.user_data.get(LAST_USER_TEXT_KEY) or "")
    last_ts = float(context.user_data.get(LAST_USER_TS_KEY) or 0.0)

    context.user_data[LAST_USER_TEXT_KEY] = msg
    context.user_data[LAST_USER_TS_KEY] = now

    if msg and msg == last_text and (now - last_ts) < DUPLICATE_IGNORE_SEC:
        return True
    return False


def _push_pending(context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
    pending = context.user_data.get(PENDING_MSGS_KEY)
    if not isinstance(pending, list):
        pending = []
    pending.append(msg)
    if len(pending) > MAX_PENDING:
        pending = pending[-MAX_PENDING:]
        pending[0] = "[… частина уточнень згорнута через спам …]\n" + pending[0]
    context.user_data[PENDING_MSGS_KEY] = pending


async def _analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get(BUSY_KEY):
        return

    draft = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
    if not draft:
        return

    context.user_data[BUSY_KEY] = True
    context.user_data[CANCEL_KEY] = False
    req_id = str(time.time_ns())
    context.user_data[REQUEST_ID_KEY] = req_id
    set_state(context.user_data, "analyzing")

    await _start_status(update, context)

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_loop(update, context, stop_typing))

    try:
        while True:
            if context.user_data.get(CANCEL_KEY) or context.user_data.get(REQUEST_ID_KEY) != req_id:
                return

            data = await asyncio.to_thread(
                api.chat,
                draft,
                user_external_id=update.effective_user.id if update.effective_user else None,
                chat_id=context.user_data.get(CHAT_ID_KEY),
            )

            if context.user_data.get(CANCEL_KEY) or context.user_data.get(REQUEST_ID_KEY) != req_id:
                return

            if data.get("chat_id"):
                context.user_data[CHAT_ID_KEY] = str(data.get("chat_id"))

            answer_text = str(data.get("answer") or "").strip()
            citations = data.get("citations") or []
            questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
            need_more_info = bool(data.get("need_more_info", False))

            context.user_data[LAST_ANSWER_KEY] = answer_text
            context.user_data[LAST_CITATIONS_KEY] = citations
            context.user_data[LAST_QUESTIONS_KEY] = questions

            pending = context.user_data.get(PENDING_MSGS_KEY) or []
            if isinstance(pending, list) and pending:
                context.user_data[PENDING_MSGS_KEY] = []
                extra = "\n\n".join(str(x).strip() for x in pending if str(x).strip())
                draft = f"{draft}\n\n{extra}".strip()
                context.user_data[DRAFT_CASE_KEY] = draft
                continue

            if need_more_info and questions:
                await _send_need_more_info(update, context)
                return

            await _send_answer_short(update, context)
            return

    except Exception:
        log.exception("Analyze failed")
        chat = update.effective_chat
        if chat is not None:
            await context.bot.send_message(
                chat_id=chat.id,
                text="Сталася помилка під час обробки. Натисніть «Нова справа» і надішліть запит ще раз.",
                reply_markup=bottom_keyboard(),
            )
        set_state(context.user_data, "idle")
    finally:
        context.user_data[BUSY_KEY] = False

        stop_typing.set()
        try:
            await typing_task
        except Exception:
            pass

        await _stop_status(update, context, delete=True)


async def _do_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[CANCEL_KEY] = True
    await _stop_status(update, context, delete=True)
    _new_question_reset(context)
    set_state(context.user_data, "idle")

    chat = update.effective_chat
    if chat is None:
        return

    await context.bot.send_message(
        chat_id=chat.id,
        text="🆕 Нова справа. Надішліть вашу ситуацію одним повідомленням.",
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _new_question_reset(context)
    await _send_welcome(update, context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_welcome(update, context)


async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_welcome(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[CANCEL_KEY] = True
    await _stop_status(update, context, delete=True)
    _new_question_reset(context)
    chat = update.effective_chat
    if chat is not None:
        await context.bot.send_message(
            chat_id=chat.id,
            text="Скасовано. Надішліть нову ситуацію одним повідомленням.",
            reply_markup=bottom_keyboard(),
        )
    set_state(context.user_data, "idle")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text=_help_text(),
        reply_markup=bottom_keyboard(),
        disable_web_page_preview=True,
    )


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_new_case(update, context)


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

    data = q.data or ""
    # антиспам на callback
    if not _throttle(context, f"cb:{data}"):
        try:
            await q.answer()
        except Exception:
            pass
        return

    try:
        await q.answer()
    except BadRequest:
        pass

    ns, action, _extra = _parse_callback(data)

    if ns == "main":
        if action in {"newq", "newchat"}:
            await _do_new_case(update, context)
            return
        if action in {"back", "menu"}:
            await _send_welcome(update, context)
            return
        if action == "help":
            await cmd_help(update, context)
            return
        if action == "template":
            await _send_template(update, context)
            return
        if action == "topics":
            await _send_topics(update, context)
            return
        if action == "cancel":
            await cmd_cancel(update, context)
            return
        if action == "noop":
            if q.message:
                try:
                    await q.message.delete()
                except Exception:
                    pass
            await _send_welcome(update, context)
            return
        return

    if ns == "topic":
        chat = q.message.chat if q.message else update.effective_chat
        if chat is None:
            return
        await context.bot.send_message(
            chat_id=chat.id,
            text=topic_hint_text(action),
            reply_markup=bottom_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if ns == "ans":
        msg = q.message
        if not msg:
            return

        payload = _get_answer(context, msg.message_id)
        if not payload:
            await context.bot.send_message(
                chat_id=msg.chat.id,
                text="Сесія оновилась. Натисніть «Нова справа» і надішліть запит ще раз.",
                reply_markup=bottom_keyboard(),
            )
            return

        anchor_id = int(payload.get("answer_msg_id") or msg.message_id)

        if action == "sources":
            src_text = _format_sources(payload.get("citations") or [])
            await context.bot.send_message(
                chat_id=msg.chat.id,
                text="📚 Джерела (офіційні посилання)\n\n" + src_text,
                reply_to_message_id=anchor_id,
                reply_markup=bottom_keyboard(),
                disable_web_page_preview=True,
            )
            return

        if action == "full":
            if payload.get("full_sent"):
                return
            full_answer = str(payload.get("answer") or "").strip() or "Порожня відповідь."
            await _send_long_text(
                update,
                context,
                "✅ Відповідь (повна):\n\n" + full_answer,
                reply_to_message_id=anchor_id,
                disable_preview=True,
            )
            payload["full_sent"] = True
            return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    msg = (update.message.text or "").strip()
    if not msg:
        return

    # 1) Сервисные кнопки обрабатываем сразу и под throttle (чтобы "Шаблон" не спамился)
    if msg in CONTROL_TEXTS:
        if msg in {BTN_NEW, "Нова справа", "Новая справа", "🆕 Новая справа"}:
            await _do_new_case(update, context)
            return

            # остальное — можно throttle
        if not _throttle(context, f"ctl:{msg}"):
            return

        if context.user_data.get(BUSY_KEY):
            return

        if msg == BTN_TEMPLATE:
            await _send_template(update, context)
            return
        if msg in {BTN_TOPICS, BTN_TOPICS_RU, "Тема", "Темы", "тема", "темы"}:
            await _send_topics(update, context)
            return
        if msg == BTN_HELP:
            await cmd_help(update, context)
            return

        return

    # 2) Общий антиспам на текст (не чаще 1 сообщения в 0.4с)
    if not _throttle(context, "txt:any", interval=CONTROL_THROTTLE_SEC):
        return

    # 3) Дедуп обычного текста
    if _dedupe_should_ignore(context, msg):
        return

    # 4) Если бот занят — складываем уточнение, без новых сообщений
    if context.user_data.get(BUSY_KEY):
        _push_pending(context, msg)
        await _ack_pending(update, context)
        return

    state = get_state(context.user_data)

    if state == "need_more_info":
        prev = str(context.user_data.get(DRAFT_CASE_KEY) or "").strip()
        context.user_data[DRAFT_CASE_KEY] = f"{prev}\n\n{msg}".strip() if prev else msg
    else:
        _drop_draft(context)
        context.user_data[DRAFT_CASE_KEY] = msg

    await _analyze(update, context)
