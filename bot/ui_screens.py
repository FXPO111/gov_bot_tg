from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

TG_MSG_LIMIT = 3800


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧾 Шаблон-підказка", callback_data="main:template")],
            [InlineKeyboardButton("🆕 Нове питання", callback_data="main:newq")],
        ]
    )


def answer_markup(has_sources: bool, show_full_button: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
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
    clean = [q.strip() for q in questions[:3] if str(q).strip()]
    return "\n".join(f"• {q}" for q in clean) if clean else ""


def trim_answer_ex(text: str) -> tuple[str, bool]:
    t = (text or "").strip()
    if len(t) <= 3600:
        return t, False
    return t[:3600].rstrip() + "\n\n…", True
