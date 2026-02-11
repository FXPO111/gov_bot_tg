from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .api_client import APIClient

api = APIClient()
CHAT_ID_KEY = "chat_id"
LAST_CITATIONS_KEY = "last_citations"
LAST_QUESTIONS_KEY = "last_questions"
LAST_NEED_MORE_INFO_KEY = "last_need_more_info"
LAST_TOPIC_KEY = "last_topic"
TG_MSG_LIMIT = 3800

MAIN_PROMPT_TEXT = (
    "Опишіть, що сталося: хто, коли, де, суми, які документи є. "
    "Якщо не знаєте — пишіть як можете."
)

TOPIC_HINTS: dict[str, tuple[str, list[str]]] = {
    "credit": (
        "Кредити/борги",
        [
            "Хто кредитор або кому ви винні?",
            "Дата договору/розписки та сума боргу.",
            "Чи є графік платежів і прострочка?",
            "Які штрафи/пеня нараховані?",
            "Чи були вимоги, дзвінки, листи або суд?",
            "Які документи у вас на руках?",
        ],
    ),
    "fines": (
        "Штрафи/поліція",
        [
            "Хто склав постанову або протокол?",
            "Дата, місце та суть порушення.",
            "Номер постанови/протоколу.",
            "Який строк оскарження залишився?",
            "Чи є фото/відео або свідки?",
            "Які документи вже отримали/подали?",
        ],
    ),
    "work": (
        "Робота",
        [
            "Хто роботодавець і яка посада?",
            "Що сталося: звільнення, борг по зарплаті, інше?",
            "Коли це сталося та які були накази/повідомлення?",
            "Які суми заборгованості або виплат?",
            "Чи є трудовий договір, накази, переписка?",
            "Чи зверталися до роботодавця письмово?",
        ],
    ),
    "family": (
        "Сім’я",
        [
            "Що саме: аліменти, розлучення, місце проживання дитини?",
            "Хто учасники та вік дітей (якщо є)?",
            "Чи є шлюб/розлучення офіційно зареєстровані?",
            "Які доходи та витрати важливі для справи?",
            "Чи були домовленості або рішення суду раніше?",
            "Які документи вже є?",
        ],
    ),
    "realty": (
        "Нерухомість",
        [
            "Про що спір: купівля, оренда, виселення, право власності?",
            "Адреса об’єкта та хто власник за документами?",
            "Які договори підписані та коли?",
            "Чи були платежі/борги по комунальних?",
            "Чи є реєстраційні документи, витяг, техпаспорт?",
            "Чи є претензії або судові документи?",
        ],
    ),
    "inherit": (
        "Спадщина",
        [
            "Хто спадкодавець і дата смерті?",
            "Яке майно входить у спадщину?",
            "Який ваш родинний зв’язок?",
            "Чи є заповіт?",
            "Чи подавали заяву нотаріусу та коли?",
            "Які документи підтвердження вже маєте?",
        ],
    ),
    "other": (
        "Інше",
        [
            "Коротко: що сталося і хто учасники?",
            "Коли та де це сталося?",
            "Які суми або втрати важливі?",
            "Які документи/докази вже є?",
            "Що ви вже робили для вирішення?",
            "Який результат вам потрібен?",
        ],
    ),
}


def _main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Як правильно написати", callback_data="m:template")],
            [InlineKeyboardButton("📌 Обрати тему", callback_data="m:topics")],
            [InlineKeyboardButton("🆕 Нове питання", callback_data="m:new")],
            [InlineKeyboardButton("📚 Що таке “джерела”", callback_data="m:sources_info")],
        ]
    )


def _topics_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Кредити/борги", callback_data="t:credit")],
            [InlineKeyboardButton("Штрафи/поліція", callback_data="t:fines")],
            [InlineKeyboardButton("Робота", callback_data="t:work")],
            [InlineKeyboardButton("Сім’я", callback_data="t:family")],
            [InlineKeyboardButton("Нерухомість", callback_data="t:realty")],
            [InlineKeyboardButton("Спадщина", callback_data="t:inherit")],
            [InlineKeyboardButton("Інше", callback_data="t:other")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="a:back")],
        ]
    )


