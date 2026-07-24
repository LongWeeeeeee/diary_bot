"""Асинхронный модуль работы с базой данных SQLite."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Dict, List, Optional, TypeVar, Callable, Any

import aiosqlite

from config import timed

logger = logging.getLogger(__name__)

DB_PATH = 'daily_scores.db'
MAX_RETRIES = 3
RETRY_DELAY = 0.1
POOL_SIZE = 5

T = TypeVar('T')


@dataclass
class ParsedProfile:
    """Распарсенный профиль пользователя."""
    user_id: str
    tasks_pool: List[str]
    one_time_tasks: List[str]
    scheduler_arguments: Dict[str, Any]
    personal_records: Dict[str, Any]
    previous_diary: str
    chosen_collected_data: List[str]
    notifications_data: Dict[str, Any]
    daily_tasks: Dict[str, str]
    daily_tasks_not_time: List[str]


def parse_profile(row: tuple) -> Optional[ParsedProfile]:
    """Парсит строку профиля из БД в объект."""
    if not row:
        return None
    try:
        return ParsedProfile(
            user_id=str(json.loads(row[0]) if row[0] else row[0]),
            tasks_pool=json.loads(row[1]) if row[1] else [],
            one_time_tasks=json.loads(row[2]) if row[2] else [],
            scheduler_arguments=json.loads(row[3]) if row[3] else {},
            personal_records=json.loads(row[4]) if row[4] else {},
            previous_diary=row[5] or '',
            chosen_collected_data=json.loads(row[6]) if row[6] else [],
            notifications_data=json.loads(row[7]) if row[7] else {},
            daily_tasks=json.loads(row[8]) if row[8] else {},
            daily_tasks_not_time=json.loads(row[9]) if row[9] else []
        )
    except (json.JSONDecodeError, IndexError) as e:
        logger.error(f"Error parsing profile: {e}")
        return None


class ConnectionPool:
    """Оптимизированный пул соединений для SQLite."""
    
    def __init__(self, db_path: str, pool_size: int = POOL_SIZE):
        self.db_path = db_path
        self.pool_size = pool_size
        self._connections: List[aiosqlite.Connection] = []
        self._available: List[aiosqlite.Connection] = []
        self._initialized = False
        self._lock: Optional[asyncio.Lock] = None
    
    async def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock
    
    async def _create_connection(self) -> aiosqlite.Connection:
        """Создаёт оптимизированное соединение."""
        conn = await aiosqlite.connect(self.db_path, timeout=30.0)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA cache_size=10000")
        await conn.execute("PRAGMA temp_store=MEMORY")
        return conn
    
    async def initialize(self):
        """Инициализирует пул соединений."""
        if self._initialized:
            return
        lock = await self._get_lock()
        async with lock:
            if self._initialized:
                return
            for _ in range(self.pool_size):
                conn = await self._create_connection()
                self._connections.append(conn)
                self._available.append(conn)
            self._initialized = True
            logger.info(f"Connection pool initialized with {self.pool_size} connections")
    
    @asynccontextmanager
    async def acquire(self):
        """Получает соединение из пула."""
        if not self._initialized:
            await self.initialize()
        
        lock = await self._get_lock()
        conn = None
        async with lock:
            if self._available:
                conn = self._available.pop()

        # Пул исчерпан — временное соединение. ВАЖНО: работаем вне lock,
        # иначе запросы сериализуются, а вложенный acquire() даёт дедлок.
        if conn is None:
            conn = await self._create_connection()
            try:
                yield conn
            finally:
                await conn.close()
            return

        try:
            yield conn
        finally:
            async with lock:
                self._available.append(conn)
    
    async def close(self):
        """Закрывает все соединения в пуле."""
        for conn in self._connections:
            try:
                await conn.close()
            except Exception:
                pass
        self._connections.clear()
        self._available.clear()
        self._initialized = False
        self._lock = None
        logger.info("Connection pool closed")


# Глобальный пул соединений
_pool: Optional[ConnectionPool] = None
_pool_db_path: Optional[str] = None


async def get_pool() -> ConnectionPool:
    """Получает или создаёт пул соединений."""
    global _pool, _pool_db_path
    # Пересоздаём пул если путь к БД изменился
    if _pool is None or _pool_db_path != DB_PATH:
        if _pool is not None:
            await _pool.close()
        _pool = ConnectionPool(DB_PATH)
        _pool_db_path = DB_PATH
        await _pool.initialize()
    return _pool


async def reset_pool():
    """Сбрасывает пул соединений (для тестов)."""
    global _pool, _pool_db_path
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_db_path = None


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


@asynccontextmanager
async def get_db():
    """Получить соединение из пула."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@timed
