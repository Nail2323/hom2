# main.py
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

    print("--- Bybit Bot с DeepSeek и инструментами (ОДНОМОДЕЛЬНЫЙ АВТОНОМНЫЙ РЕЖИМ) ---")
    print("Запускается режим ожидания по таймеру (следующая 15-минутная отметка)...")
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
    should_wait_for_time = True # <-- НОВОЕ: флаг для ожидания времени
    next_analysis_time = None # <-- Время следующего запуска анализа

    try:
        while True:
            current_time = datetime.now()

            if should_wait_for_time:
                if next_analysis_time is None:
                    # Если время ещё не рассчитано (первый запуск или после завершения анализа без 'ждать')
                    # Рассчитываем ближайшую 15-минутную отметку от *текущего* времени
                    # Например, если сейчас 16:03 -> следующая отметка 16:15
                    # Если сейчас 16:15 -> следующая отметка 16:30
                    minutes = current_time.minute
                    # Находим остаток от деления на 15
                    remainder = minutes % 15
                    # Рассчитываем минуту следующей отметки
                    next_minute = minutes + (15 - remainder)
                    # Если получилось >= 60, переходим на следующий час
                    if next_minute >= 60:
                        next_hour = (current_time.hour + 1) % 24 # Обработка перехода на следующий день
                        next_minute = next_minute % 60
                        # Если перешли на следующий день, нужно обновить дату
                        if current_time.hour == 23 and next_hour == 0:
                            next_date = current_time.date() + timedelta(days=1)
                            next_analysis_time = current_time.replace(year=next_date.year, month=next_date.month, day=next_date.day, hour=next_hour, minute=next_minute, second=0, microsecond=0)
                        else:
                            next_analysis_time = current_time.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
                    else:
                        next_analysis_time = current_time.replace(minute=next_minute, second=0, microsecond=0)

                    print(f"⏳ Следующий запуск анализа ИИ запланирован на {next_analysis_time.strftime('%H:%M:%S')}")

                # Ждём наступления времени
                time_to_sleep = (next_analysis_time - current_time).total_seconds()
                if time_to_sleep > 0:
                    print(f"💤 Ожидание до {next_analysis_time.strftime('%H:%M:%S')} (~{time_to_sleep:.1f} секунд)...")
                    # Ждём до наступления времени, проверяя прерывание каждые 10 секунд
                    while time_to_sleep > 0:
                        sleep_duration = min(10, time_to_sleep)
                        await asyncio.sleep(sleep_duration)
                        time_to_sleep -= sleep_duration
                        current_time = datetime.now()
                        if current_time >= next_analysis_time:
                            print(f"⏰ Время {next_analysis_time.strftime('%H:%M:%S')} наступило. Готовимся к запуску ИИ.")
                            break # Выходим из внутреннего цикла ожидания
                else:
                    # Это может случиться, если вычисления заняли немного времени и текущее время уже >= next_analysis_time
                    print(f"⏰ Время {next_analysis_time.strftime('%H:%M:%S')} уже наступило (по расчёту). Продолжаем.")

                # Сбрасываем флаг ожидания, чтобы запустить анализ
                should_wait_for_time = False
                # Сбрасываем время, чтобы при следующем вхождении в `if should_wait_for_time` оно пересчиталось
                next_analysis_time = None

            # Если флаг ожидания сброшен, запускаем анализ
            if not should_wait_for_time:
                print("🤖 Запуск ПОЛНОГО цикла анализа ИИ...")
                # Подготовим фиктивную информацию о свече или None
                # Так как мы не ждём конкретную свечу, передаём None или минимальные данные
                # Важно, чтобы run_full_analysis_cycle_until_wait мог работать без конкретных данных свечи,
                # если ИИ просто продолжает анализ с места остановки или ожидает команды от себя же.
                # Предположим, что он может принимать None или словарь с фиктивными данными.
                # Лучше всего передать None, если в run_full_analysis_cycle_until_wait обработка None предусмотрена.
                # Если нет, можно передать минимальный словарь, например:
                fake_candle_info = {
                    'symbol': 'DOGEUSDT',
                    'interval': '15m', # Указываем, что это "ожидаемый" интервал
                    'open': 0.0,
                    'high': 0.0,
                    'low': 0.0,
                    'close': 0.0,
                    'volume': 0.0,
                    'turnover': 0.0,
                    'timestamp': int(datetime.now().timestamp() * 1000), # Текущее время
                    'start_time': int((datetime.now() - timedelta(minutes=15)).timestamp() * 1000), # Примерное начало
                    'end_time': int(datetime.now().timestamp() * 1000)
                }

                # Запускаем ПОЛНЫЙ цикл анализа ИИ
                should_wait_for_next_candle = await client.run_full_analysis_cycle_until_wait(candle_info=fake_candle_info)

                # Проверяем, попросил ли ИИ ждать следующей свечи (через вызов инструмента wait_for_next_candle)
                if should_wait_for_next_candle:
                    print("⏳ ИИ принял решение ждать следующей 15-минутной отметки по времени.")
                    # Устанавливаем флаг, чтобы вернуться к ожиданию времени
                    should_wait_for_time = True
                    # next_analysis_time будет рассчитано в следующей итерации цикла при проверке if should_wait_for_time
                else:
                    print("✅ Анализ завершен. Ждем следующего решения ИИ или наступления времени...")
                    # Даже если ИИ не сказал "ждать", мы всё равно возвращаемся к ожиданию времени
                    # Это потому что основная логика - "цикл по времени"
                    should_wait_for_time = True
                    # next_analysis_time будет рассчитано в следующей итерации цикла при проверке if should_wait_for_time


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
            logger.error(f"❌ Ошибка при закрытии приватного потока: {e}")
        print("👋 До свидания!")


if __name__ == "__main__":
    asyncio.run(main())