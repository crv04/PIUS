import time
import logging
from telegram.error import NetworkError, TelegramError
from config import MAX_RETRIES, RETRY_DELAY

logger = logging.getLogger(__name__)

async def safe_send_message(context, chat_id, text, **kwargs):
    for attempt in range(MAX_RETRIES):  # Максимальное количество попыток
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return True # Успешная отправак
        except (NetworkError, TelegramError) as e:
            logger.warning(f"Ошибка отправки (попытка {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY) # Задержка между попытками
    return False    # Все попытки провалились

async def check_bot_health(context):
    import os
    import psutil
    from datetime import datetime
    from config import ADMIN_CHAT_ID

    pid = os.getpid()   # ID процесса бота
    process = psutil.Process(pid)   # Информация о процессе

    status = (  # Формирование статусного сообщения
        f" Статус бота:\n"
        f"• CPU: {psutil.cpu_percent()}%\n" # Загрузка процессора
        f"• RAM: {process.memory_info().rss / 1024 / 1024:.2f} МБ\n"    # Использование памяти
        f"• Задач: {len(context.application.job_queue.jobs())}\n"   #Количество активных задач
        f"• Время: {datetime.now()}"    # Текущее время
    )
    # отправка статуса админу
    await safe_send_message(context, ADMIN_CHAT_ID, status)