async def get_user_data(user_id: str, include_today: bool = False) -> Optional[Dict]:
    """Получает все данные пользователя одним запросом.
    
    Args:
        user_id: ID пользователя
        include_today: Если True, также загружает today_tasks и today_tasks_not_time
    """
    user_id = str(user_id)
    async with get_db() as db:
        # Получаем профиль
        async with db.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)) as cursor:
            profile = await cursor.fetchone()
        
        if not profile:
            return None
        
        # Получаем tasks_pool
        async with db.execute("SELECT task_name FROM tasks_pool WHERE user_id = ? ORDER BY rowid", (user_id,)) as cursor:
            tasks_pool_rows = await cursor.fetchall()
        
        # Получаем one_time_tasks
        async with db.execute("SELECT task_name FROM one_time_items WHERE user_id = ? ORDER BY rowid", (user_id,)) as cursor:
            one_time_rows = await cursor.fetchall()
        
        result = {
            'profile': profile,
            'tasks_pool': [row[0] for row in tasks_pool_rows],
            'one_time_tasks': [row[0] for row in one_time_rows]
        }
        
        # Опционально загружаем today_tasks
        if include_today:
            async with db.execute("SELECT task_time, task_name FROM today_tasks_table WHERE user_id = ? ORDER BY task_time",
                                  (user_id,)) as cursor:
                today_rows = await cursor.fetchall()
            async with db.execute("SELECT task_name FROM today_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                                  (user_id,)) as cursor:
                today_not_time_rows = await cursor.fetchall()
            result['today_tasks'] = {row[0]: row[1] for row in today_rows}
            result['today_tasks_not_time'] = [row[0] for row in today_not_time_rows]
        
        return result


async def database_start():
    """Инициализация базы данных и миграции."""
    # Инициализируем пул соединений
    pool = await get_pool()
    
    async with pool.acquire() as db:
        
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_today_tasks_user ON today_tasks_table(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_today_not_time_user ON today_tasks_not_time_table(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_daily_not_time_user ON daily_tasks_not_time_table(user_id)")

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


@timed
async def create_profile(user_id) -> Optional[tuple]:
    """Создаёт профиль пользователя или возвращает существующий."""
    user_id = str(user_id)  # Стандартизируем тип
    async with get_db() as db:
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


class BatchUpdater:
    """Накопитель обновлений БД для батчинга."""
    
    def __init__(self, user_id: str):
        self.user_id = str(user_id)
        self._updates: Dict[str, any] = {}
    
    def add(self, **kwargs):
        """Добавляет обновления в батч."""
        self._updates.update(kwargs)
        return self
    
    async def commit(self):
        """Выполняет все накопленные обновления одним запросом."""
        if self._updates:
            await edit_database(user_id=self.user_id, **self._updates)
            self._updates.clear()


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
    
    async with get_db() as db:
        try:
            await db.execute("INSERT OR IGNORE INTO profile (user_id) VALUES (?)", (user_id,))
            # Один UPDATE вместо нескольких
            await db.execute(f"UPDATE profile SET {', '.join(updates)} WHERE user_id = ?", values)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"Database error in edit_database for user {user_id}: {e}")
            raise


async def batch_update_tasks(user_id: str, tasks_pool: Optional[List[str]] = None,
                             one_time_tasks: Optional[List[str]] = None,
                             daily_tasks: Optional[Dict[str, str]] = None,
                             daily_tasks_not_time: Optional[List[str]] = None):
    """Обновляет несколько таблиц задач в одной транзакции."""
    user_id = str(user_id)
    async with get_db() as db:
        try:
            if tasks_pool is not None:
                await db.execute("DELETE FROM tasks_pool WHERE user_id = ?", (user_id,))
                if tasks_pool:
                    await db.executemany("INSERT INTO tasks_pool(user_id, task_name) VALUES (?, ?)",
                                        [(user_id, t) for t in tasks_pool])
            
            if one_time_tasks is not None:
                await db.execute("DELETE FROM one_time_items WHERE user_id = ?", (user_id,))
                if one_time_tasks:
                    await db.executemany("INSERT INTO one_time_items(user_id, task_name) VALUES (?, ?)",
                                        [(user_id, t) for t in one_time_tasks])
            
            if daily_tasks is not None:
                await db.execute("DELETE FROM daily_tasks_table WHERE user_id = ?", (user_id,))
                if daily_tasks:
                    await db.executemany("INSERT INTO daily_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                                        [(user_id, k, v) for k, v in daily_tasks.items()])
            
            if daily_tasks_not_time is not None:
                await db.execute("DELETE FROM daily_tasks_not_time_table WHERE user_id = ?", (user_id,))
                if daily_tasks_not_time:
                    await db.executemany("INSERT INTO daily_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                                        [(user_id, t) for t in daily_tasks_not_time])
            
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"Batch update error for user {user_id}: {e}")
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
    async with get_db() as db:
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
    async with get_db() as db:
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
    async with get_db() as db:
        async with db.execute("SELECT task_name FROM one_time_items WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def remove_one_time_task(user_id: str, task_name: str):
    """Удаляет разовую задачу."""
    user_id = str(user_id)
    async with get_db() as db:
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
    async with get_db() as db:
        async with db.execute("SELECT task_time, task_name FROM daily_tasks_table WHERE user_id = ? ORDER BY task_time",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


async def remove_daily_task_by_name(user_id: str, task_name: str):
    """Удаляет ежедневную задачу по имени."""
    user_id = str(user_id)
    async with get_db() as db:
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
    async with get_db() as db:
        async with db.execute("SELECT task_name FROM daily_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def remove_daily_not_time_task(user_id: str, task_name: str):
    """Удаляет ежедневную задачу без времени."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("DELETE FROM daily_tasks_not_time_table WHERE user_id = ? AND task_name = ?",
                         (user_id, task_name))
        await db.commit()


async def get_today_tasks(user_id: str) -> Dict[str, str]:
    """Получает задачи на сегодня с временем."""
    user_id = str(user_id)
    async with get_db() as db:
        async with db.execute("SELECT task_time, task_name FROM today_tasks_table WHERE user_id = ? ORDER BY task_time",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


async def add_today_task(user_id: str, task_time: str, task_name: str):
    """Добавляет задачу на сегодня."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("INSERT OR REPLACE INTO today_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                         (user_id, task_time, task_name))
        await db.commit()


async def remove_today_task(user_id: str, task_time: str):
    """Удаляет задачу на сегодня по времени."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_time = ?", (user_id, task_time))
        await db.commit()


async def remove_today_tasks_by_name(user_id: str, task_name: str):
    """Удаляет задачи на сегодня по имени."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
        await db.commit()


async def clear_today_tasks(user_id: str):
    """Очищает все задачи на сегодня."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("DELETE FROM today_tasks_table WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_today_tasks_not_time(user_id: str) -> List[str]:
    """Получает задачи на сегодня без времени."""
    user_id = str(user_id)
    async with get_db() as db:
        async with db.execute("SELECT task_name FROM today_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                              (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def add_today_task_not_time(user_id: str, task_name: str):
    """Добавляет задачу на сегодня без времени."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO today_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                         (user_id, task_name))
        await db.commit()


async def remove_today_task_not_time(user_id: str, task_name: str):
    """Удаляет задачу на сегодня без времени."""
    user_id = str(user_id)
    async with get_db() as db:
        await db.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ? AND task_name = ?",
                         (user_id, task_name))
        await db.commit()


@with_retry
async def add_daily_log(user_id, date, activities, steps, sleep_quality, about_day, personal_rate):
    """Добавляет или обновляет запись о дне."""
    user_id = str(user_id)
    logger.info(f"add_daily_log: user_id={user_id}, date={date}")
    try:
        async with get_db() as db:
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
        async with get_db() as db:
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
        async with get_db() as db:
            async with db.execute(
                "SELECT date, activities, steps, sleep_quality, about_day, personal_rate "
                "FROM daily_logs WHERE user_id = ? ORDER BY date ASC",
                (user_id,)
            ) as cursor:
                return await cursor.fetchall()
    except Exception as e:
        logger.error(f"Error getting all logs for {user_id}: {e}")
        return []


@timed
async def get_full_user_state(user_id: str) -> Dict:
    """Получает полное состояние пользователя для инициализации.
    
    Оптимизированный запрос для загрузки всех данных при старте.
    """
    user_id = str(user_id)
    result = {
        'profile': None,
        'tasks_pool': [],
        'one_time_tasks': [],
        'daily_tasks': {},
        'daily_tasks_not_time': [],
        'today_tasks': {},
        'today_tasks_not_time': []
    }
    
    async with get_db() as db:
        # Профиль
        async with db.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)) as cursor:
            result['profile'] = await cursor.fetchone()
        
        if not result['profile']:
            return result
        
        # Все задачи одним блоком запросов
        queries = [
            ("SELECT task_name FROM tasks_pool WHERE user_id = ? ORDER BY rowid", 'tasks_pool'),
            ("SELECT task_name FROM one_time_items WHERE user_id = ? ORDER BY rowid", 'one_time_tasks'),
            ("SELECT task_time, task_name FROM daily_tasks_table WHERE user_id = ? ORDER BY task_time", 'daily_tasks'),
            ("SELECT task_name FROM daily_tasks_not_time_table WHERE user_id = ? ORDER BY rowid", 'daily_tasks_not_time'),
            ("SELECT task_time, task_name FROM today_tasks_table WHERE user_id = ? ORDER BY task_time", 'today_tasks'),
            ("SELECT task_name FROM today_tasks_not_time_table WHERE user_id = ? ORDER BY rowid", 'today_tasks_not_time'),
        ]
        
        for query, key in queries:
            async with db.execute(query, (user_id,)) as cursor:
                rows = await cursor.fetchall()
                if key in ('daily_tasks', 'today_tasks'):
                    result[key] = {row[0]: row[1] for row in rows}
                else:
                    result[key] = [row[0] for row in rows]
    
    return result



async def get_users_with_notifications() -> List[Dict]:
    """Получает всех пользователей с включёнными уведомлениями."""
    result = []
    async with get_db() as db:
        async with db.execute("SELECT user_id, notifications_data FROM profile") as cursor:
            rows = await cursor.fetchall()
    
    for row in rows:
        try:
            user_id = str(json.loads(row[0]) if row[0] else row[0])
            notifications_data = json.loads(row[1]) if row[1] else {}
            if (notifications_data.get('chosen_notifications') == ['Включено']
                and 'hours' in notifications_data
                and 'minutes' in notifications_data):
                result.append({
                    'user_id': user_id,
                    'hours': notifications_data['hours'],
                    'minutes': notifications_data['minutes']
                })
        except (json.JSONDecodeError, TypeError):
            continue

    return result


async def get_users_with_scheduler_jobs() -> List[tuple]:
    """Получает (user_id, scheduler_arguments) всех пользователей с напоминаниями."""
    result = []
    async with get_db() as db:
        async with db.execute("SELECT user_id, scheduler_arguments FROM profile") as cursor:
            rows = await cursor.fetchall()

    for row in rows:
        try:
            user_id = str(json.loads(row[0]) if row[0] else row[0])
            scheduler_arguments = json.loads(row[1]) if row[1] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if scheduler_arguments:
            result.append((user_id, scheduler_arguments))

    return result
