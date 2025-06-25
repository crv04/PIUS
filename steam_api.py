import requests
import time
import logging
from typing import Optional, Dict
from config import STEAM_REQUEST_DELAY

logger = logging.getLogger(__name__)

LAST_STEAM_REQUEST_TIME = 0 #Таймстемп последнего запроса для ограничения частоты


def extract_app_id(game_url: str) -> Optional[str]:
    if 'app/' in game_url:
        parts = game_url.split('app/')[1].split('/')
        return parts[0] if parts[0].isdigit() else None # Возвращает только цифровой ID
    return None

def get_game_name_from_url(game_url: str) -> str:
    """Извлекает название игры из URL Steam"""
    if not game_url:
        return "Неизвестная игра"

    # Удаляем слэши и разбиваем URL по частям
    parts = game_url.strip("/").split("/")
    raw_name = parts[-1]    # Берем последнюю часть URL

    # Заменяем специальные символы
    name = raw_name.replace("__", ": ").replace("_", " ")

    # Удаляем цифры в конце (например, для игр с годами)
    if name.split()[-1].isdigit():
        name = " ".join(name.split()[:-1])

    return name
def get_steam_game_price(app_id: str) -> Optional[Dict]:
    global LAST_STEAM_REQUEST_TIME

    # Валидация app_id
    if not app_id or not app_id.isdigit():
        logger.error(f"Некорректный app_id: {app_id}")
        return None

    # Соблюдаем лимит запросов раз в 1.1 сек
    time_since_last = time.time() - LAST_STEAM_REQUEST_TIME
    if time_since_last < STEAM_REQUEST_DELAY:
        time.sleep(STEAM_REQUEST_DELAY - time_since_last)
    LAST_STEAM_REQUEST_TIME = time.time()

    # Формирование запроса
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc=ru"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Проверка HTTP ошибок
        data = response.json()
        app_data = data.get(str(app_id), {})

        # Обработка ошибок API
        if not app_data.get("success", False):
            logger.error(f"Steam API error for {app_id}: {app_data.get('error', 'Unknown error')}")
            return None

        # Проверка наличия данных о цене
        if "price_overview" not in app_data["data"]:
            logger.debug(f"Игра {app_id} не имеет ценовых данных")
            return None
        # Форматирование результата
        price_data = app_data["data"]["price_overview"]
        result = {
            "original": price_data.get("initial", price_data["final"]) / 100,   # Цена без скидки
            "final": price_data["final"] / 100, # Текущая цена
            "discount": price_data.get("discount_percent", 0),  # Процент скидки
            "currency": price_data["currency"]  # Валюта (RUB)
        }

        logger.debug(f"Данные по игре {app_id}: {result}")
        return result

    except Exception as e:
        logger.error(f"Ошибка при запросе к Steam API: {e}", exc_info=True)
        return None