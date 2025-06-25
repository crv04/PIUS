import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes
)
from datetime import datetime
from database import get_db_connection
from steam_api import get_game_name_from_url, extract_app_id, get_steam_game_price
from utils import safe_send_message
from config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

# Основной обработчик ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Исключение при обработке обновления:", exc_info=context.error)
    if update and hasattr(update, 'effective_message'):
        await safe_send_message(
            context,
            update.effective_message.chat_id,
            "⚠️ Произошла ошибка. Пожалуйста, попробуйте позже."
        )
    await safe_send_message(context, ADMIN_CHAT_ID, f"🚨 Критическая ошибка:\n{context.error}")

# Команда /start - приветствие и регистрация
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING""",
            (user.id, user.username, user.first_name, user.last_name))
        conn.commit()

        support_button = InlineKeyboardButton("🆘 Поддержка", url="https://t.me/bigfloppa232")
        await update.message.reply_text(
            "Привет! Отправь мне ссылку на игру в Steam, и я буду уведомлять тебя о скидках.\n\n"
            "Доступные команды:\n"
            "/mysubs - список подписок\n"
            "/unsubscribe - отписаться от игры",
            reply_markup=InlineKeyboardMarkup([[support_button]]))
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {e}")
    finally:
        cursor.close()
        conn.close()

# Обработчик URL игры
async def handle_game_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "store.steampowered.com" not in (game_url := update.message.text):
        await update.message.reply_text("Это не похоже на ссылку на игру в Steam.")
        return

    game_name = get_game_name_from_url(game_url)
    context.user_data['pending_game'] = {'url': game_url, 'name': game_name}

    keyboard = [
        [InlineKeyboardButton("✅ Подписаться", callback_data="confirm_subscribe"),
         InlineKeyboardButton("❌ Отменить", callback_data="cancel_subscribe")]
    ]
    await update.message.reply_text(
        f"Вы хотите подписаться на уведомления для этой игры?\n"
        f"🎮 Название: <b>{game_name}</b>\n🔗 Ссылка: {game_url}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))

# Подтверждение подписки (callback)
async def handle_subscription_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_subscribe":
        await query.edit_message_text("❌ Подписка отменена")
        context.user_data.pop('pending_game', None)
        return

    if not (game_data := context.user_data.get('pending_game')):
        await query.edit_message_text("❌ Ошибка: данные игры не найдены")
        return

    user = query.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING""",
            (user.id, user.username, user.first_name, user.last_name))

        cursor.execute(
            """INSERT INTO user_games (user_id, game_url, game_name)
            VALUES (%s, %s, %s) ON CONFLICT ON CONSTRAINT unique_user_game DO NOTHING
            RETURNING id""",
            (user.id, game_data['url'], game_data['name']))

        if result := cursor.fetchone():
            sub_id = result[0]
            conn.commit()

            keyboard = [
                [InlineKeyboardButton(f"{i}%", callback_data=f"thres_{sub_id}_{i}")]
                for i in range(10, 101, 10)]
            keyboard.append([InlineKeyboardButton("Любая скидка", callback_data=f"thres_{sub_id}_0")])

            await query.edit_message_text(
                f"🎮 Игра: <b>{game_data['name']}</b>\n"
                "Выберите минимальный порог скидки:\n"
                "(Уведомление придёт при скидке ≥ выбранного значения)",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(f"ℹ️ Вы уже подписаны на «{game_data['name']}»")
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при подписке: {e}")
        await query.edit_message_text("❌ Произошла ошибка при добавлении подписки.")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        context.user_data.pop('pending_game', None)

# Команда /mysubs - список подписок
async def my_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT game_url, game_name FROM user_games 
            WHERE user_id = %s ORDER BY subscription_date DESC""",
            (user.id,))

        if not (games := cursor.fetchall()):
            support_button = InlineKeyboardButton("🆘 Поддержка", url="https://t.me/bigfloppa232")
            await update.message.reply_text(
                "У вас нет подписок.",
                reply_markup=InlineKeyboardMarkup([[support_button]]))
            return

        message = "🎮 Ваши подписки:\n\n"
        for game_url, game_name in games:
            if price_info := get_steam_game_price(extract_app_id(game_url)) if extract_app_id(game_url) else None:
                if price_info["discount"] > 0:
                    message += (
                        f" <b>{game_name}</b>\n"
                        f"   💰 <s>{price_info['original']} {price_info['currency']}</s> → "
                        f"<b>{price_info['final']} {price_info['currency']}</b>\n"
                        f"   🔻Скидка: {price_info['discount']}%\n"
                        f"   🔗 URL: {game_url}\n\n")
                else:
                    message += (
                        f" <b>{game_name}</b>\n"
                        f"   💰 {price_info['final']} {price_info['currency']} (без скидки)\n"
                        f"   🔗 URL: {game_url}\n\n")
            else:
                message += f" {game_name}\n   🔗 {game_url}\n\n"

        support_button = InlineKeyboardButton("🆘 Поддержка", url="https://t.me/bigfloppa232")
        await update.message.reply_text(
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[support_button]]))
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    finally:
        cursor.close()
        conn.close()

# Команда /unsubscribe - отписка от игры
async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_user_games(update, context, mode="unsubscribe")

# Вспомогательная функция показа игр
async def show_user_games(update: Update, context: ContextTypes.DEFAULT_TYPE, mode="list"):
    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT id, game_name FROM user_games 
            WHERE user_id = %s ORDER BY subscription_date DESC""",
            (user.id,))

        if not (games := cursor.fetchall()):
            await update.message.reply_text("У вас нет подписок.")
            return

        if mode == "list":
            response = "🎮 Ваши подписки:\n\n" + "\n".join(
                f"{idx + 1}. {name}" for idx, (_, name) in enumerate(games))
            await update.message.reply_text(response)
        elif mode == "unsubscribe":
            keyboard = [[InlineKeyboardButton(name, callback_data=f"unsub_{id}")]
                        for id, name in games]
            await update.message.reply_text(
                "Выберите игру для отписки:",
                reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Ошибка при получении подписок: {e}")
    finally:
        cursor.close()
        conn.close()

# Обработчик отписки (callback)
async def handle_unsubscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sub_id = int(query.data.split("_")[1])
    user = query.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """DELETE FROM user_games 
            WHERE id = %s AND user_id = %s RETURNING game_name""",
            (sub_id, user.id))

        if deleted := cursor.fetchone():
            conn.commit()
            await query.edit_message_text(f"❌ Отписались от «{deleted[0]}».")
        else:
            await query.edit_message_text("Ошибка: подписка не найдена.")
    except Exception as e:
        logger.error(f"Ошибка при отписке: {e}")
    finally:
        cursor.close()
        conn.close()

