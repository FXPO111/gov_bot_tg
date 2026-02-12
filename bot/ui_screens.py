from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

TG_MSG_LIMIT = 3800

TOPIC_HINTS: dict[str, tuple[str, list[str]]] = {
    "credit": ("Кредити/борги", [
        "Хто кредитор або кому ви винні?",
        "Дата договору/розписки та сума боргу.",
        "Чи є графік платежів і прострочка?",
        "Які штрафи/пеня нараховані?",
        "Чи були вимоги, дзвінки, листи або суд?",
        "Які документи у вас на руках?",
    ]),
    "fines": ("Штрафи/поліція", [
        "Хто склав постанову або протокол?",
        "Дата, місце та суть порушення.",
        "Номер постанови/протоколу.",
        "Який строк оскарження залишився?",
        "Чи є фото/відео або свідки?",
        "Які документи вже отримали/подали?",
    ]),
    "work": ("Робота", [
        "Хто роботодавець і яка посада?",
        "Що сталося: звільнення, борг по зарплаті, інше?",
        "Коли це сталося та які були накази/повідомлення?",
        "Які суми заборгованості або виплат?",
        "Чи є трудовий договір, накази, переписка?",
        "Чи зверталися до роботодавця письмово?",
    ]),
    "family": ("Сім’я", [
        "Що саме: аліменти, розлучення, місце проживання дитини?",
        "Хто учасники та вік дітей (якщо є)?",
        "Чи є шлюб/розлучення офіційно зареєстровані?",
        "Які доходи та витрати важливі для справи?",
        "Чи були домовленості або рішення суду раніше?",
        "Які документи вже є?",
    ]),
    "realty": ("Нерухомість", [
        "Про що спір: купівля, оренда, виселення, право власності?",
        "Адреса об’єкта та хто власник за документами?",
        "Які договори підписані та коли?",
        "Чи були платежі/борги по комунальних?",
        "Чи є реєстраційні документи, витяг, техпаспорт?",
        "Чи є претензії або судові документи?",
    ]),
    "inherit": ("Спадщина", [
        "Хто спадкодавець і дата смерті?",
        "Яке майно входить у спадщину?",
        "Який ваш родинний зв’язок?",
        "Чи є заповіт?",
        "Чи подавали заяву нотаріусу та коли?",
        "Які документи підтвердження вже маєте?",
    ]),
    "other": ("Інше", [
        "Коротко: що сталося і хто учасники?",
        "Коли та де це сталося?",
        "Які суми або втрати важливі?",
        "Які документи/докази вже є?",
        "Що ви вже робили для вирішення?",
        "Який результат вам потрібен?",
    ]),
}


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📌 Як правильно написати", callback_data="main:template")],
            [InlineKeyboardButton("📌 Обрати тему", callback_data="main:topics")],
            [InlineKeyboardButton("🆕 Нове питання", callback_data="main:newq")],
            [InlineKeyboardButton("📚 Що таке «джерела»", callback_data="main:sources_info")],
        ]
    )


def topics_markup() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(name, callback_data=f"topic:{key}")] for key, (name, _) in TOPIC_HINTS.items()]
    return InlineKeyboardMarkup(rows)


def case_markup(has_draft: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_draft:
        rows.append([InlineKeyboardButton("✅ Готово, аналізуй", callback_data="case:analyze")])
    rows.append([InlineKeyboardButton("🧾 Вставити шаблон", callback_data="case:template")])
    rows.append([InlineKeyboardButton("🗑 Очистити", callback_data="case:clear")])
    return InlineKeyboardMarkup(rows)


def answer_markup(has_sources: bool, show_full_button: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_sources:
        rows.append([InlineKeyboardButton("📚 Джерела", callback_data="ans:sources")])
    if show_full_button:
        rows.append([InlineKeyboardButton("⬇️ Показати повністю", callback_data="ans:toggle_full")])
    rows.append([InlineKeyboardButton("🆕 Нове питання", callback_data="main:newq")])
    return InlineKeyboardMarkup(rows)


def need_more_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🆕 Нове питання", callback_data="main:newq")]])


def sources_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ До відповіді", callback_data="ans:back")],
            [InlineKeyboardButton("🆕 Нове питання", callback_data="main:newq")],
        ]
    )


def template_text() -> str:
    return (
        "1) Що сталося (1–2 речення):\n"
        "2) Хто учасники:\n"
        "3) Коли і де це сталося:\n"
        "4) Суми/збитки (якщо є):\n"
        "5) Які документи є:\n"
        "6) Що вже робили:\n"
        "7) Який результат вам потрібен:"
    )


def format_sources(citations: list[dict]) -> str:
    blocks = []
    for c in citations[:6]:
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
    return "\n\n".join(blocks) if blocks else "Джерела відсутні."


def format_questions(questions: list[str]) -> str:
    clean = [q.strip() for q in questions[:8] if str(q).strip()]
    return "\n".join(f"• {q}" for q in clean) if clean else ""


def trim_answer_ex(text: str) -> tuple[str, bool]:
    t = (text or "").strip()
    if len(t) <= 3000:
        return t, False
    return t[:3000].rstrip() + "\n\n…", True
