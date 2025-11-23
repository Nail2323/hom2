import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools.bybit_wrapper import BybitWrapper
from utils.globals import initialize_global_services
from utils.deepseek_client import DeepSeekClient
from config import DEEPSEEK_CHAT_MODEL, BYBIT_API_KEY, BYBIT_API_SECRET
from utils.helpers import logger

# Импортируем асинхронный WebSocket напрямую
from pybit.unified_trading import WebSocket

# --- ГЛОБАЛЬНАЯ ПЕРЕМЕННАЯ ДЛЯ ХРАНЕНИЯ ЦИКЛА СОБЫТИЙ ---
MAIN_EVENT_LOOP = None

# --- СИНХРОННЫЕ ОБРАБОТЧИКИ ДЛЯ PYBIT (для приватных и других публичных данных) ---

def handle_position_sync(message):
    """Синхронный обработчик позиций."""
    # Если нужно обновлять состояние позиции в реальном времени вне основного цикла ИИ
    # можно использовать asyncio.run_coroutine_threadsafe здесь
    # Но для логики "ждать до следующей 15-минутной отметки" это может быть не нужно.
    # Пока что просто логируем.
    print(f"🔄 Обновление позиции: {message.get('data', [])[:1]}") # Печатаем первые элементы, если есть

def handle_order_sync(message):
    """Синхронный обработчик ордеров."""
    print(f"🔄 Обновление ордера: {message.get('data', [])[:1]}")

def handle_execution_sync(message):
    """Синхронный обработчик исполнений."""
    print(f"✅ Исполнение: {message.get('data', [])[:1]}")

def handle_wallet_sync(message):
    """Синхронный обработчик кошелька."""
    print(f"💰 Обновление кошелька: {message.get('data', {})}")

# --- ОБРАБОТЧИКИ ЛИКВИДАЦИЙ ---

def handle_all_liquidation_sync(message):
    """Синхронный обработчик всех ликвидаций (точка входа для pybit)."""
    # Используем глобальный цикл событий, который будет установлен в main
    global MAIN_EVENT_LOOP
    if MAIN_EVENT_LOOP:
        # Создаем задачу для асинхронной функции
        asyncio.run_coroutine_threadsafe(_handle_all_liquidation_async(message), MAIN_EVENT_LOOP)
    else:
        logger.warning("handle_all_liquidation_sync: Цикл событий НЕ НАЙДЕН (MAIN_EVENT_LOOP is None). Событие НЕ обработано.")

async def _handle_all_liquidation_async(message):
    """Внутренняя асинхронная логика для обработки ликвидаций."""
    topic = message.get('topic')
    liquidations_list = message.get('data', [])
    if topic and liquidations_list:
        for liquidation in liquidations_list:
            timestamp = liquidation.get('T', 'Н/Д')
            liq_symbol = liquidation.get('s', 'Н/Д')
            side = liquidation.get('S', 'Н/Д')
            volume = liquidation.get('v', 'Н/Д')
            price = liquidation.get('p', 'Н/Д')

            # Выводим информацию в консоль
            readable_time = format_readable_time(timestamp) if timestamp != 'Н/Д' else 'Н/Д'
            print(f"🔥 Ликвидация: {liq_symbol} {side} {volume} по {price} в {readable_time}")

            # Сохраняем ликвидацию в JSON файл (используем функцию из старого файла)
            # Предположим, функция save_liquidation_to_file находится в utils или в handlers
            # Импортируем её
            from websocet.handlers.liquidations import save_liquidation_to_file
            save_liquidation_to_file(liquidation, liq_symbol)

            # (Опционально) Отправить событие в event_queue, если другие части системы его слушают
            # await event_queue.put({
            #     'type': EventType.LIQUIDATION_DETECTED,
            #     'data': {
            #         'symbol': liq_symbol,
            #         'side': side,
            #         'volume': volume,
            #         'price': price,
            #         'timestamp': timestamp,
            #         'readable_time': readable_time
            #     }
            # })


