"""Асинхронный модуль работы с базой данных SQLite."""
import asyncio
import json
import logging
from functools import wraps
from typing import Dict, List, Optional, TypeVar, Callable

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = 'daily_scores.db'
MAX_RETRIES = 3
RETRY_DELAY = 0.1

T = TypeVar('T')


def with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Декоратор для повторных попыток при ошибках БД."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return await func(*args, **kwargs)
            except aiosqlite.OperationalError as e:
                last_error = e
                if "database is locked" in str(e).lower():
                    logger.warning(f"Database locked, retry {attempt + 1}/{MAX_RETRIES}")
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise
        raise last_error
    return wrapper

ALLOWED_COLUMNS = {
    "user_id", "tasks_pool", "one_time_tasks", "scheduler_arguments",
    "personal_records", "previous_diary", "chosen_collected_data",
    "notifications_data", "daily_tasks", "daily_tasks_not_time"
}


async def get_db() -> aiosqlite.Connection:
    """Получить соединение с БД."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def database_start():
    """Инициализация базы данных и миграции."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Оптимизация SQLite для производительности
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=10000")
        await db.execute("PRAGMA temp_store=MEMORY")
        
        await db.execute(
            "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, tasks_pool TEXT, one_time_tasks TEXT,"
            " scheduler_arguments TEXT, personal_records TEXT, previous_diary TEXT, chosen_collected_data TEXT,"
            " notifications_data TEXT, daily_tasks TEXT, daily_tasks_not_time TEXT)")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS tasks_pool (user_id TEXT, task_name TEXT, PRIMARY KEY (user_id, task_name))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS one_time_items (user_id TEXT, task_name TEXT, PRIMARY KEY (user_id, task_name))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS daily_tasks_table (user_id TEXT, task_time TEXT, task_name TEXT,"
            " PRIMARY KEY (user_id, task_time))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS daily_tasks_not_time_table (user_id TEXT, task_name TEXT,"
            " PRIMARY KEY (user_id, task_name))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS today_tasks_table (user_id TEXT, task_time TEXT, task_name TEXT,"
            " PRIMARY KEY (user_id, task_time))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS today_tasks_not_time_table (user_id TEXT, task_name TEXT,"
            " PRIMARY KEY (user_id, task_name))")

        await db.execute(
            "CREATE TABLE IF NOT EXISTS daily_logs (user_id TEXT, date TEXT, activities TEXT, steps REAL, "
            "sleep_quality REAL, about_day TEXT, personal_rate REAL, PRIMARY KEY (user_id, date))"
        )

        # Индексы для производительности
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_pool_user ON tasks_pool(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_one_time_user ON one_time_items(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_daily_tasks_user ON daily_tasks_table(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date ON daily_logs(user_id, date)")

        # Миграция: добавление недостающих колонок
        async with db.execute("PRAGMA table_info(profile)") as cursor:
            rows = await cursor.fetchall()
            columns = [row[1] for row in rows]

        expected_columns = [
            ("tasks_pool", "TEXT"),
            ("one_time_tasks", "TEXT"),
            ("scheduler_arguments", "TEXT"),
            ("personal_records", "TEXT"),
            ("previous_diary", "TEXT"),
            ("chosen_collected_data", "TEXT"),
            ("notifications_data", "TEXT"),
            ("daily_tasks", "TEXT"),
            ("daily_tasks_not_time", "TEXT")
        ]

        for col_name, col_type in expected_columns:
            if col_name not in columns:
                try:
                    await db.execute(f"ALTER TABLE profile ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Migrated database: added column {col_name}")
                except Exception as e:
                    logger.error(f"Migration error for {col_name}: {e}")

        await db.commit()


async def create_profile(user_id) -> Optional[tuple]:
    """Создаёт профиль пользователя или возвращает существующий."""
    user_id = str(user_id)  # Стандартизируем тип
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        if not user:
            await db.execute(
                "INSERT INTO profile VALUES(?,?,?,?,?,?,?,?,?,?)",
                (user_id, '[]', '[]', '{}', '{}', '', '[]', '{}', '{}', '[]')
            )
            await db.commit()
            async with db.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)) as cursor:
                user = await cursor.fetchone()
        await _migrate_legacy_tasks(db, user)
    return user


@with_retry
async def edit_database(user_id, **kwargs):
    """Обновляет данные профиля пользователя."""
    user_id = str(user_id)
    if not kwargs:
        return
    
    # Фильтруем и подготавливаем данные
    updates = []
    values = []
    for name, value_to_dump in kwargs.items():
        if name not in ALLOWED_COLUMNS or name == "user_id":
            continue
        updates.append(f"{name} = ?")
        values.append(json.dumps(value_to_dump, ensure_ascii=False))
    
    if not updates:
        return
    
    values.append(user_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT OR IGNORE INTO profile (user_id) VALUES (?)", (user_id,))
            # Один UPDATE вместо нескольких
            await db.execute(f"UPDATE profile SET {', '.join(updates)} WHERE user_id = ?", values)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"Database error in edit_database for user {user_id}: {e}")
            raise


async def _migrate_legacy_tasks(db: aiosqlite.Connection, user_row):
    """Миграция данных из старого формата."""
    if not user_row:
        return
    try:
        user_id = str(json.loads(user_row[0]))
    except Exception:
        user_id = str(user_row[0])

    legacy_tasks_pool = json.loads(user_row[1]) if user_row[1] else []
    legacy_one_time = json.loads(user_row[2]) if user_row[2] else []
    legacy_daily_tasks = json.loads(user_row[8]) if user_row[8] else {}
    legacy_daily_not_time = json.loads(user_row[9]) if user_row[9] else []

    async with db.execute("SELECT 1 FROM tasks_pool WHERE user_id = ? LIMIT 1", (user_id,)) as cursor:
        exists = await cursor.fetchone()
    if legacy_tasks_pool and not exists:
        await _replace_rows_internal(db, "DELETE FROM tasks_pool WHERE user_id = ?",
                                     "INSERT INTO tasks_pool(user_id, task_name) VALUES (?, ?)",
                                     user_id, [(user_id, t) for t in legacy_tasks_pool])

    async with db.execute("SELECT 1 FROM one_time_items WHERE user_id = ? LIMIT 1", (user_id,)) as cursor:
        exists = await cursor.fetchone()
    if legacy_one_time and not exists:
        await _replace_rows_internal(db, "DELETE FROM one_time_items WHERE user_id = ?",
                                     "INSERT INTO one_time_items(user_id, task_name) VALUES (?, ?)",
                                     user_id, [(user_id, t) for t in legacy_one_time])

    async with db.execute("SELECT 1 FROM daily_tasks_table WHERE user_id = ? LIMIT 1", (user_id,)) as cursor:
        exists = await cursor.fetchone()
    if legacy_daily_tasks and not exists:
        rows = [(user_id, k, v) for k, v in legacy_daily_tasks.items()]
        await _replace_rows_internal(db, "DELETE FROM daily_tasks_table WHERE user_id = ?",
                                     "INSERT INTO daily_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                                     user_id, rows)

    async with db.execute("SELECT 1 FROM daily_tasks_not_time_table WHERE user_id = ? LIMIT 1", (user_id,)) as cursor:
        exists = await cursor.fetchone()
    if legacy_daily_not_time and not exists:
        await _replace_rows_internal(db, "DELETE FROM daily_tasks_not_time_table WHERE user_id = ?",
                                     "INSERT INTO daily_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                                     user_id, [(user_id, t) for t in legacy_daily_not_time])


async def _replace_rows_internal(db: aiosqlite.Connection, query_delete: str, query_insert: str,
                                  user_id: str, rows: List[tuple]):
    """Внутренняя функция замены строк (без создания нового соединения)."""
    await db.execute(query_delete, (user_id,))
    if rows:
        await db.executemany(query_insert, rows)
    await db.commit()


async def _replace_rows(query_delete: str, query_insert: str, user_id: str, rows: List[tuple]):
    """Заменяет строки в таблице."""
    async with aiosqlite.connect(DB_PATH) as db:
        await _replace_rows_internal(db, query_delete, query_insert, user_id, rows)


@with_retry
async def replace_tasks_pool(user_id: str, tasks: List[str]):
    """Заменяет список задач пользователя."""
    user_id = str(user_id)
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM tasks_pool WHERE user_id = ?",
                        "INSERT INTO tasks_pool(user_id, task_name) VALUES (?, ?)", user_id, rows)


async def get_tasks_pool(user_id: str) -> List[str]:
    """Получает список задач пользователя."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_name FROM tasks_pool WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


@with_retry
async def replace_one_time_tasks(user_id: str, tasks: List[str]):
    """Заменяет список разовых задач."""
    user_id = str(user_id)
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM one_time_items WHERE user_id = ?",
                        "INSERT INTO one_time_items(user_id, task_name) VALUES (?, ?)", user_id, rows)


async def get_one_time_tasks(user_id: str) -> List[str]:
    """Получает список разовых задач."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_name FROM one_time_items WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def remove_one_time_task(user_id: str, task_name: str):
    """Удаляет разовую задачу."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM one_time_items WHERE user_id = ? AND task_name = ?", (user_id, task_name))
        await db.commit()


async def replace_daily_tasks(user_id: str, tasks: Dict[str, str]):
    """Заменяет ежедневные задачи с временем."""
    user_id = str(user_id)
    rows = [(user_id, time_key, task_name) for time_key, task_name in tasks.items()]
    await _replace_rows("DELETE FROM daily_tasks_table WHERE user_id = ?",
                        "INSERT INTO daily_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                        user_id, rows)


async def get_daily_tasks(user_id: str) -> Dict[str, str]:
    """Получает ежедневные задачи с временем."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_time, task_name FROM daily_tasks_table WHERE user_id = ? ORDER BY task_time",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


async def remove_daily_task_by_name(user_id: str, task_name: str):
    """Удаляет ежедневную задачу по имени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM daily_tasks_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
        await db.commit()


async def replace_daily_tasks_not_time(user_id: str, tasks: List[str]):
    """Заменяет ежедневные задачи без времени."""
    user_id = str(user_id)
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM daily_tasks_not_time_table WHERE user_id = ?",
                        "INSERT INTO daily_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                        user_id, rows)


async def get_daily_tasks_not_time(user_id: str) -> List[str]:
    """Получает ежедневные задачи без времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_name FROM daily_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def remove_daily_not_time_task(user_id: str, task_name: str):
    """Удаляет ежедневную задачу без времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM daily_tasks_not_time_table WHERE user_id = ? AND task_name = ?",
                         (user_id, task_name))
        await db.commit()


async def get_today_tasks(user_id: str) -> Dict[str, str]:
    """Получает задачи на сегодня с временем."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_time, task_name FROM today_tasks_table WHERE user_id = ? ORDER BY task_time",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


async def add_today_task(user_id: str, task_time: str, task_name: str):
    """Добавляет задачу на сегодня."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO today_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                         (user_id, task_time, task_name))
        await db.commit()


async def remove_today_task(user_id: str, task_time: str):
    """Удаляет задачу на сегодня по времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_time = ?", (user_id, task_time))
        await db.commit()


async def remove_today_tasks_by_name(user_id: str, task_name: str):
    """Удаляет задачи на сегодня по имени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
        await db.commit()


async def clear_today_tasks(user_id: str):
    """Очищает все задачи на сегодня."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_today_tasks_not_time(user_id: str) -> List[str]:
    """Получает задачи на сегодня без времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT task_name FROM today_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def add_today_task_not_time(user_id: str, task_name: str):
    """Добавляет задачу на сегодня без времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO today_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                         (user_id, task_name))
        await db.commit()


async def remove_today_task_not_time(user_id: str, task_name: str):
    """Удаляет задачу на сегодня без времени."""
    user_id = str(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ? AND task_name = ?",
                         (user_id, task_name))
        await db.commit()


@with_retry
async def add_daily_log(user_id, date, activities, steps, sleep_quality, about_day, personal_rate):
    """Добавляет или обновляет запись о дне."""
    user_id = str(user_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO daily_logs (user_id, date, activities, steps, sleep_quality, about_day, personal_rate) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, date, activities, steps, sleep_quality, about_day, personal_rate)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error adding daily log for {user_id}: {e}")


async def get_last_logs(user_id, limit=7) -> List[tuple]:
    """Получает последние записи дневника."""
    user_id = str(user_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT date, activities, steps, sleep_quality, about_day, personal_rate "
                "FROM daily_logs WHERE user_id = ? ORDER BY date DESC LIMIT ?",
                (user_id, limit)
            ) as cursor:
                return await cursor.fetchall()
    except Exception as e:
        logger.error(f"Error getting last logs for {user_id}: {e}")
        return []


async def get_all_logs(user_id) -> List[tuple]:
    """Получает все записи дневника для статистики."""
    user_id = str(user_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT date, activities, steps, sleep_quality, about_day, personal_rate "
                "FROM daily_logs WHERE user_id = ? ORDER BY date ASC",
                (user_id,)
            ) as cursor:
                return await cursor.fetchall()
    except Exception as e:
        logger.error(f"Error getting all logs for {user_id}: {e}")
        return []

