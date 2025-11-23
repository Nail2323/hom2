# tools/wait_for_next_candle_tool.py
import asyncio
from .base_tool import BaseTool
from typing import Dict, Any, Optional
from utils.pair_state_manager import pair_state_manager

class WaitForNextCandleTool(BaseTool):
    @property
    def name(self):
        return "wait_for_next_candle"

    @property
    def description(self):
        return "Приостанавливает выполнение текущего цикла анализа до закрытия следующей 15-минутной свечи для указанного символа. Используется, когда role: user принял решение не совершать никаких действий в текущем цикле и хочет дождаться обновления рыночных данных."

    @property
    def parameters(self):
        return {
            "symbol": {
                "type": "string",
                "description": "Торговая пара, например 'DOGEUSDT'.",
                "pattern": "^[A-Z0-9]+$"
            },
            "timeframe": {
                "type": "string",
                "description": "Таймфрейм свечи, ожидаемой для продолжения анализа (по умолчанию '15m').",
                "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
                "default": "15m"
            }
        }

    @property
    def required_parameters(self):
        return ["symbol"]

    async def execute(self, symbol: str, timeframe: str = "15m") -> Dict[str, Any]:
        """
        Этот инструмент устанавливает состояние ожидания для указанной пары.
        """
        # Устанавливаем состояние ожидания для пары
        pair_state_manager.set_pair_waiting(symbol, timeframe)
        
        return {
            "status": "waiting_for_next_candle",
            "symbol": symbol,
            "timeframe": timeframe,
            "message": f"ИИ принял решение ждать закрытия следующей {timeframe} свечи для {symbol} перед следующим анализом."
        }
