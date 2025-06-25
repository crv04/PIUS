import logging
import asyncio
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from database import init_db
from telegram_handlers import get_handlers, error_handler
from tasks import check_discounts
from config import TOKEN, ADMIN_CHAT_ID

# Настройка логирования (запись в файл и вывод в консоль)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"), # Логи в файл
        logging.StreamHandler(),    # Логи в консоль
    ]
)
logger = logging.getLogger(__name__)


async def post_init(app):
    # Функция, вызываемая после успешного запуска бота
    logger.info("🚀 Бот успешно запущен")
    await app.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text="🚀 Бот запущен"
    )


async def force_check(update, context):
    #Ручной запуск проверки скидок
    await check_discounts(context)
    await update.message.reply_text("✅ Проверка скидок выполнена")


def main():
    logger.info("Инициализация бота...")
    init_db()

    # Создание экземпляра бота
    application = ApplicationBuilder() \
        .token(TOKEN) \
        .post_init(post_init) \
        .build()

    # Обработчики
    application.add_error_handler(error_handler)    # Обработчик ошибок
    application.job_queue.run_repeating(check_discounts, interval=20)  # Каждый час / 20 сек для проверки не в боевом режиме

    # Добавляем все обработчики команд
    for handler in get_handlers():
        application.add_handler(handler)

    # Добавляем команду для ручной проверки
    application.add_handler(CommandHandler("forcecheck", force_check))

    # Запуск бота в режиме polling
    application.run_polling()


if __name__ == "__main__":
    main()