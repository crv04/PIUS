import logging
from database import get_db_connection, update_game_discount
from steam_api import get_steam_game_price, extract_app_id
from utils import safe_send_message
from config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)


async def check_discounts(context):
    """Проверяет скидки для всех подписанных игр"""
    logger.info("Запуск проверки скидок")
    conn = None
    try:
        # Устанавливаем соединение с БД
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем все активные подписки с рисоединённых пользователей
        cursor.execute("""
            SELECT ug.id, ug.user_id, ug.game_url, ug.game_name, 
                   ug.last_discount, ug.discount_threshold
            FROM user_games ug
            JOIN users u ON ug.user_id = u.user_id
        """)
        # Обрабатываем каждую подписку
        for sub_id, user_id, game_url, game_name, last_discount, threshold in cursor.fetchall():
            # Извлекаем app_id из URL игры
            app_id = extract_app_id(game_url)
            if not app_id:
                continue     # Пропускаем если не удалось извлечь app_id

            price_info = get_steam_game_price(app_id)   # Получаем текущую информацию о цене из Steam API
            if not price_info:  # Пропускаем если данных нет
                logger.debug(f"Не удалось получить данные для {game_name} (app_id: {app_id})")
                continue

            current_discount = price_info["discount"]

            # Всегда обновляем last_discount, даже если скидка ниже порога
            update_game_discount(sub_id, current_discount)

            # Проверяем условие для уведомления
            # Текущая скидка >= порога пользователя
            # Скидка изменилась с последней проверки
            if current_discount >= threshold and current_discount != last_discount:
                # Формируем красивое сообщение
                message = (
                    f"🎉 <b>Скидка на {game_name} достигла вашего порога {threshold}%!</b>\n"
                    f"💰 <s>{price_info['original']} {price_info['currency']}</s> → "
                    f"<b>{price_info['final']} {price_info['currency']}</b>\n"
                    f"🔻 Текущая скидка: {current_discount}%\n"
                    f"🔗 URL: {game_url}"
                )
                # Отправляем сообщение через безопасный метол
                await safe_send_message(
                    context,
                    user_id,
                    message,
                    parse_mode="HTML"   # Поддержка HTML-разметки
                )
                logger.info(f"Отправлено уведомление для {user_id} о скидке на {game_name}")

    except Exception as e:
        # Логируем и сообщаем админу об ошибках
        logger.error(f"Ошибка при проверке скидок: {e}", exc_info=True)
        await safe_send_message(
            context,
            ADMIN_CHAT_ID,
            f"🚨 Ошибка при проверке скидок:\n{e}"
        )
    finally:
        # Всегда закрываем соединение с БД
        if conn:
            conn.close()