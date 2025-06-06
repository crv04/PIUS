import psycopg2
import requests
import time
import logging
from telegram.error import NetworkError, TelegramError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from datetime import datetime

# Конфигурация PostgreSQL
DB_CONFIG = {
    "dbname": "steam_bot",
    "user": "postgres",
    "password": "",
    "host": "localhost",
    "port": "5432",
}

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные настройки
MAX_RETRIES = 3
RETRY_DELAY = 5

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Проверяем существование таблиц перед созданием
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(100),
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                registration_date TIMESTAMP DEFAULT NOW()
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_games (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                game_url TEXT NOT NULL,
                game_name TEXT,
                last_discount INTEGER DEFAULT 0,
                discount_threshold INTEGER DEFAULT 0,
                subscription_date TIMESTAMP DEFAULT NOW(),
                CONSTRAINT unique_user_game UNIQUE (user_id, game_url)
            )
        """)
        conn.commit()
        print("✅ Таблицы проверены/созданы")
    except Exception as e:
        print(f"Ошибка при инициализации БД: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_user_games(update, context, mode="set_threshold")


async def handle_set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Разделяем данные: sub_123_50 → отписаться от подписки 123 с порогом 50%
    _, sub_id, threshold = query.data.split('_')

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE user_games SET discount_threshold = %s WHERE id = %s RETURNING game_name",
            (int(threshold), int(sub_id))
        )
        game_name = cursor.fetchone()[0]
        conn.commit()
        await query.edit_message_text(
            f"✅ Для игры «{game_name}» установлен порог уведомления при скидке {threshold}% или больше"
        )
    except Exception as e:
        print(f"Ошибка при установке порога: {e}")
        await query.edit_message_text("❌ Произошла ошибка при установке порога")
    finally:
        cursor.close()
        conn.close()

def get_game_name_from_url(game_url):
    if not game_url:
        return "Неизвестная игра"
    parts = game_url.strip("/").split("/")
    raw_name = parts[-1]
    name = raw_name.replace("__", ": ").replace("_", " ")
    if name.split()[-1].isdigit():
        name = " ".join(name.split()[:-1])
    return name


def extract_app_id(game_url):
    if 'app/' in game_url:
        return game_url.split('app/')[1].split('/')[0]
    return None


def get_steam_game_price(app_id):
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=ru"
    response = requests.get(url)
    data = response.json()

    if not data.get(str(app_id), {}).get("success"):
        return None

    game_data = data[str(app_id)]["data"]
    if not game_data.get("price_overview"):
        return None

    price_data = game_data["price_overview"]
    return {
        "original": price_data.get("initial", price_data["final"]) / 100,
        "final": price_data["final"] / 100,
        "discount": price_data.get("discount_percent", 0),
        "currency": price_data["currency"]
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user.id, user.username, user.first_name, user.last_name),
        )
        conn.commit()

        # Создаем кнопку поддержки
        support_button = InlineKeyboardButton(
            text="🆘 Поддержка",
            url="https://t.me/bigfloppa232"
        )
        reply_markup = InlineKeyboardMarkup([[support_button]])

        await update.message.reply_text(
            "Привет! Отправь мне ссылку на игру в Steam, и я буду уведомлять тебя о скидках.\n\n"
            "Доступные команды:\n"
            "/mysubs - список подписок\n"
            "/unsubscribe - отписаться от игры",
             reply_markup = reply_markup
        )
    except Exception as e:
        print(f"Ошибка при регистрации: {e}")
    finally:
        cursor.close()
        conn.close()


async def handle_game_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    game_url = update.message.text
    if "store.steampowered.com" not in game_url:
        await update.message.reply_text("Это не похоже на ссылку на игру в Steam.")
        return

    game_name = get_game_name_from_url(game_url)
    context.user_data['pending_game'] = {
        'url': game_url,
        'name': game_name
    }

    # Создаем кнопки для подтверждения
    keyboard = [
        [
            InlineKeyboardButton("✅ Подписаться", callback_data="confirm_subscribe"),
            InlineKeyboardButton("❌ Отменить", callback_data="cancel_subscribe")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Вы хотите подписаться на уведомления для этой игры?\n"
        f"🎮 Название: <b>{game_name}</b>\n"
        f"🔗 Ссылка: {game_url}",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def handle_subscription_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_subscribe":
        await query.edit_message_text("❌ Подписка отменена")
        if 'pending_game' in context.user_data:
            del context.user_data['pending_game']
        return

    # Получаем данные о игре из контекста
    game_data = context.user_data.get('pending_game')
    if not game_data:
        await query.edit_message_text("❌ Ошибка: данные игры не найдены")
        return

    user = query.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Добавляем запись о подписке
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user.id, user.username, user.first_name, user.last_name),
        )

        cursor.execute(
            """
            INSERT INTO user_games (user_id, game_url, game_name)
            VALUES (%s, %s, %s)
            ON CONFLICT ON CONSTRAINT unique_user_game DO NOTHING
            RETURNING id
            """,
            (user.id, game_data['url'], game_data['name']),
        )

        result = cursor.fetchone()
        if result:
            sub_id = result[0]
            conn.commit()

            # Теперь предлагаем выбрать порог скидки
            keyboard = [
                [InlineKeyboardButton(f"{i}%", callback_data=f"thres_{sub_id}_{i}")]
                for i in range(10, 101, 10)
            ]
            keyboard.append([InlineKeyboardButton("Любая скидка", callback_data=f"thres_{sub_id}_0")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"🎮 Игра: <b>{game_data['name']}</b>\n"
                "Выберите минимальный порог скидки:\n"
                "(Уведомление придёт при скидке ≥ выбранного значения)",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(f"ℹ️ Вы уже подписаны на «{game_data['name']}»")
            conn.commit()

    except Exception as e:
        print(f"Ошибка при подписке: {e}")
        await query.edit_message_text("❌ Произошла ошибка при добавлении подписки.")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        if 'pending_game' in context.user_data:
            del context.user_data['pending_game']


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    game_url = context.user_data.get('pending_game_url')

    if not game_url:
        await update.message.reply_text("Сначала отправьте ссылку на игру.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Добавляем пользователя (если его нет)
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user.id, user.username, user.first_name, user.last_name),
        )

        # 2. Добавляем игру с проверкой уникальности
        game_name = get_game_name_from_url(game_url)
        cursor.execute(
            """
            INSERT INTO user_games (user_id, game_url, game_name, discount_threshold)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT unique_user_game 
            DO UPDATE SET game_name = EXCLUDED.game_name
            RETURNING id
            """,
            (user.id, game_url, game_name, 0),  # 0 - временное значение, будет обновлено
        )

        result = cursor.fetchone()
        if result:
            sub_id = result[0]
            conn.commit()

            # 3. Предлагаем выбрать порог скидки
            keyboard = [
                [InlineKeyboardButton(f"{i}%", callback_data=f"thres_{sub_id}_{i}")]
                for i in range(10, 101, 10)  # Варианты 10%, 20%, ..., 100%
            ]

            # Добавляем кнопку "Пропустить" (уведомлять при любой скидке)
            keyboard.append([InlineKeyboardButton("Любая скидка", callback_data=f"thres_{sub_id}_0")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"Выберите минимальный порог скидки для «{game_name}»:\n"
                "(Уведомление придёт при скидке ≥ выбранного значения)\n\n"
                "Или нажмите «Любая скидка» для уведомлений о всех скидках",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(f"ℹ️ Вы уже подписаны на «{game_name}»")
            conn.commit()

    except Exception as e:
        print(f"Ошибка при подписке: {e}")
        await update.message.reply_text("❌ Произошла ошибка при добавлении подписки.")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        if 'pending_game_url' in context.user_data:
            del context.user_data['pending_game_url']


async def my_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT game_url, game_name FROM user_games 
            WHERE user_id = %s
            ORDER BY subscription_date DESC
            """,
            (user.id,),
        )
        games = cursor.fetchall()

        if not games:
            # Добавляем кнопку поддержки даже когда подписок нет
            support_button = InlineKeyboardButton(
                text="🆘 Поддержка",
                url="https://t.me/bigfloppa232"
            )
            reply_markup = InlineKeyboardMarkup([[support_button]])
            await update.message.reply_text(
                "У вас нет подписок.",
                reply_markup=reply_markup
            )
            return

        message = "🎮 Ваши подписки:\n\n"
        for game_url, game_name in games:
            app_id = extract_app_id(game_url)
            price_info = get_steam_game_price(app_id) if app_id else None

            if price_info:
                if price_info["discount"] > 0:
                    message += (
                        f"🔹 <b>{game_name}</b>\n"
                        f"   💰 <s>{price_info['original']} {price_info['currency']}</s> → "
                        f"<b>{price_info['final']} {price_info['currency']}</b>\n"
                        f"   🎯 Скидка: {price_info['discount']}%\n"
                        f"   🔗 {game_url}\n\n"
                    )
                else:
                    message += (
                        f"🔹 <b>{game_name}</b>\n"
                        f"   💰 {price_info['final']} {price_info['currency']} (без скидки)\n"
                        f"   🔗 {game_url}\n\n"
                    )
            else:
                message += f"🔹 {game_name}\n   🔗 {game_url}\n\n"
        # Добавляем кнопку поддержки внизу списка подписок
        support_button = InlineKeyboardButton(
            text="🆘 Поддержка",
            url="https://t.me/bigfloppa232"
        )
        reply_markup = InlineKeyboardMarkup([[support_button]])

        await update.message.reply_text(message, parse_mode="HTML", disable_web_page_preview=True, reply_markup=reply_markup)

    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        cursor.close()
        conn.close()


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_user_games(update, context, mode="unsubscribe")


