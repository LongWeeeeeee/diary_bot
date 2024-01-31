import json
import sqlite3 as sq


async def database_start():
    global db, cur

    db = sq.connect('daily_scores.db')
    cur = db.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, daily_scores TEXT, one_time_jobs TEXT, scheduler_arguments TEXT)")

    db.commit()


async def create_profile(user_id):
    user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cur.execute("INSERT INTO profile VALUES(?,?,?,?)", (user_id, '[]', '[]', '{}'))
        db.commit()
    else:
        return cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()


async def edit_database(**kwargs):
    for name in kwargs:
        value = json.dumps(kwargs[name], ensure_ascii=False)
        cur.execute(f'UPDATE profile SET {name} = ?', (value,))
    db.commit()

