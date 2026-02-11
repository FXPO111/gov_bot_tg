from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from shared.settings import get_settings
from .handlers import (
    cmd_back,
    cmd_cancel,
    cmd_help,
    cmd_menu,
    cmd_newchat,
    cmd_start,
    on_callback,
    on_text,
)

settings = get_settings()


def _token_configured(token: str) -> bool:
    t = (token or "").strip()
    return bool(t and t.lower() not in {"your-telegram-bot-token", "change-me", "changeme"})


def _on_error(update, context) -> None:
    logging.getLogger("bot").exception("Unhandled bot error", exc_info=context.error)


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    log = logging.getLogger("bot")

    if not _token_configured(settings.telegram_bot_token):
        log.warning("TELEGRAM_BOT_TOKEN is not configured. Bot stays idle.")
        while True:
            time.sleep(3600)

    app = ApplicationBuilder().token(settings.telegram_bot_token).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("back", cmd_back))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("newchat", cmd_newchat))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(_on_error)

    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