# Команда /setthreshold - установка порога скидки
async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_user_games(update, context, mode="set_threshold")

# Обработчик установки порога (callback)
async def handle_set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, sub_id, threshold = query.data.split('_')

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE user_games SET discount_threshold = %s WHERE id = %s RETURNING game_name",
            (int(threshold), int(sub_id)))
        game_name = cursor.fetchone()[0]
        conn.commit()
        await query.edit_message_text(
            f"✅ Для игры «{game_name}» установлен порог уведомления при скидке {threshold}% или больше")
    except Exception as e:
        logger.error(f"Ошибка при установке порога: {e}")
        await query.edit_message_text("❌ Произошла ошибка при установке порога")
    finally:
        cursor.close()
        conn.close()

# Команда /cancel - отмена действия
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_game_url', None)
    await update.message.reply_text("Действие отменено.")

# Команда /subscribe - подписка на игру
async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (game_data := context.user_data.get('pending_game')):
        await update.message.reply_text("Сначала отправьте ссылку на игру.")
        return

    user = update.message.from_user
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING""",
            (user.id, user.username, user.first_name, user.last_name))

        cursor.execute(
            """INSERT INTO user_games (user_id, game_url, game_name, discount_threshold)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT unique_user_game DO UPDATE SET game_name = EXCLUDED.game_name
            RETURNING id""",
            (user.id, game_data['url'], game_data['name'], 0))

        if result := cursor.fetchone():
            sub_id = result[0]
            conn.commit()

            keyboard = [
                [InlineKeyboardButton(f"{i}%", callback_data=f"thres_{sub_id}_{i}")]
                for i in range(10, 101, 10)]
            keyboard.append([InlineKeyboardButton("Любая скидка", callback_data=f"thres_{sub_id}_0")])

            await update.message.reply_text(
                f"Выберите минимальный порог скидки для «{game_data['name']}»:\n"
                "(Уведомление придёт при скидке ≥ выбранного значения)\n\n"
                "Или нажмите «Любая скидка» для уведомлений о всех скидках",
                reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(f"ℹ️ Вы уже подписаны на «{game_data['name']}»")
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при подписке: {e}")
        await update.message.reply_text("❌ Произошла ошибка при добавлении подписки.")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        context.user_data.pop('pending_game', None)

# Регистрация обработчиков
def get_handlers():
    return [
        CommandHandler("start", start),
        CommandHandler("subscribe", subscribe),
        CommandHandler("setthreshold", set_threshold),
        CommandHandler("unsubscribe", unsubscribe),
        CommandHandler("mysubs", my_subs),
        CommandHandler("cancel", cancel),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_game_url),
        CallbackQueryHandler(handle_subscription_confirmation, pattern="^(confirm_subscribe|cancel_subscribe)"),
        CallbackQueryHandler(handle_set_threshold, pattern="^thres_"),
        CallbackQueryHandler(handle_unsubscribe_button, pattern="^unsub_")
    ]