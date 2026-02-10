from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder

from shared.settings import get_settings
from .handlers import cmd_help, cmd_start, cmd_newchat, on_text
from telegram.ext import CommandHandler, MessageHandler, filters

settings = get_settings()


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is empty")

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("newchat", cmd_newchat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