def _post_answer_actions_markup(*, has_citations: bool, has_questions: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_citations:
        rows.append([InlineKeyboardButton("📚 Показати джерела", callback_data="a:sources")])
    if has_questions:
        rows.append([InlineKeyboardButton("🧩 Уточнити", callback_data="a:questions")])
    rows.append([InlineKeyboardButton("🆕 Нове питання", callback_data="m:new")])
    rows.append([InlineKeyboardButton("📋 Меню", callback_data="m:menu")])
    return InlineKeyboardMarkup(rows)


def _template_text() -> str:
    return (
        "Шаблон (скопіюйте та заповніть):\n\n"
        "1) Що сталося (1–2 речення):\n"
        "2) Хто учасники:\n"
        "3) Коли і де це сталося:\n"
        "4) Суми/збитки (якщо є):\n"
        "5) Які документи є:\n"
        "6) Що вже робили:\n"
        "7) Який результат вам потрібен:\n"
    )


def _sources_info_text() -> str:
    return (
        "“Джерела” — це документи і норми, на які спирається відповідь.\n"
        "Натисніть кнопку “Показати джерела”, щоб побачити назву, розділ та посилання."
    )


def _format_sources(citations: list[dict]) -> str:
    lines = []
    for c in citations[:6]:
        n = c.get("n")
        title = c.get("title") or "Джерело"
        heading = c.get("heading") or c.get("path") or ""
        url = c.get("url") or ""

        head = f"[{n}] {title}" if n is not None else title
        if heading:
            head += f" — {heading}"

        block = head if not url else f"{head}\n{url}"
        lines.append(block)
    return "\n\n".join(lines).strip()


def _format_questions(questions: list[str]) -> str:
    clean = [q.strip() for q in questions[:8] if q and q.strip()]
    if not clean:
        return ""
    return "\n".join(f"• {q}" for q in clean)


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


def _reset_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(CHAT_ID_KEY, None)
    context.user_data.pop(LAST_CITATIONS_KEY, None)
    context.user_data.pop(LAST_QUESTIONS_KEY, None)
    context.user_data.pop(LAST_NEED_MORE_INFO_KEY, None)


async def _send_main_menu(target, text: str | None = None) -> None:
    if text:
        await target.reply_text(text)
    await target.reply_text("Оберіть дію:", reply_markup=_main_menu_markup())


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _reset_context(context)
    if update.message:
        await _send_main_menu(update.message, MAIN_PROMPT_TEXT)


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _reset_context(context)
    if update.message:
        await _send_main_menu(update.message, "Ок, нове питання. Напишіть, що сталося.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await _send_main_menu(update.message, "Поставте запитання своїми словами або скористайтесь меню нижче.")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = (query.data or "").strip()

    if data == "m:menu":
        await query.message.reply_text("Оберіть дію:", reply_markup=_main_menu_markup())
        return

    if data == "m:new":
        _reset_context(context)
        await query.message.reply_text("Ок, нове питання. Напишіть, що сталося.")
        await query.message.reply_text("Оберіть дію:", reply_markup=_main_menu_markup())
        return

    if data == "m:template":
        await query.message.reply_text(_template_text())
        return

    if data == "m:topics":
        await query.message.reply_text("Оберіть тему-підказку:", reply_markup=_topics_markup())
        return

    if data == "m:sources_info":
        await query.message.reply_text(_sources_info_text())
        return

    if data.startswith("t:"):
        topic_key = data.split(":", 1)[1]
        if topic_key not in TOPIC_HINTS:
            await query.message.reply_text("Невідома тема. Спробуйте ще раз.")
            return

        topic_name, hints = TOPIC_HINTS[topic_key]
        context.user_data[LAST_TOPIC_KEY] = topic_name

        bullets = "\n".join(f"• {h}" for h in hints)
        await query.message.reply_text(
            f"Тема: {topic_name}.\nЩо бажано вказати:\n{bullets}\n\nПишіть далі вільним текстом.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a:back")]]),
        )
        return

    if data == "a:back":
        await query.message.reply_text("Оберіть дію:", reply_markup=_main_menu_markup())
        return

    if data == "a:sources":
        citations = context.user_data.get(LAST_CITATIONS_KEY) or []
        src = _format_sources(citations)
        if src:
            await query.message.reply_text(f"Джерела:\n\n{src}")
        else:
            await query.message.reply_text("Для останньої відповіді джерела відсутні.")
        return

    if data == "a:questions":
        questions = context.user_data.get(LAST_QUESTIONS_KEY) or []
        formatted = _format_questions(questions)
        if formatted:
            await query.message.reply_text(f"Щоб відповісти точно, уточніть, будь ласка:\n{formatted}")
        else:
            await query.message.reply_text("Уточнення для останньої відповіді відсутні.")
        return


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

    citations = data.get("citations") or []
    questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
    need_more_info = bool(data.get("need_more_info", False))

    context.user_data[LAST_CITATIONS_KEY] = citations
    context.user_data[LAST_QUESTIONS_KEY] = questions
    context.user_data[LAST_NEED_MORE_INFO_KEY] = need_more_info

    answer = (data.get("answer") or "").strip() or "Порожня відповідь від API."
    for part in _split_for_telegram(answer):
        await update.message.reply_text(part)

    if need_more_info and questions:
        formatted = _format_questions(questions)
        if formatted:
            await update.message.reply_text(
                f"Щоб відповісти точно, уточніть, будь ласка:\n{formatted}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧩 Уточнити", callback_data="a:questions")]]),
            )

    await update.message.reply_text(
        "Що далі?",
        reply_markup=_post_answer_actions_markup(has_citations=bool(citations), has_questions=bool(questions)),
    )
