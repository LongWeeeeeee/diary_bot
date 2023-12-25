import json
import sqlite3 as sq


async def database_start():
    global db, cur

    db = sq.connect('daily_scores.db')
    cur = db.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS profile (user_id TEXT PRIMARY KEY, daily_scores TEXT, one_time_jobs TEXT, date_jobs TEXT, maximum_records TEXT)")

    db.commit()


async def create_profile(user_id):
    user = cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        cur.execute("INSERT INTO profile VALUES(?,?,?,?,?)", (user_id, '', '', '', ''))
        db.commit()
    else:
        return cur.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()


async def edit_database(*args, **kwargs):
    for name in kwargs:
        value = json.dumps(kwargs[name], ensure_ascii=False)
        cur.execute(f'UPDATE profile SET {name} = ?', (value,))
    db.commit()

# async def main():
#     await db_start()
#     answer = await create_profile(123456)
#     if answer is not None:
#         print(answer)
#     await edit_database(daily_scores={'встал в 6:30': 1, 'лег в 11': 1})
# asyncio.run(main())
