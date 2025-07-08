import json
import sqlite3 as sq
from aiogram.types import Message
ALLOWED_COLUMNS = {
    "tasks_pool", "one_time_tasks", "scheduler_arguments",
    "personal_records", "previous_diary", "chosen_collected_data",
    "notifications_data", "today_tasks", "daily_tasks"
}
async def database_start():
    global db, cur

    db = sq.connect('daily_scores.db')
    cur = db.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, tasks_pool TEXT, one_time_tasks TEXT,"
        " scheduler_arguments TEXT, personal_records TEXT, previous_diary TEXT, chosen_collected_data TEXT,"
        " notifications_data TEXT, today_tasks TEXT, daily_tasks TEXT)")

    db.commit()


async def create_profile(user_id):
    user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cur.execute("INSERT INTO profile VALUES(?,?,?,?,?,?,?,?,?,?)", (user_id, '[]', '[]', '{}', '{}', '', '[]', '{}', '{}', '{}'))
        db.commit()
    else:
        return cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()


async def edit_database(**kwargs):
    try:
        # Начинаем транзакцию
        db.execute("BEGIN TRANSACTION")
        for name, value_to_dump in kwargs.items():
            # Проверяем, что имя столбца разрешено
            if name not in ALLOWED_COLUMNS:
                print(f"Database error: Attempted to update a non-whitelisted column: {name}")
                # Можно либо проигнорировать, либо вызвать исключение
                continue  # или raise ValueError(f"Invalid column name: {name}")

            value = json.dumps(value_to_dump, ensure_ascii=False)
            cur.execute(f'UPDATE profile SET {name} = ?', (value,))

        # Завершаем транзакцию, применяя все изменения разом
        db.commit()

    except Exception as e:
        # Если что-то пошло не так, откатываем все изменения
        db.rollback()
        print(f"Database error: {e}. Transaction rolled back.")
        raise

