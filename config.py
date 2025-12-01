"""
Конфигурация и общие объекты приложения.
Этот модуль должен импортироваться первым для избежания циклических зависимостей.
"""
import logging
import os
import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys

logger = logging.getLogger(__name__)

# === Профилирование ===
PROFILING_ENABLED = os.environ.get('BOT_PROFILING', '0') == '1'
PROFILING_THRESHOLD_MS = 100  # Логировать только если > 100ms


def timed(func: Callable) -> Callable:
    """Декоратор для замера времени выполнения async функций."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if not PROFILING_ENABLED:
            return await func(*args, **kwargs)
        
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms > PROFILING_THRESHOLD_MS:
                logger.warning(f"⏱️ SLOW: {func.__module__}.{func.__name__} took {elapsed_ms:.0f}ms")
            else:
                logger.debug(f"⏱️ {func.__name__}: {elapsed_ms:.0f}ms")
    return wrapper

# === Константы ===
TARGET_TZ = ZoneInfo("Europe/Moscow")
MIN_DIARY_MESSAGE_LENGTH = 120
PERSONAL_RATE_MIN = 0
PERSONAL_RATE_MAX = 10

# Координаты для расчёта заката (Набережные Челны)
LAT = 55.72545
LNG = 52.41122

# === Переводы и маппинги ===
WEEKDAY_TRANSLATE = {
    'понедельник': 'mon', 
    'вторник': 'tue', 
    'среду': 'wed', 
    'четверг': 'thu', 
    'пятницу': 'fri',
    'субботу': 'sat',
    'воскресенье': 'sun'
}

WEEKDAY_PREFIX = {
    'воскресенье': 'каждое',
    'субботу': 'каждую',
    'пятницу': 'каждую',
    'четверг': 'каждый',
    'среду': 'каждую',
    'вторник': 'каждый',
    'понедельник': 'каждый'
}

NEGATIVE_RESPONSES = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}

MONTH_NAMES_RU = [
    "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня",
    "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"
]

# === FSM States ===
class ClientState(StatesGroup):
    greet = State()
    personal_rate_1 = State()
    one_time_tasks_3 = State()
    change_tasks_pool_1 = State()
    steps = State()
    total_sleep = State()
    about_day = State()
    add_tasks_pool = State()
    edit_tasks_pool = State()
    personal_rate = State()
    settings = State()
    one_time_tasks_2 = State()
    date_jobs = State()
    date_jobs_1 = State()
    date_jobs_2 = State()
    date_jobs_week = State()
    date_jobs_year = State()
    date_jobs_once = State()
    date_jobs_month = State()
    collected_data = State()
    notification_proceed = State()
    notification_set_date = State()
    new_today_tasks = State()


# === Инициализация объектов ===
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

try:
    from aiogram.fsm.storage.redis import RedisStorage
    storage = RedisStorage.from_url(redis_url)
except ImportError:
    print("\n" + "=" * 60)
    print("ОШИБКА: Модуль redis не установлен!")
    print("Установите: pip install redis")
    print("=" * 60 + "\n")
    raise SystemExit(1)

bot = Bot(token=keys.Token)
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler(timezone='Europe/Moscow')

# === Утилиты ===
remove_markup = types.ReplyKeyboardRemove()


def day_to_prefix(day: str) -> str:
    """Возвращает правильный префикс для дня недели (каждый/каждую/каждое)."""
    return WEEKDAY_PREFIX.get(day, 'каждый')


def has_user_data(user_data: Any) -> bool:
    """Проверяет, есть ли данные пользователя в state."""
    return user_data is not None and isinstance(user_data, dict) and len(user_data) > 0


def should_task_run_today(values: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """
    Проверяет, должна ли запланированная задача выполниться сегодня.
    
    Args:
        values: Словарь с параметрами задачи (day_of_week, day, month, run_date)
        now: Текущее время (если None, берётся текущее время в TARGET_TZ)
    
    Returns:
        True если задача должна выполниться сегодня
    """
    if now is None:
        now = datetime.now(TARGET_TZ)
    
    # Weekly tasks (day_of_week)
    if 'day_of_week' in values:
        today_dow = now.strftime('%a').lower()[:3]
        if values['day_of_week'] == today_dow:
            return True
    
    # Monthly tasks (day of month only)
    elif 'day' in values and 'month' not in values:
        try:
            if int(values['day']) == now.day:
                return True
        except (ValueError, TypeError):
            pass
    
    # Yearly tasks (specific day and month)
    elif 'day' in values and 'month' in values:
        try:
            if int(values['day']) == now.day and int(values['month']) == now.month:
                return True
        except (ValueError, TypeError):
            pass
    
    # One-time tasks (run_date)
    elif 'run_date' in values:
        try:
            run_date = values['run_date']
            if isinstance(run_date, str):
                run_date = datetime.fromisoformat(run_date)
            if isinstance(run_date, datetime):
                if run_date.date() == now.date():
                    return True
        except Exception:
            pass
    
    return False