# --- ФУНКЦИЯ ФОРМАТИРОВАНИЯ ВРЕМЕНИ (копируем из старого файла или utils) ---
def format_readable_time(timestamp_ms):
    """Преобразует timestamp (в мс) в читаемый формат ДД.ММ.ГГГГ ЧЧ:ММ"""
    if timestamp_ms == 'N/A' or not isinstance(timestamp_ms, (int, float)):
        return 'N/A'
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime('%d.%m.%Y %H:%M')


async def main():
    global MAIN_EVENT_LOOP # <-- Объявляем, что будем использовать глобальную переменную
    MAIN_EVENT_LOOP = asyncio.get_running_loop() # <-- Сохраняем текущий цикл событий
    print(f"✅ Цикл событий сохранён в MAIN_EVENT_LOOP.")

    # === ИНИЦИАЛИЗАЦИЯ BYBIT И ГЛОБАЛЬНЫХ СЕРВИСОВ ===
    try:
        print("🔧 Инициализация BybitWrapper и глобальных сервисов...")
        bybit_client = BybitWrapper()
        initialize_global_services(bybit_client.ccxt_session, bybit_client)
        print("✅ Глобальные сервисы инициализированы")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        return

    # === ИНИЦИАЛИЗАЦИЯ КЛИЕНТА ===
    try:
        client = DeepSeekClient()
        logger.info(f"Клиент DeepSeek инициализирован с моделью: {DEEPSEEK_CHAT_MODEL}")
    except Exception as e:
        logger.error(f"❌ Не удалось инициализировать клиента DeepSeek: {e}")
        return

    # === ИНИЦИАЛИЗАЦИЯ СПИСКА ПАР ===
    # В реальном приложении список пар можно получить динамически
    trading_pairs = {"DOGEUSDT"}  # Можно добавить больше пар

    print("--- Bybit Bot с DeepSeek и инструментами (ОДНОМОДЕЛЬНЫЙ АВТОНОМНЫЙ РЕЖИМ) ---")
    print(f"Запускается режим анализа для пар: {', '.join(trading_pairs)}")
    print("Для остановки нажмите Ctrl+C")
    print("----------------------------")

    # === ИНИЦИАЛИЗАЦИЯ ВЕБСОКЕТА ===
    print("🔌 Подключение к публичному и приватному потокам (асинхронно)...")
    try:
        # --- ПУБЛИЧНЫЙ ВЕБСОКЕТ ---
        public_ws = WebSocket(
            testnet=False,
            channel_type="linear",
            # Убираем подписку на kline.15.DOGEUSDT
            # ping_interval=20,
            # ping_timeout=10,
            # restart_on_error=True,
            # retries=10
        )
        await asyncio.sleep(2) # Асинхронный sleep
        print("✅ Публичный поток подключен.")

        # --- ПОДПИСКИ НА ПУБЛИЧНЫЕ ДАННЫЕ (БЕЗ СВЕЧЕЙ) ---
        # Подписка на ликвидации
        try:
            public_ws.all_liquidation_stream('DOGEUSDT', handle_all_liquidation_sync) # Передаём синхронный обработчик
            print("✅ Подписка на ликвидации DOGEUSDT выполнена")
        except Exception as e:
            logger.error(f"❌ Ошибка при подписке на ликвидации: {e}")

        # Подписка на другие публичные данные (например, тикеры, если нужно)
        # public_ws.ticker_stream('DOGEUSDT', handle_ticker_sync)
        # print("✅ Подписка на тикер DOGEUSDT выполнена")

    except Exception as e:
        logger.error(f"❌ Ошибка при подключении публичного WebSocket: {e}")
        return # Если публичный не подключился, дальше смысла нет

    # --- ПРИВАТНЫЕ ДАННЫЕ ---
    private_ws = None
    if BYBIT_API_KEY and BYBIT_API_SECRET and BYBIT_API_KEY != "YOUR_API_KEY":
        print("🔐 Подключение к приватному потоку...")
        try:
            private_ws = WebSocket(
                testnet=False,
                channel_type="private",
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
                # ping_interval=20,
                # ping_timeout=10,
                # restart_on_error=True,
                # retries=10
            )
            await asyncio.sleep(2) # Асинхронный sleep
            print("✅ Приватный поток подключен.")

            # Подписки на приватные данные
            try:
                private_ws.position_stream(handle_position_sync) # Передаём синхронный обработчик
                print("✅ Подписка на поток позиций выполнена")
            except Exception as e:
                logger.error(f"❌ Ошибка при подписке на позиции: {e}")

            try:
                private_ws.order_stream(handle_order_sync) # Передаём синхронный обработчик
                print("✅ Подписка на поток ордеров выполнена")
            except Exception as e:
                logger.error(f"❌ Ошибка при подписке на ордера: {e}")

            try:
                private_ws.execution_stream(handle_execution_sync) # Передаём синхронный обработчик
                print("✅ Подписка на поток исполнений выполнена")
            except Exception as e:
                logger.error(f"❌ Ошибка при подписке на исполнения: {e}")

            try:
                private_ws.wallet_stream(handle_wallet_sync) # Передаём синхронный обработчик
                print("✅ Подписка на поток кошелька выполнена")
            except Exception as e:
                logger.error(f"❌ Ошибка при подписке на кошелек: {e}")

        except Exception as e:
            logger.error(f"❌ Не удалось подключиться или подписаться на приватный поток: {e}")
            private_ws = None
    else:
        print("⚠️ API ключи не установлены или имеют значения по умолчанию. Приватный поток пропущен.")

    print("🟢 Все слушатели WebSocket запущены. Ожидание данных...")
    print("Нажмите Ctrl+C для остановки.")

    # === ЦИКЛ РАБОТЫ С ИИ ПО ТАЙМЕРУ ===
    # Упрощаем цикл - теперь он будет работать непрерывно, проверяя каждую пару на готовность к анализу
    try:
        while True:
            current_time = datetime.now()
            
            # Получаем список всех пар, готовых к анализу
            ready_pairs = client.pair_state_manager.get_all_ready_pairs(trading_pairs)
            
            if ready_pairs:
                print(f"🤖 Найдено {len(ready_pairs)} пар, готовых к анализу: {', '.join(ready_pairs)}")
                
                # Запускаем анализ для каждой готовой пары
                for symbol in ready_pairs:
                    print(f"🔬 Запуск анализа для пары {symbol}")
                    
                    # Подготовим фиктивную информацию о свече
                    fake_candle_info = {
                        'symbol': symbol,
                        'interval': '15m', # Указываем, что это "ожидаемый" интервал
                        'open': 0.0,
                        'high': 0.0,
                        'low': 0.0,
                        'close': 0.0,
                        'volume': 0.0,
                        'turnover': 0.0,
                        'timestamp': int(current_time.timestamp() * 1000), # Текущее время
                        'start_time': int((current_time - timedelta(minutes=15)).timestamp() * 1000), # Примерное начало
                        'end_time': int(current_time.timestamp() * 1000)
                    }

                    # Запускаем ПОЛНЫЙ цикл анализа ИИ для конкретной пары
                    should_wait_for_next_candle = await client.run_full_analysis_cycle_until_wait(
                        symbol=symbol, 
                        candle_info=fake_candle_info
                    )

                    # Если ИИ запросил ожидание для этой пары, состояние уже установлено в инструменте
                    if should_wait_for_next_candle:
                        print(f"⏳ ИИ принял решение ждать следующей свечи для пары {symbol}")
                    else:
                        print(f"✅ Анализ для пары {symbol} завершен")
            else:
                print("🔄 Нет пар, готовых к анализу. Проверка состояния пар...")
            
            # Ждем перед следующей проверкой
            print("💤 Ожидание 30 секунд перед следующей проверкой...")
            await asyncio.sleep(30)

    except KeyboardInterrupt:
        print("\n🛑 Получен сигнал прерывания. Остановка...")
    finally:
        # Корректное завершение работы вебсокетов
        print("🧹 Закрытие соединений WebSocket...")
        try:
            if public_ws:
                public_ws.exit()
                print("✅ Публичный поток закрыт.")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии публичного потока: {e}")
        try:
            if private_ws:
                private_ws.exit()
                print("✅ Приватный поток закрыт.")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытия приватного потока: {e}")
        print("👋 До свидания!")


if __name__ == "__main__":
    asyncio.run(main())