async def show_user_games(update: Update, context: ContextTypes.DEFAULT_TYPE, mode="list"):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, game_name FROM user_games 
            WHERE user_id = %s
            ORDER BY subscription_date DESC
            """,
            (user.id,),
        )
        games = cursor.fetchall()
        if not games:
            await update.message.reply_text("У вас нет подписок.")
            return
        if mode == "list":
            response = "🎮 Ваши подписки:\n\n" + "\n".join(
                f"{idx + 1}. {name}" for idx, (_, name) in enumerate(games)
            )
            await update.message.reply_text(response)
        elif mode == "unsubscribe":
            keyboard = [
                [InlineKeyboardButton(name, callback_data=f"unsub_{id}")]
                for id, name in games
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Выберите игру для отписки:",
                reply_markup=reply_markup,
            )
    except Exception as e:
        print(f"Ошибка при получении подписок: {e}")
    finally:
        cursor.close()
        conn.close()


async def handle_unsubscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sub_id = int(query.data.split("_")[1])
    user = query.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            DELETE FROM user_games 
            WHERE id = %s AND user_id = %s
            RETURNING game_name
            """,
            (sub_id, user.id),
        )
        deleted = cursor.fetchone()
        if deleted:
            conn.commit()
            await query.edit_message_text(f"❌ Отписались от «{deleted[0]}».")
        else:
            await query.edit_message_text("Ошибка: подписка не найдена.")
    except Exception as e:
        print(f"Ошибка при отписке: {e}")
    finally:
        cursor.close()
        conn.close()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'pending_game_url' in context.user_data:
        del context.user_data['pending_game_url']
    await update.message.reply_text("Действие отменено.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик всех необработанных исключений."""
    logger.error(msg="Исключение при обработке обновления:", exc_info=context.error)

    # Отправляем сообщение об ошибке пользователю
    if update and hasattr(update, 'effective_message'):
        text = "⚠️ Произошла ошибка. Пожалуйста, попробуйте позже."
        await safe_send_message(update.effective_message, text)
async def safe_send_message(message, text, reply_markup=None, parse_mode=None, **kwargs):
    """
    Безопасная отправка сообщения с повторными попытками.
    """
    for attempt in range(MAX_RETRIES):
        try:
            await message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                **kwargs
            )
            return True
        except NetworkError as e:
            logger.warning(f"NetworkError (попытка {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            continue
        except TelegramError as e:
            logger.error(f"TelegramError: {e}")
            break
    return False

async def check_discounts(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT ug.id, ug.user_id, ug.game_url, ug.game_name, 
                   ug.last_discount, ug.discount_threshold
            FROM user_games ug
            JOIN users u ON ug.user_id = u.user_id
        """)

        for sub_id, user_id, game_url, game_name, last_discount, threshold in cursor.fetchall():
            app_id = extract_app_id(game_url)
            if not app_id:
                continue

            price_info = get_steam_game_price(app_id)
            if not price_info:
                continue

            current_discount = price_info["discount"]

            if (current_discount != last_discount and current_discount >= threshold):
                message = (
                    f"🎉 <b>Скидка на {game_name} достигла вашего порога {threshold}%!</b>\n"
                    f"💰 <s>{price_info['original']} {price_info['currency']}</s> → "
                    f"<b>{price_info['final']} {price_info['currency']}</b>\n"
                    f"🎯 Текущая скидка: {current_discount}%\n"
                    f"🔗 {game_url}"
                )

                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode="HTML"
                    )
                except NetworkError as e:
                    logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
                    continue

            cursor.execute(
                "UPDATE user_games SET last_discount = %s WHERE id = %s",
                (current_discount, sub_id),
            )
            conn.commit()

    except Exception as e:
        logger.error(f"Ошибка при проверке скидок: {e}")
    finally:
        cursor.close()
        conn.close()

def main():
    print("🤖 Бот запускается...")  # Добавьте эту строку
    init_db()
    print("✅ База данных инициализирована")  # И эту

    application = ApplicationBuilder() \
        .token("YOUR_TOKEN") \
        .post_init(lambda _: print("🚀 Бот успешно запущен и готов к работе!")) \
        .build()

    application = ApplicationBuilder().token("7517560971:AAHHa2d-9aWZFk9L1ROsvrpROx7Tm4qflhY").build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("setthreshold", set_threshold))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("mysubs", my_subs))
    application.add_handler(CommandHandler("cancel", cancel))

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_game_url))

    # Обработчики callback-кнопок
    application.add_handler(
        CallbackQueryHandler(handle_subscription_confirmation, pattern="^(confirm_subscribe|cancel_subscribe)"))
    application.add_handler(CallbackQueryHandler(handle_set_threshold, pattern="^thres_"))
    application.add_handler(CallbackQueryHandler(handle_unsubscribe_button, pattern="^unsub_"))

    application.job_queue.run_repeating(check_discounts, interval=21600)

    application.run_polling()


if __name__ == "__main__":
    main()
