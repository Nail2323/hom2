import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from utils.helpers import logger


class PairState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.is_waiting_for_candle = False
        self.wait_until_time: Optional[datetime] = None
        self.timeframe: Optional[str] = None

    def set_waiting(self, timeframe: str):
        """Устанавливает состояние ожидания свечи для пары"""
        self.is_waiting_for_candle = True
        self.timeframe = timeframe
        
        # Рассчитываем время ожидания в зависимости от таймфрейма
        current_time = datetime.now()
        
        if timeframe == "1m":
            next_minute = (current_time.minute + 1) % 60
            if next_minute == 0:
                next_hour = (current_time.hour + 1) % 24
                if current_time.hour == 23 and next_hour == 0:
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
            else:
                self.wait_until_time = current_time.replace(
                    minute=next_minute, 
                    second=0, 
                    microsecond=0
                )
        elif timeframe == "3m":
            remainder = current_time.minute % 3
            next_minute = current_time.minute + (3 - remainder)
            if next_minute >= 60:
                next_hour = (current_time.hour + 1) % 24
                if current_time.hour == 23 and next_hour == 0:
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
            else:
                self.wait_until_time = current_time.replace(
                    minute=next_minute, 
                    second=0, 
                    microsecond=0
                )
        elif timeframe == "5m":
            remainder = current_time.minute % 5
            next_minute = current_time.minute + (5 - remainder)
            if next_minute >= 60:
                next_hour = (current_time.hour + 1) % 24
                if current_time.hour == 23 and next_hour == 0:
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
            else:
                self.wait_until_time = current_time.replace(
                    minute=next_minute, 
                    second=0, 
                    microsecond=0
                )
        elif timeframe in ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]:
            # Для 15m используем логику из main.py
            if timeframe == "15m":
                remainder = current_time.minute % 15
                next_minute = current_time.minute + (15 - remainder)
            elif timeframe == "30m":
                remainder = current_time.minute % 30
                next_minute = current_time.minute + (30 - remainder)
            elif timeframe == "1h":
                next_minute = 0
                next_hour = (current_time.hour + 1) % 24
                if current_time.hour == 23:
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                    return
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                    return
            elif timeframe == "2h":
                next_minute = 0
                next_hour = current_time.hour + 2
                if next_hour >= 24:
                    next_hour %= 24
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                return
            elif timeframe == "4h":
                next_minute = 0
                next_hour = current_time.hour + 4
                if next_hour >= 24:
                    next_hour %= 24
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                return
            elif timeframe == "6h":
                next_minute = 0
                next_hour = current_time.hour + 6
                if next_hour >= 24:
                    next_hour %= 24
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                return
            elif timeframe == "12h":
                next_minute = 0
                next_hour = current_time.hour + 12
                if next_hour >= 24:
                    next_hour %= 24
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute, 
                        second=0, 
                        microsecond=0
                    )
                return
            elif timeframe == "1d":
                next_minute = 0
                next_hour = 0
                next_date = current_time.date() + timedelta(days=1)
                self.wait_until_time = current_time.replace(
                    year=next_date.year, 
                    month=next_date.month, 
                    day=next_date.day,
                    hour=next_hour, 
                    minute=next_minute, 
                    second=0, 
                    microsecond=0
                )
                return
            else:
                # По умолчанию для 15m
                remainder = current_time.minute % 15
                next_minute = current_time.minute + (15 - remainder)
            
            if next_minute >= 60:
                next_hour = (current_time.hour + 1) % 24
                if current_time.hour == 23 and next_hour == 0:
                    next_date = current_time.date() + timedelta(days=1)
                    self.wait_until_time = current_time.replace(
                        year=next_date.year, 
                        month=next_date.month, 
                        day=next_date.day,
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
                else:
                    self.wait_until_time = current_time.replace(
                        hour=next_hour, 
                        minute=next_minute % 60, 
                        second=0, 
                        microsecond=0
                    )
            else:
                self.wait_until_time = current_time.replace(
                    minute=next_minute, 
                    second=0, 
                    microsecond=0
                )

    def is_ready_for_analysis(self) -> bool:
        """Проверяет, готова ли пара к анализу (не в состоянии ожидания или время ожидания прошло)"""
        if not self.is_waiting_for_candle:
            return True
        if self.wait_until_time and datetime.now() >= self.wait_until_time:
            # Время ожидания прошло, сбрасываем состояние
            self.is_waiting_for_candle = False
            self.wait_until_time = None
            self.timeframe = None
            return True
        return False

    def reset_wait(self):
        """Сбрасывает состояние ожидания"""
        self.is_waiting_for_candle = False
        self.wait_until_time = None
        self.timeframe = None


class PairStateManager:
    def __init__(self):
        self.pairs: Dict[str, PairState] = {}
        self.lock = asyncio.Lock()  # Для потокобезопасности

    def get_pair_state(self, symbol: str) -> PairState:
        """Получает или создает состояние для пары"""
        if symbol not in self.pairs:
            self.pairs[symbol] = PairState(symbol)
        return self.pairs[symbol]

    def set_pair_waiting(self, symbol: str, timeframe: str):
        """Устанавливает состояние ожидания для пары"""
        pair_state = self.get_pair_state(symbol)
        pair_state.set_waiting(timeframe)
        logger.info(f"✅ Пара {symbol} установлена в состояние ожидания до {pair_state.wait_until_time} ({timeframe})")

    def is_pair_ready_for_analysis(self, symbol: str) -> bool:
        """Проверяет, готова ли пара к анализу"""
        pair_state = self.get_pair_state(symbol)
        return pair_state.is_ready_for_analysis()

    def get_all_ready_pairs(self, all_symbols: Set[str]) -> Set[str]:
        """Возвращает список всех пар, готовых к анализу"""
        ready_pairs = set()
        for symbol in all_symbols:
            if self.is_pair_ready_for_analysis(symbol):
                ready_pairs.add(symbol)
        return ready_pairs

    def reset_pair_wait(self, symbol: str):
        """Сбрасывает состояние ожидания для пары"""
        pair_state = self.get_pair_state(symbol)
        pair_state.reset_wait()
        logger.info(f"🔄 Сброшено состояние ожидания для пары {symbol}")


# Глобальный экземпляр менеджера состояний пар
pair_state_manager = PairStateManager()