from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

TG_MSG_LIMIT = 3800

# Теми — необовʼязковий шлях (для тих, кому простіше обрати категорію),
# але основний UX — написати ситуацію текстом одним повідомленням.
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
            "Чи були платежі/борги?",
            "Які докази/документи на руках?",
        ],
    ),
    "inherit": (
        "Спадщина",
        [
            "Хто спадкодавець і дата смерті?",
            "Яке майно входить у спадщину?",
            "Який ваш родинний зв’язок?",
            "Чи є заповіт?",
            "Чи зверталися до нотаріуса і коли?",
            "Які документи вже маєте?",
        ],
    ),
    "other": (
        "Інше",
        [
            "Коротко: що сталося і хто учасники?",
            "Коли та де це сталося?",
            "Які суми/втрати важливі?",
            "Які документи/докази вже є?",
            "Що ви вже робили?",
            "Який результат вам потрібен?",
        ],
    ),
}


# -----------------------------
# Нижня панель (ReplyKeyboard)
# -----------------------------

def bottom_keyboard() -> ReplyKeyboardMarkup:
    """
    Постійні кнопки під полем вводу (ReplyKeyboard).
    Це “нормальні кнопки знизу”.
    """
    rows = [
        [KeyboardButton("🆕 Нова справа"), KeyboardButton("📋 Шаблон")],
        [KeyboardButton("🧭 Теми"), KeyboardButton("ℹ️ Допомога")],
    ]
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Опишіть ситуацію одним повідомленням…",
    )


# -----------------------------
# Inline кнопки під відповіддю
# -----------------------------

def answer_inline_markup(has_sources: bool, show_full_button: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_sources:
        rows.append([InlineKeyboardButton("📚 Джерела", callback_data="ans:sources")])
    if show_full_button:
        rows.append([InlineKeyboardButton("⬇️ Повністю", callback_data="ans:full")])
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def topics_markup() -> InlineKeyboardMarkup:
    # Дві колонки + кнопка "Закрити"
    keys = list(TOPIC_HINTS.keys())
    rows: list[list[InlineKeyboardButton]] = []

    i = 0
    while i < len(keys):
        k1 = keys[i]
        b1 = InlineKeyboardButton(TOPIC_HINTS[k1][0], callback_data=f"topic:{k1}")
        i += 1
        if i < len(keys):
            k2 = keys[i]
            b2 = InlineKeyboardButton(TOPIC_HINTS[k2][0], callback_data=f"topic:{k2}")
            rows.append([b1, b2])
            i += 1
        else:
            rows.append([b1])

    rows.append([InlineKeyboardButton("Закрити", callback_data="main:noop")])
    return InlineKeyboardMarkup(rows)


def topic_hint_text(topic_key: str) -> str:
    name, qs = TOPIC_HINTS.get(topic_key, ("Тема", []))
    bullets = "\n".join(f"• {q}" for q in qs[:6])
    return (
        f"🧭 Тема: {name}\n\n"
        "Надішліть одним повідомленням 2–4 відповіді по пунктах (або просто опишіть ситуацію):\n\n"
        f"{bullets}"
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


def format_questions(questions: list[str]) -> str:
    clean = [str(q).strip() for q in (questions or [])[:8] if str(q).strip()]
    return "\n".join(f"• {q}" for q in clean) if clean else ""


def trim_answer_ex(text: str, limit: int = 2800) -> tuple[str, bool]:
    t = (text or "").strip()
    if len(t) <= limit:
        return t, False
    return t[:limit].rstrip() + "\n\n…", True


# -----------------------------
# Backward-compatible wrappers
# (щоб старі імпорти не падали)
# -----------------------------

def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🆕 Нова справа", callback_data="main:newq"),
                InlineKeyboardButton("📋 Шаблон", callback_data="main:template"),
            ],
            [
                InlineKeyboardButton("🧭 Теми", callback_data="main:topics"),
                InlineKeyboardButton("ℹ️ Допомога", callback_data="main:help"),
            ],
        ]
    )


def need_more_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🆕 Нова справа", callback_data="main:newq"),
                InlineKeyboardButton("🧭 Теми", callback_data="main:topics"),
            ],
            [InlineKeyboardButton("ℹ️ Допомога", callback_data="main:help")],
        ]
    )


def sources_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📚 Джерела", callback_data="ans:sources")]])


def answer_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 Джерела", callback_data="ans:sources")],
            [InlineKeyboardButton("⬇️ Повністю", callback_data="ans:full")],
        ]
    )
