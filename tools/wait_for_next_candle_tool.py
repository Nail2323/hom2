# tools/wait_for_next_candle_tool.py
import asyncio
from .base_tool import BaseTool
from typing import Dict, Any, Optional

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
        Этот инструмент не выполняет реального действия, а просто сигнализирует,
        что цикл анализа должен быть приостановлен.
        Возвращаем специальный флаг, который можно проверить в run_single_analysis_cycle.
        """
        # Возвращаем структурированный ответ, который можно использовать в логике.
        # Важно: этот инструмент должен быть *последним* вызванным инструментом в цикле,
        # чтобы триггер сработал.
        return {
            "status": "waiting_for_next_candle",
            "symbol": symbol,
            "timeframe": timeframe,
            "message": f"ИИ принял решение ждать закрытия следующей {timeframe} свечи для {symbol} перед следующим анализом."
        }
