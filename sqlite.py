import json
import sqlite3 as sq
from aiogram.types import Message
ALLOWED_COLUMNS = {
    "tasks_pool", "one_time_tasks", "scheduler_arguments",
    "personal_records", "previous_diary", "chosen_collected_data",
    "notifications_data", "today_tasks", "daily_tasks", "user_id", "today_tasks_not_time"
}
async def database_start():
    global db, cur

    db = sq.connect('daily_scores.db')
    cur = db.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, tasks_pool TEXT, one_time_tasks TEXT,"
        " scheduler_arguments TEXT, personal_records TEXT, previous_diary TEXT, chosen_collected_data TEXT,"
        " notifications_data TEXT, today_tasks TEXT, daily_tasks TEXT, today_tasks_not_time TEXT)")

    db.commit()


async def create_profile(user_id):
    user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cur.execute("INSERT INTO profile VALUES(?,?,?,?,?,?,?,?,?,?,?)", (user_id, '[]', '[]', '{}', '{}', '', '[]', '{}', '{}', '{}', '[]'))
        db.commit()
    else:
        return cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()


async def edit_database(user_id, **kwargs):
    try:
        db.execute("BEGIN TRANSACTION")
        for name, value_to_dump in kwargs.items():
            if name not in ALLOWED_COLUMNS:
                continue
            value = json.dumps(value_to_dump, ensure_ascii=False)
            cur.execute(f"UPDATE profile SET {name} = ? WHERE user_id = ?", (value, user_id))
        db.commit()
    except Exception as e:
        db.rollback()
        raise

