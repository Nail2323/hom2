# utils/deepseek_client.py
import asyncio
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from openai import AsyncOpenAI
from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
    DEEPSEEK_CHAT_MODEL,
    DEEPSEEK_REASONER_MODEL,
    MAX_CONTEXT_TOKENS
)
from tools import get_all_tools
from utils.helpers import logger
from utils.context_manager import (
    save_context_to_file, load_context_from_file,
    truncate_context_adaptive, count_tokens_in_messages,
    format_messages_for_deepseek, truncate_context_by_cycles,
)


class DeepSeekClient:
    def __init__(self):
        self.model = DEEPSEEK_CHAT_MODEL
        self.client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL
        )
        # --- Добавляем рассуждающую модель ---
        self.reasoner_model = DEEPSEEK_REASONER_MODEL
        self.reasoner_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL
        )
        # -------------------------------
        self._verify_tools_initialization()
        self.tools = get_all_tools()
        self.tool_schemas = [tool.to_function_definition() for tool in self.tools]
        self.tool_map = {tool.name: tool for tool in self.tools}
        from utils.reasoner_context_manager import load_reasoner_context_from_file
        self.reasoner_context, _ = load_reasoner_context_from_file()
        
        # Импортируем менеджер состояний пар
        from utils.pair_state_manager import pair_state_manager
        self.pair_state_manager = pair_state_manager
        self.token_usage = {
            'total_prompt_tokens': 0,
            'total_completion_tokens': 0,
            'total_tokens': 0
        }

        logger.info(f"DeepSeek клиент инициализирован с моделями: {self.model}, {self.reasoner_model}")

    def _verify_tools_initialization(self):
        import utils.globals as globals_module
        logger.info("🔍 Проверка инициализации инструментов...")
        required_services = {
            "bybit_wrapper_instance": globals_module.bybit_wrapper_instance,
            "ccxt_exchange_client": globals_module.ccxt_exchange_client,
            "chart_patterns_manager_instance": globals_module.chart_patterns_manager_instance
        }
        missing = []
        for name, service in required_services.items():
            if service is None:
                missing.append(name)
                logger.error(f"❌ {name} не инициализирован")
            else:
                logger.info(f"✅ {name} готов")
        if missing:
            logger.warning(f"⚠️ Не инициализированы: {missing}")

    def _log_token_usage(self, usage: Any, stage: str = ""):
        if not usage:
            return
        try:
            prompt = getattr(usage, 'prompt_tokens', 0)
            completion = getattr(usage, 'completion_tokens', 0)
            total = getattr(usage, 'total_tokens', 0)
            self.token_usage['total_prompt_tokens'] += prompt
            self.token_usage['total_completion_tokens'] += completion
            self.token_usage['total_tokens'] += total
            logger.info(f"🔢 [Tokens {stage}] Prompt: {prompt}, Completion: {completion}, Total: {total}")
            print(f"\n[Tokens {stage}] Prompt: {prompt}, Completion: {completion}, Total: {total}\n")
        except Exception as e:
            logger.warning(f"Ошибка при логировании токенов: {e}")

    def _clean_incomplete_tool_calls(self, messages: list) -> list:
        cleaned = []
        pending = set()
        for msg in messages:
            if msg['role'] == 'assistant' and 'tool_calls' in msg:
                cleaned.append(msg)
                for call in msg['tool_calls']:
                    pending.add(call['id'])
            elif msg['role'] == 'tool':
                if msg['tool_call_id'] in pending:
                    cleaned.append(msg)
                    pending.remove(msg['tool_call_id'])
                else:
                    logger.warning(f"Лишний tool response: {msg['tool_call_id']}")
            else:
                cleaned.append(msg)
        if pending:
            logger.warning(f"Незавершённые tool_calls: {pending}. Очищаем.")
            cleaned = [m for m in cleaned if not (
                    m.get('role') == 'assistant' and 'tool_calls' in m and
                    any(c['id'] in pending for c in m['tool_calls'])
            )]
        return cleaned

    async def _execute_tool(self, tool_instance, function_args: dict, tool_call_id: str) -> dict:
        try:
            logger.info(f"⚡ Выполнение инструмента {tool_instance.name}...")
            result = await tool_instance.execute(**function_args)
            logger.info(f"✅ Инструмент {tool_instance.name} выполнен")
            return {
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'content': json.dumps(result) if not isinstance(result, str) else result
            }
        except Exception as e:
            logger.error(f"❌ Ошибка в {tool_instance.name}: {e}")
            return {
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'content': json.dumps({"error": str(e)})
            }

    # ✅ Возвращаем ПОЛНОЕ сообщение ассистента (включая tool_calls)
    async def call_model_with_tools(self, messages: list) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        formatted = format_messages_for_deepseek(messages)
        logger.info("🔄 Вызов модели с инструментами...")
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=formatted,
                tools=self.tool_schemas,
                tool_choice="auto"
            )
        except Exception as e:
            logger.error(f"❌ Ошибка вызова модели: {e}")
            error_msg = {'role': 'assistant', 'content': f"Ошибка: {str(e)}", 'tool_calls': []}
            return error_msg, []

        self._log_token_usage(response.usage)
        msg = response.choices[0].message

        assistant_msg = {
            'role': msg.role,
            'content': msg.content or '',
            'tool_calls': []
        }

        if msg.tool_calls:
            assistant_msg['tool_calls'] = [
                {
                    'id': call.id,
                    'type': call.type,
                    'function': {
                        'name': call.function.name,
                        'arguments': call.function.arguments
                    }
                } for call in msg.tool_calls
            ]

        tool_results = []
        if msg.tool_calls:
            logger.info(f"🛠️ Модель хочет вызвать {len(msg.tool_calls)} инструментов.")
            tasks = []
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                logger.info(f"🔧 Вызов: {name} с {args}")
                if tool := self.tool_map.get(name):
                    tasks.append(self._execute_tool(tool, args, tool_call.id))
                else:
                    logger.error(f"❌ Инструмент не найден: {name}")
                    tool_results.append({
                        'role': 'tool',
                        'tool_call_id': tool_call.id,
                        'content': json.dumps({"error": f"Инструмент '{name}' не найден"})
                    })
            if tasks:
                tool_results.extend(await asyncio.gather(*tasks))

        print(f"\n[🤖 Ответ трейдера]:\n{assistant_msg['content'] or '(без текста)'}\n")
        return assistant_msg, tool_results

    # --- ОБНОВЛЁННАЯ ФУНКЦИЯ: вызов рассуждающей модели с её полным контекстом ---
    async def call_reasoner_model(
        self,
        system_prompt_for_reasoner: str,
        assistant_content: str,
        tool_results: List[Dict[str, Any]]
    ) -> str:
        """
        Отправляет данные для рассуждения, включая историю.
        """
        logger.info("🧠 Подготовка данных для рассуждающей модели с историей...")

        # 1. Гарантируем, что assistant_content — строка
        assistant_text = assistant_content or "(инструментальная модель не предоставила пояснений)"

        # 2. Извлекаем только content из результатов инструментов
        tool_contents = []
        for tr in tool_results:
            content = tr.get('content', '')
            # Опционально: делаем JSON читаемым
            try:
                parsed = json.loads(content)
                content = json.dumps(parsed, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
            tool_contents.append(content)

        tool_results_text = "\n\n".join(tool_contents) if tool_contents else "(результаты инструментов отсутствуют)"

        # 3. Формируем единый запрос от инструментальной модели для reasoner'а
        user_message_content = (
            f"### Пояснение от трейдера:\n{assistant_text}\n\n"
            f"### Данные от инструментов:\n{tool_results_text}"
        )

        # 4. Собираем полные сообщения для reasoner'а
        messages_for_reasoner = []

        # Добавляем системный промпт как первое сообщение, если его ещё нет в контексте
        if not self.reasoner_context or self.reasoner_context[0].get('role') != 'system':
            messages_for_reasoner.append({"role": "system", "content": system_prompt_for_reasoner})

        # Добавляем усечённый старый контекст (если есть)
        if self.reasoner_context:
            from utils.reasoner_context_manager import (
                truncate_reasoner_context_by_cycles,
                truncate_reasoner_context,  # по токенам
                save_reasoner_context_to_file
            )

            # Шаг 1: оставляем последние 10 циклов
            truncated = truncate_reasoner_context_by_cycles(self.reasoner_context, max_cycles=10)
            # Шаг 2: на всякий случай — проверяем по токенам (например, лимит 90k)
            truncated = truncate_reasoner_context(truncated, max_tokens=90000)

            self.reasoner_context = truncated
            save_reasoner_context_to_file(self.reasoner_context, iteration=0)
            messages_for_reasoner.extend(truncated)

        # Добавляем текущий запрос (от инструментальной модели)
        messages_for_reasoner.append({
            "role": "user",
            "content": user_message_content
        })

        logger.info("🧠 Вызов рассуждающей модели с историей...")
        try:
            response = await self.reasoner_client.chat.completions.create(
                model=self.reasoner_model,
                messages=messages_for_reasoner,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка вызова рассуждающей модели: {e}")
            return f"Ошибка рассуждающей модели: {str(e)}"

        self._log_token_usage(response.usage, stage="reasoner")
        final_content = response.choices[0].message.content or "(пустой ответ)"

        # Проверяем, есть ли у ответа рассуждения (например, если reasoner модель поддерживает reasoning_content)
        reasoning_content = getattr(response.choices[0].message, 'reasoning_content', None)

        if reasoning_content:
            print(f"\n[🧠 Думки рассуждающей модели]:\n{reasoning_content}\n")

        print(f"\n[💡 Ответ рассуждающей модели]:\n{final_content}\n")
        return final_content

    async def run_autonomous_tool_cycle(self, initial_prompt: str):
        messages, iteration = load_context_from_file()
        if not messages:
            from utils.system_prompt import generate_system_prompt
            system_prompt = generate_system_prompt()
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': initial_prompt}
            ]
            iteration = 0

        print(f"\n--- 🚀 Запуск ДВУХМОДЕЛЬНОГО автономного цикла ---")
        print(f"📅 Итерация: {iteration}")
        print("🛑 Остановка по Ctrl+C")

        while True:
            iteration += 1
            logger.info(f"--- 🔄 Итерация {iteration} ---")
            try:

                # Сначала по циклам, потом по токенам — на всякий случай
                messages = truncate_context_by_cycles(messages, max_cycles=8)
                messages = truncate_context_adaptive(messages, max_tokens=900000)  # или используй простое усечение по токенам
                messages = self._clean_incomplete_tool_calls(messages)
                estimated = count_tokens_in_messages(messages)
                logger.info(f"📊 Токены перед итерацией {iteration}: ~{estimated}")
                print(f"\n--- 🔄 Итерация {iteration} ---")
                print(f"[Токены: ~{estimated} / 100000]\n")

                # --- ШАГ 1: вызов инструментальной модели ---
                assistant_msg, tool_results = await self.call_model_with_tools(messages)

                # --- ШАГ 2: Подготовка данных ДЛЯ REASONER'А ---
                from utils.system_prompt_reasoner import generate_reasoner_system_prompt
                reasoner_system_prompt = generate_reasoner_system_prompt()

                # Передаём ТОЛЬКО content (без tool_calls!)
                assistant_content_only = assistant_msg.get('content', '')  # ← именно это!

                # --- ШАГ 3: Вызов reasoner'а ---
                reasoner_response = await self.call_reasoner_model(
                    system_prompt_for_reasoner=reasoner_system_prompt,
                    assistant_content=assistant_content_only,
                    tool_results=tool_results  # ← уже содержит только результаты
                )

                # --- ФОРМИРУЕМ ТЕКУЩИЙ ВХОД ДЛЯ REASONER'А (user) ---
                # (Повторяем логику из call_reasoner_model для формирования user_message_content)
                assistant_text = assistant_msg.get('content', '') or "(инструментальная модель не предоставила пояснений)"
                tool_contents = []
                for tr in tool_results:
                    content = tr.get('content', '')
                    try:
                        parsed = json.loads(content)
                        content = json.dumps(parsed, ensure_ascii=False, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    tool_contents.append(content)
                tool_results_text = "\n\n".join(tool_contents) if tool_contents else "(результаты инструментов отсутствуют)"
                user_message_content = (
                    f"### Пояснение от трейдера:\n{assistant_text}\n\n"
                    f"### Данные от инструментов:\n{tool_results_text}"
                )

                # --- СОХРАНЯЕМ user и assistant в reasoner_context ---
                # Если системный промпт ещё не добавлен в этот сеанс (например, после перезапуска)
                if not self.reasoner_context or self.reasoner_context[0].get('role') != 'system':
                    self.reasoner_context.append({"role": "system", "content": reasoner_system_prompt})

                self.reasoner_context.append({
                    "role": "user",
                    "content": user_message_content
                })
                self.reasoner_context.append({
                    "role": "assistant",
                    "content": reasoner_response
                })

                # --- СОХРАНЯЕМ КОНТЕКСТ РАССУЖДЕНИЙ ---
                from utils.reasoner_context_manager import save_reasoner_context_to_file
                save_reasoner_context_to_file(self.reasoner_context, iteration)

                # --- ШАГ 4: добавляем всё в основной контекст ---
                # assistant -> tool -> user (с ответом reasoner)
                messages.append(assistant_msg)
                messages.extend(tool_results)
                messages.append({
                    'role': 'user',
                    'content': reasoner_response
                })

                # Сохраняем контекст
                save_context_to_file(messages, iteration)

                print(f"\n⏸️ Пауза 1 секунд...")
                await asyncio.sleep(30)

            except KeyboardInterrupt:
                logger.info("🛑 Цикл прерван.")
                print(f"\n--- 🛑 ЦИКЛ ПРЕРВАН ---")
                print(f"📊 Итоги: {self.token_usage}")
                save_context_to_file(messages, iteration)
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в итерации {iteration}: {e}")
                print(f"❌ Ошибка: {e}")
                messages = self._clean_incomplete_tool_calls(messages)
                messages.append({
                    'role': 'user',
                    'content': f"Произошла ошибка: {e}. Продолжай работу."
                })
                save_context_to_file(messages, iteration)
                await asyncio.sleep(5)

    def get_token_statistics(self) -> Dict[str, int]:
        return self.token_usage.copy()

    # --- ОБНОВЛЁННЫЙ МЕТОД: запуск одиночного цикла анализа ---
    async def run_single_analysis_cycle(self, candle_info: dict = None):
        """
        Загружает контекст, добавляет информацию о новой свече (если есть),
        выполняет одну итерацию анализа, и сохраняет обновленный контекст.
        Не входит в бесконечный цикл.
        Возвращает True, если ИИ запросил ожидание следующей свечи.
        """
        print(f"\n--- 🚀 Запуск ОДИНОЧНОГО цикла анализа ---")
        messages, iteration = load_context_from_file()
        if not messages:
            from utils.system_prompt import generate_system_prompt
            system_prompt = generate_system_prompt()
            messages = [
                {'role': 'system', 'content': system_prompt},
                # {'role': 'user', 'content': initial_prompt} # <-- УБРАНО
            ]
            iteration = 0
        else:
            # Добавляем информацию о новой свече к существующему контексту
            # Это НЕ initial_prompt, а просто информация о событии
            if candle_info:
                candle_message = f"Закрылась новая {candle_info['interval']}-минутная свеча для {candle_info['symbol']} в {candle_info['timestamp']}."
                messages.append({'role': 'user', 'content': candle_message})
            iteration += 1  # Увеличиваем номер итерации

        # Выполняем одну итерацию
        updated_messages, should_wait = await self._run_single_iteration(messages, iteration)
        print(f"--- ✅ ОДИНОЧНЫЙ цикл анализа завершен ---")
        # Контекст уже сохранен внутри _run_single_iteration
        return should_wait  # Возвращаем флаг ожидания

    # --- НОВЫЙ МЕТОД: одиночная итерация ---
    # Принимает messages, уже содержащий информацию о новой свече
    async def _run_single_iteration(self, messages: list, iteration: int) -> tuple[list, bool]:  # <-- Добавили bool
        """
        Выполняет одну итерацию анализа (вызов моделей, обработка инструментов, рассуждение).
        Возвращает обновленный список сообщений и флаг, указывающий, нужно ли ждать следующей свечи.
        """
        # Сначала по циклам, потом по токенам — на всякий случай
        messages = truncate_context_by_cycles(messages, max_cycles=8)
        messages = truncate_context_adaptive(messages,
                                             max_tokens=900000)  # или используй простое усечение по токенам
        messages = self._clean_incomplete_tool_calls(messages)
        estimated = count_tokens_in_messages(messages)
        logger.info(f"📊 Токены перед итерацией {iteration}: ~{estimated}")
        print(f"\n--- 🔄 Итерация {iteration} ---")
        print(f"[Токены: ~{estimated} / 100000]\n")

        # --- ШАГ 1: вызов инструментальной модели ---
        # Теперь в messages есть и старый контекст, и сообщение о новой свече
        assistant_msg, tool_results = await self.call_model_with_tools(messages)

        # --- ПРОВЕРКА: вызван ли wait_for_next_candle в последнем шаге? ---
        # Это работает, если wait_for_next_candle был последним вызванным инструментом.
        # Ищем его в assistant_msg['tool_calls']
        wait_for_candle = False
        if assistant_msg.get('tool_calls'):
            # Проверяем, был ли последним вызванным инструментом wait_for_next_candle
            last_tool_call = assistant_msg['tool_calls'][-1]  # Берем последний вызов
            if last_tool_call.get('function', {}).get('name') == 'wait_for_next_candle':
                # Проверяем результат вызова инструмента
                if tool_results:  # Если есть результаты инструментов
                    last_tool_result = tool_results[-1]  # Берем последний результат
                    if last_tool_result.get('role') == 'tool' and last_tool_result.get('tool_call_id') == \
                            last_tool_call['id']:
                        try:
                            result_content = json.loads(last_tool_result.get('content', '{}'))
                            if result_content.get('status') == 'waiting_for_next_candle':
                                print(
                                    f"✅ Обнаружен сигнал ожидания следующей свечи: {result_content.get('message', 'Ожидание свечи')}")
                                wait_for_candle = True
                        except json.JSONDecodeError:
                            print("⚠️ Не удалось распознать результат инструмента wait_for_next_candle.")
        # --- КОНЕЦ ПРОВЕРКИ ---

        # Если сигнал ожидания получен, НЕ вызываем рассуждающую модель и НЕ добавляем инструменты в контекст.
        # Просто возвращаем сообщение ИИ (если оно есть) и флаг.
        if wait_for_candle:
            # Добавляем только сообщение ассистента (если оно есть) и сохраняем контекст
            if assistant_msg.get('content'):
                messages.append(assistant_msg)
            # Сохраняем контекст
            save_context_to_file(messages, iteration)
            return messages, True  # <-- Указывает, что нужно ждать

        # --- ШАГ 2: Подготовка данных ДЛЯ REASONER'А ---
        from utils.system_prompt_reasoner import generate_reasoner_system_prompt
        reasoner_system_prompt = generate_reasoner_system_prompt()

        # Передаём ТОЛЬКО content (без tool_calls!)
        assistant_content_only = assistant_msg.get('content', '')  # ← именно это!

        # --- ШАГ 3: Вызов reasoner'а ---
        reasoner_response = await self.call_reasoner_model(
            system_prompt_for_reasoner=reasoner_system_prompt,
            assistant_content=assistant_content_only,
            tool_results=tool_results  # ← уже содержит только результаты
        )

        # --- ФОРМИРУЕМ ТЕКУЩИЙ ВХОД ДЛЯ REASONER'А (user) ---
        # (Повторяем логику из call_reasoner_model для формирования user_message_content)
        assistant_text = assistant_msg.get('content', '') or "(инструментальная модель не предоставила пояснений)"
        tool_contents = []
        for tr in tool_results:
            content = tr.get('content', '')
            try:
                parsed = json.loads(content)
                content = json.dumps(parsed, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
            tool_contents.append(content)
        tool_results_text = "\n\n".join(tool_contents) if tool_contents else "(результаты инструментов отсутствуют)"
        user_message_content = (
            f"### Пояснение от трейдера:\n{assistant_text}\n\n"
            f"### Данные от инструментов:\n{tool_results_text}"
        )

        # --- СОХРАНЯЕМ user и assistant в reasoner_context ---
        # Если системный промпт ещё не добавлен в этот сеанс (например, после перезапуска)
        if not self.reasoner_context or self.reasoner_context[0].get('role') != 'system':
            self.reasoner_context.append({"role": "system", "content": reasoner_system_prompt})

        self.reasoner_context.append({
            "role": "user",
            "content": user_message_content
        })
        self.reasoner_context.append({
            "role": "assistant",
            "content": reasoner_response
        })

        # --- СОХРАНЯЕМ КОНТЕКСТ РАССУЖДЕНИЙ ---
        from utils.reasoner_context_manager import save_reasoner_context_to_file
        save_reasoner_context_to_file(self.reasoner_context, iteration)

        # --- ШАГ 4: добавляем всё в основной контекст ---
        # assistant -> tool -> user (с ответом reasoner)
        messages.append(assistant_msg)
        messages.extend(tool_results)
        messages.append({
            'role': 'user',
            'content': reasoner_response
        })

        # Сохраняем контекст
        save_context_to_file(messages, iteration)
        return messages, False  # <-- Указывает, что НЕ нужно ждать

    async def run_full_analysis_cycle_until_wait(self, symbol: str, candle_info: dict = None):
        """
        Загружает контекст, добавляет информацию о новой свече (если есть),
        запускает цикл: инструментальная модель -> рассуждающая модель,
        до тех пор, пока инструментальная модель не вызовет инструмент 'wait_for_next_candle'.
        Возвращает True, если был вызван wait_for_next_candle, иначе False.
        """
        # Проверяем, готова ли пара к анализу
        if not self.pair_state_manager.is_pair_ready_for_analysis(symbol):
            logger.info(f"🔄 Пара {symbol} в состоянии ожидания, пропускаем анализ")
            return False

        print(f"\n--- 🚀 Запуск ПОЛНОГО цикла анализа для пары {symbol} до команды 'ждать' ---")
        messages, iteration = load_context_from_file()
        if not messages:
            from utils.system_prompt import generate_system_prompt
            system_prompt = generate_system_prompt()
            messages = [
                {'role': 'system', 'content': system_prompt},
            ]
            iteration = 0
        else:
            # Добавляем информацию о новой свече к существующему контексту
            if candle_info:
                candle_message = f"Закрылась новая {candle_info['interval']}-минутная свеча для {symbol} в {candle_info['timestamp']}."
                messages.append({'role': 'user', 'content': candle_message})
            iteration += 1  # Увеличиваем номер итерации

        # Цикл анализа до команды 'ждать'
        while True:
            iteration += 1
            logger.info(f"--- 🔄 Итерация полного цикла {iteration} для пары {symbol} ---")
            try:
                # Сначала по циклам, потом по токенам — на всякий случай
                messages = truncate_context_by_cycles(messages, max_cycles=8)
                messages = truncate_context_adaptive(messages, max_tokens=900000)
                messages = self._clean_incomplete_tool_calls(messages)
                estimated = count_tokens_in_messages(messages)
                logger.info(f"📊 Токены перед итерацией {iteration} для пары {symbol}: ~{estimated}")
                print(f"\n--- 🔄 Итерация {iteration} для пары {symbol} ---")
                print(f"[Токены: ~{estimated} / 100000]\n")

                # --- ШАГ 1: вызов инструментальной модели ---
                assistant_msg, tool_results = await self.call_model_with_tools(messages)

                # --- ПРОВЕРКА: вызван ли wait_for_next_candle СРАЗУ после инструментальной модели? ---
                wait_for_candle_immediate = False
                if assistant_msg.get('tool_calls'):
                    # Проверяем, был ли последним вызванным инструментом wait_for_next_candle
                    last_tool_call = assistant_msg['tool_calls'][-1]  # Берем последний вызов
                    if last_tool_call.get('function', {}).get('name') == 'wait_for_next_candle':
                        # Проверяем результат вызова инструмента
                        if tool_results:  # Если есть результаты инструментов
                            last_tool_result = tool_results[-1]  # Берем последний результат
                            if last_tool_result.get('role') == 'tool' and last_tool_result.get('tool_call_id') == \
                                    last_tool_call['id']:
                                try:
                                    result_content = json.loads(last_tool_result.get('content', '{}'))
                                    if result_content.get('status') == 'waiting_for_next_candle':
                                        print(
                                            f"✅ Обнаружен сигнал ожидания следующей свечи сразу после инструментальной модели: {result_content.get('message', 'Ожидание свечи')}")
                                        wait_for_candle_immediate = True
                                except json.JSONDecodeError:
                                    print("⚠️ Не удалось распознать результат инструмента wait_for_next_candle.")
                # --- КОНЕЦ ПРОВЕРКИ ---

                # Если сигнал ожидания получен сразу после инструментальной модели, ВЫХОДИМ ИЗ ЦИКЛА
                if wait_for_candle_immediate:
                    # Добавляем сообщение инструментальной модели и результат инструмента в контекст
                    messages.append(assistant_msg)
                    messages.extend(tool_results)  # Результат wait_for_next_candle уже в tool_results
                    # Сохраняем контекст
                    save_context_to_file(messages, iteration)
                    print(f"--- ✅ ПОЛНЫЙ цикл анализа для пары {symbol} завершен по команде 'ждать' ---")
                    return True  # <-- Указывает, что нужно ждать

                # --- ШАГ 2: Подготовка данных ДЛЯ REASONER'А ---
                from utils.system_prompt_reasoner import generate_reasoner_system_prompt
                reasoner_system_prompt = generate_reasoner_system_prompt()

                # Передаём ТОЛЬКО content (без tool_calls!)
                assistant_content_only = assistant_msg.get('content', '')  # ← именно это!

                # --- ШАГ 3: Вызов reasoner'а ---
                reasoner_response = await self.call_reasoner_model(
                    system_prompt_for_reasoner=reasoner_system_prompt,
                    assistant_content=assistant_content_only,
                    tool_results=tool_results  # ← уже содержит только результаты
                )

                # --- ФОРМИРУЕМ ТЕКУЩИЙ ВХОД ДЛЯ REASONER'А (user) ---
                # (Повторяем логику из call_reasoner_model для формирования user_message_content)
                assistant_text = assistant_msg.get('content',
                                                   '') or "(инструментальная модель не предоставила пояснений)"
                tool_contents = []
                for tr in tool_results:
                    content = tr.get('content', '')
                    try:
                        parsed = json.loads(content)
                        content = json.dumps(parsed, ensure_ascii=False, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    tool_contents.append(content)
                tool_results_text = "\n\n".join(
                    tool_contents) if tool_contents else "(результаты инструментов отсутствуют)"
                user_message_content = (
                    f"### Пояснение от трейдера:\n{assistant_text}\n\n"
                    f"### Данные от инструментов:\n{tool_results_text}"
                )

                # --- СОХРАНЯЕМ user и assistant в reasoner_context ---
                # Если системный промпт ещё не добавлен в этот сеанс (например, после перезапуска)
                if not self.reasoner_context or self.reasoner_context[0].get('role') != 'system':
                    self.reasoner_context.append({"role": "system", "content": reasoner_system_prompt})

                self.reasoner_context.append({
                    "role": "user",
                    "content": user_message_content
                })
                self.reasoner_context.append({
                    "role": "assistant",
                    "content": reasoner_response
                })

                # --- СОХРАНЯЕМ КОНТЕКСТ РАССУЖДЕНИЙ ---
                from utils.reasoner_context_manager import save_reasoner_context_to_file
                save_reasoner_context_to_file(self.reasoner_context, iteration)

                # --- ШАГ 4: добавляем всё в основной контекст ---
                # assistant -> tool -> user (с ответом reasoner)
                messages.append(assistant_msg)
                messages.extend(tool_results)
                messages.append({
                    'role': 'user',
                    'content': reasoner_response
                })

                # Сохраняем контекст
                save_context_to_file(messages, iteration)

                # Цикл продолжается, возвращаемся к вызову инструментальной модели

            except KeyboardInterrupt:
                logger.info("🛑 Цикл прерван пользователем.")
                print(f"\n--- 🛑 ЦИКЛ ПРЕРВАН ---")
                save_context_to_file(messages, iteration)
                return False  # <-- Возвращаем False, так как не было команды 'ждать'
            except Exception as e:
                logger.error(f"❌ Ошибка в итерации полного цикла {iteration} для пары {symbol}: {e}")
                print(f"❌ Ошибка: {e}")
                messages = self._clean_incomplete_tool_calls(messages)
                messages.append({
                    'role': 'user',
                    'content': f"Произошла ошибка: {e}. Продолжай работу."
                })
                save_context_to_file(messages, iteration)
                # Возвращаем False, чтобы main.py не ждал, а продолжил ожидание свечи
                # Или можно решить по-другому, например, продолжить цикл
                # Пока что вернем False
                return False
