import json
import sqlite3 as sq
from typing import Dict, List
from aiogram.types import Message
ALLOWED_COLUMNS = {
    "user_id", "tasks_pool", "one_time_tasks", "scheduler_arguments",
    "personal_records", "previous_diary", "chosen_collected_data",
    "notifications_data", "daily_tasks", "daily_tasks_not_time"
}
async def database_start():
    global db, cur

    db = sq.connect('daily_scores.db')
    cur = db.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, tasks_pool TEXT, one_time_tasks TEXT,"
        " scheduler_arguments TEXT, personal_records TEXT, previous_diary TEXT, chosen_collected_data TEXT,"
        " notifications_data TEXT, daily_tasks TEXT, daily_tasks_not_time TEXT)")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS tasks_pool (user_id TEXT, task_name TEXT, PRIMARY KEY (user_id, task_name))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS one_time_items (user_id TEXT, task_name TEXT, PRIMARY KEY (user_id, task_name))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS daily_tasks_table (user_id TEXT, task_time TEXT, task_name TEXT,"
        " PRIMARY KEY (user_id, task_time))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS daily_tasks_not_time_table (user_id TEXT, task_name TEXT,"
        " PRIMARY KEY (user_id, task_name))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS today_tasks_table (user_id TEXT, task_time TEXT, task_name TEXT,"
        " PRIMARY KEY (user_id, task_time))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS today_tasks_not_time_table (user_id TEXT, task_name TEXT,"
        " PRIMARY KEY (user_id, task_name))")

    cur.execute(
        "CREATE TABLE IF NOT EXISTS daily_logs (user_id TEXT, date TEXT, activities TEXT, steps REAL, "
        "sleep_quality REAL, about_day TEXT, personal_rate REAL, PRIMARY KEY (user_id, date))"
    )

    # Check for missing columns and add them if necessary (migration)
    cur.execute("PRAGMA table_info(profile)")
    columns = [info[1] for info in cur.fetchall()]
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
                cur.execute(f"ALTER TABLE profile ADD COLUMN {col_name} {col_type}")
                print(f"Migrated database: added column {col_name}")
            except sq.OperationalError as e:
                print(f"Migration error for {col_name}: {e}")

    db.commit()


async def create_profile(user_id):
    user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cur.execute("INSERT INTO profile VALUES(?,?,?,?,?,?,?,?,?,?)", (user_id, '[]', '[]', '{}', '{}', '', '[]', '{}', '{}', '[]'))
        db.commit()
        user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    await _migrate_legacy_tasks(user)
    return user


async def edit_database(user_id, **kwargs):
    try:
        db.execute("BEGIN TRANSACTION")
        # 1) гарантируем, что строка существует
        cur.execute("INSERT OR IGNORE INTO profile (user_id) VALUES (?)", (user_id,))
        # 2) обновляем только разрешённые поля
        for name, value_to_dump in kwargs.items():
            if name not in ALLOWED_COLUMNS or name == "user_id":
                continue
            # Safe to use f-string here because name is validated against ALLOWED_COLUMNS whitelist
            # but using string formatting with column name is still needed as it can't be a parameter
            value = json.dumps(value_to_dump, ensure_ascii=False)
            cur.execute(f"UPDATE profile SET {name} = ? WHERE user_id = ?", (value, user_id))
        db.commit()
    except Exception as e:
        db.rollback()
        import logging
        logging.error(f"Database error in edit_database for user {user_id}: {e}")
        raise


