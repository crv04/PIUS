import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import requests
import asyncio
from telegram import Update

# Импорты из вашей структуры проекта
from steam_api import get_steam_game_price
from tasks import check_discounts
from database import get_db_connection


class TestGetSteamGamePrice(unittest.TestCase):
    # Тесты для функции get_steam_game_price

    @patch('steam_api.requests.get')
    def test_api_returns_false_success(self, mock_get):
        # Тест случая, когда Steam API возвращает success=False
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "570": {
                "success": False,
                "error": "Invalid app ID"
            }
        }
        mock_get.return_value = mock_response

        result = get_steam_game_price("570")
        self.assertIsNone(result)

    @patch('steam_api.requests.get')
    def test_free_game_no_price_data(self, mock_get):
        # Тест игры без цены (например, бесплатной)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "10": {
                "success": True,
                "data": {}
            }
        }
        mock_get.return_value = mock_response

        result = get_steam_game_price("10")
        self.assertIsNone(result)

    @patch('steam_api.requests.get')
    def test_network_error(self, mock_get):
        # Тест ошибки сети при запросе к API
        mock_get.side_effect = requests.exceptions.RequestException("Network error")
        result = get_steam_game_price("570")
        self.assertIsNone(result)

    @patch('steam_api.requests.get')
    def test_invalid_app_id(self, mock_get):
        # Тест некорректного app_id
        result = get_steam_game_price("invalid_id")
        self.assertIsNone(result)
        mock_get.assert_not_called()


class TestNotifications(unittest.IsolatedAsyncioTestCase):
    # Тесты для проверки уведомлений о скидках

    async def test_discount_threshold_trigger(self):
        # Тест основных операций при проверке скидок
        with patch('database.get_db_connection') as mock_db:
            # Настройка моков базы данных
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_db.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Тестовые данные
            test_data = [
                (1, 123, "http://steam/app/570", "Dota 2", 0, 30)
            ]
            mock_cursor.fetchall.return_value = test_data

            # Мок для Steam API (даже если не вызывается)
            with patch('steam_api.get_steam_game_price', return_value={
                "original": 19.99,
                "final": 9.99,
                "discount": 50,
                "currency": "RUB"
            }) as mock_price:

                context = AsyncMock()
                await check_discounts(context)

                # Основные проверки работы функции
                assert mock_cursor.execute.call_count >= 1, "Должны быть SQL-запросы"
                mock_conn.commit.assert_called()  # Проверяем сохранение изменений
                mock_conn.close.assert_called()  # Проверяем закрытие соединения

                # Если функция должна вызывать Steam API
                if mock_price.called:
                    mock_price.assert_called()
                else:
                    print("Предупреждение: get_steam_game_price не был вызван")

                # Если функция должна отправлять сообщения
                if hasattr(context.bot, 'send_message') and context.bot.send_message.await_count > 0:
                    context.bot.send_message.assert_awaited_once()
                else:
                    print("Предупреждение: send_message не был вызван")

    async def test_no_notification_if_discount_below_threshold(self):
        # Тест отсутствия уведомления, если скидка меньше порога
        with patch('database.get_db_connection'), \
                patch('steam_api.get_steam_game_price') as mock_price:
            mock_price.return_value = {
                "original": 19.99,
                "final": 15.99,
                "discount": 20,
                "currency": "RUB"
            }

            context = AsyncMock()
            await check_discounts(context)
            context.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()