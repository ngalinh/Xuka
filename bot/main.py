import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from bot.config import TELEGRAM_BOT_TOKEN, ALLOWED_USERS
from bot.handlers.start import start, help_cmd
from bot.handlers.entry import build_entry_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(build_entry_handler())

    logger.info("Xuka bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