async def _migrate_legacy_tasks(user_row):
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

    if legacy_tasks_pool:
        exists = cur.execute("SELECT 1 FROM tasks_pool WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        if not exists:
            await replace_tasks_pool(user_id, legacy_tasks_pool)

    if legacy_one_time:
        exists = cur.execute("SELECT 1 FROM one_time_items WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        if not exists:
            await replace_one_time_tasks(user_id, legacy_one_time)

    if legacy_daily_tasks:
        exists = cur.execute("SELECT 1 FROM daily_tasks_table WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        if not exists:
            await replace_daily_tasks(user_id, legacy_daily_tasks)

    if legacy_daily_not_time:
        exists = cur.execute("SELECT 1 FROM daily_tasks_not_time_table WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        if not exists:
            await replace_daily_tasks_not_time(user_id, legacy_daily_not_time)


async def _replace_rows(query_delete: str, query_insert: str, user_id: str, rows: List[tuple]):
    cur.execute(query_delete, (user_id,))
    if rows:
        cur.executemany(query_insert, rows)
    db.commit()


async def replace_tasks_pool(user_id: str, tasks: List[str]):
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM tasks_pool WHERE user_id = ?",
                       "INSERT INTO tasks_pool(user_id, task_name) VALUES (?, ?)", user_id, rows)


async def get_tasks_pool(user_id: str) -> List[str]:
    rows = cur.execute("SELECT task_name FROM tasks_pool WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
    return [row[0] for row in rows]


async def replace_one_time_tasks(user_id: str, tasks: List[str]):
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM one_time_items WHERE user_id = ?",
                       "INSERT INTO one_time_items(user_id, task_name) VALUES (?, ?)", user_id, rows)


async def get_one_time_tasks(user_id: str) -> List[str]:
    rows = cur.execute("SELECT task_name FROM one_time_items WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
    return [row[0] for row in rows]


async def remove_one_time_task(user_id: str, task_name: str):
    cur.execute("DELETE FROM one_time_items WHERE user_id = ? AND task_name = ?", (user_id, task_name))
    db.commit()


async def replace_daily_tasks(user_id: str, tasks: Dict[str, str]):
    rows = [(user_id, time_key, task_name) for time_key, task_name in tasks.items()]
    await _replace_rows("DELETE FROM daily_tasks_table WHERE user_id = ?",
                       "INSERT INTO daily_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                       user_id, rows)


async def get_daily_tasks(user_id: str) -> Dict[str, str]:
    rows = cur.execute("SELECT task_time, task_name FROM daily_tasks_table WHERE user_id = ? ORDER BY task_time",
                       (user_id,)).fetchall()
    return {row[0]: row[1] for row in rows}


async def remove_daily_task_by_name(user_id: str, task_name: str):
    cur.execute("DELETE FROM daily_tasks_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
    db.commit()


async def replace_daily_tasks_not_time(user_id: str, tasks: List[str]):
    rows = [(user_id, task) for task in tasks]
    await _replace_rows("DELETE FROM daily_tasks_not_time_table WHERE user_id = ?",
                       "INSERT INTO daily_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                       user_id, rows)


async def get_daily_tasks_not_time(user_id: str) -> List[str]:
    rows = cur.execute("SELECT task_name FROM daily_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
                       (user_id,)).fetchall()
    return [row[0] for row in rows]


async def remove_daily_not_time_task(user_id: str, task_name: str):
    cur.execute("DELETE FROM daily_tasks_not_time_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
    db.commit()


async def get_today_tasks(user_id: str) -> Dict[str, str]:
    rows = cur.execute("SELECT task_time, task_name FROM today_tasks_table WHERE user_id = ? ORDER BY task_time",
                       (user_id,)).fetchall()
    return {row[0]: row[1] for row in rows}


async def add_today_task(user_id: str, task_time: str, task_name: str):
    cur.execute("INSERT OR REPLACE INTO today_tasks_table(user_id, task_time, task_name) VALUES (?, ?, ?)",
                (user_id, task_time, task_name))
    db.commit()


async def remove_today_task(user_id: str, task_time: str):
    cur.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_time = ?", (user_id, task_time))
    db.commit()


async def remove_today_tasks_by_name(user_id: str, task_name: str):
    cur.execute("DELETE FROM today_tasks_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
    db.commit()


async def clear_today_tasks(user_id: str):
    cur.execute("DELETE FROM today_tasks_table WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ?", (user_id,))
    db.commit()


async def get_today_tasks_not_time(user_id: str) -> List[str]:
    rows = cur.execute(
        "SELECT task_name FROM today_tasks_not_time_table WHERE user_id = ? ORDER BY rowid",
        (user_id,)).fetchall()
    return [row[0] for row in rows]


async def add_today_task_not_time(user_id: str, task_name: str):
    cur.execute("INSERT OR IGNORE INTO today_tasks_not_time_table(user_id, task_name) VALUES (?, ?)",
                (user_id, task_name))
    db.commit()


async def remove_today_task_not_time(user_id: str, task_name: str):
    cur.execute("DELETE FROM today_tasks_not_time_table WHERE user_id = ? AND task_name = ?", (user_id, task_name))
    db.commit()


async def add_daily_log(user_id, date, activities, steps, sleep_quality, about_day, personal_rate):
    """Добавляет или обновляет запись о дне в SQLite"""
    try:
        # date should be YYYY-MM-DD string
        cur.execute(
            "INSERT OR REPLACE INTO daily_logs (user_id, date, activities, steps, sleep_quality, about_day, personal_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, date, activities, steps, sleep_quality, about_day, personal_rate)
        )
        db.commit()
    except Exception as e:
        import logging
        logging.error(f"Error adding daily log for {user_id}: {e}")


async def get_last_logs(user_id, limit=7):
    """Получает последние записи дневника"""
    try:
        return cur.execute(
            "SELECT date, activities, steps, sleep_quality, about_day, personal_rate "
            "FROM daily_logs WHERE user_id = ? ORDER BY date DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    except Exception as e:
        import logging
        logging.error(f"Error getting last logs for {user_id}: {e}")
        return []


async def get_all_logs(user_id):
    """Получает все записи для подсчета статистики"""
    try:
        return cur.execute(
            "SELECT date, activities, steps, sleep_quality, about_day, personal_rate "
            "FROM daily_logs WHERE user_id = ? ORDER BY date ASC",
            (user_id,)
        ).fetchall()
    except Exception as e:
        import logging
        logging.error(f"Error getting all logs for {user_id}: {e}")
        return []

