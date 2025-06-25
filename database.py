import psycopg2
import logging
from config import DB_CONFIG

logger = logging.getLogger(__name__)

def get_db_connection():
    # Создает и возвращает соединение с БД Postgres используя конфиг из DB_CONFIG
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    # Инициализирует структуру БД - создает таблицы если они не существуют
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Создание таблицы users (если не существует)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(100),
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                registration_date TIMESTAMP DEFAULT NOW()
            )
        """)
        # Создание таблицы user_games (если не существует)
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
        logger.info("Таблицы проверены/созданы")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")
        conn.rollback() # Откат изменений при ошибке
    finally:
        cursor.close()
        conn.close()

def update_game_discount(sub_id: int, discount: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE user_games SET last_discount = %s WHERE id = %s",
            (discount, sub_id))
        conn.commit()
        logger.debug(f"Обновлен last_discount для подписки {sub_id}: {discount}%")
    except Exception as e:
        logger.error(f"Ошибка при обновлении скидки: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()