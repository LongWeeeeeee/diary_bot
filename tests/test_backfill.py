"""Тесты заполнения пропущенных дней."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite as db_module
from functions import (
    build_day_schedule, day_label, missed_days, parse_user_date,
    refresh_personal_records
)

TEST_DB = 'test_backfill.db'


class test_db:
    """Отдельная БД на тест: пул сбрасываем до и после, файл удаляем.

    Асинхронный контекст, а не фикстура: async-фикстуры pytest-asyncio в strict
    mode требуют отдельного декоратора и молча отдают генератор.
    """

    async def __aenter__(self):
        self.original_path = db_module.DB_PATH
        await db_module.reset_pool()
        db_module.DB_PATH = TEST_DB
        await db_module.database_start()
        return self

    async def __aexit__(self, *exc_info):
        await db_module.reset_pool()
        db_module.DB_PATH = self.original_path
        for suffix in ('', '-wal', '-shm'):
            path = TEST_DB + suffix
            if os.path.exists(path):
                os.remove(path)
        return False


async def _add_log(user_id, iso_date, activities='дело'):
    await db_module.add_daily_log(
        user_id=user_id,
        date=iso_date,
        activities=activities,
        steps=1000,
        sleep_quality=7,
        about_day='текст',
        personal_rate=8,
    )


class TestParseUserDate:
    """Разбор даты, введённой пользователем."""

    def test_full_date_dots(self):
        assert parse_user_date('04.08.2026', today=date(2026, 8, 10)) == date(2026, 8, 4)

    def test_iso(self):
        assert parse_user_date('2026-08-04', today=date(2026, 8, 10)) == date(2026, 8, 4)

    def test_short_year(self):
        assert parse_user_date('04.08.26', today=date(2026, 8, 10)) == date(2026, 8, 4)

    def test_day_month_current_year(self):
        assert parse_user_date('04.08', today=date(2026, 8, 10)) == date(2026, 8, 4)

    def test_day_month_rolls_to_previous_year(self):
        # 31 декабря в январе — это прошлый год, а не будущее
        assert parse_user_date('31.12', today=date(2026, 1, 5)) == date(2025, 12, 31)

    def test_today_allowed(self):
        assert parse_user_date('10.08', today=date(2026, 8, 10)) == date(2026, 8, 10)

    def test_future_rejected(self):
        assert parse_user_date('11.08.2026', today=date(2026, 8, 10)) is None

    def test_garbage_rejected(self):
        assert parse_user_date('вчера', today=date(2026, 8, 10)) is None
        assert parse_user_date('', today=date(2026, 8, 10)) is None
        assert parse_user_date('45.19.2026', today=date(2026, 8, 10)) is None

    def test_spaces_and_slashes(self):
        assert parse_user_date(' 04/08/2026 ', today=date(2026, 8, 10)) == date(2026, 8, 4)


class TestDayLabel:
    def test_label_with_weekday(self):
        assert day_label('2026-08-04') == '04.08 (Вт)'

    def test_broken_date_returned_as_is(self):
        assert day_label('не дата') == 'не дата'


@pytest.mark.asyncio
async def test_missed_days_finds_gaps():
    async with test_db():
        user_id = '900001'
        for iso in ('2026-08-01', '2026-08-02', '2026-08-05'):
            await _add_log(user_id, iso)

        missed = await missed_days(user_id, window_days=30, today=date(2026, 8, 8))

        # Сегодня (08.08) не считаем, 03-04 и 06-07 — пропуски, свежие первыми
        assert missed == ['2026-08-07', '2026-08-06', '2026-08-04', '2026-08-03']


@pytest.mark.asyncio
async def test_missed_days_ignores_days_before_first_log():
    async with test_db():
        user_id = '900002'
        await _add_log(user_id, '2026-08-05')

        missed = await missed_days(user_id, window_days=30, today=date(2026, 8, 8))

        # До первой записи дневник не вёлся — это не пропуски
        assert missed == ['2026-08-07', '2026-08-06']


@pytest.mark.asyncio
async def test_missed_days_empty_without_logs():
    async with test_db():
        assert await missed_days('900003', today=date(2026, 8, 8)) == []


@pytest.mark.asyncio
async def test_missed_days_respects_window_and_limit():
    async with test_db():
        user_id = '900004'
        await _add_log(user_id, '2026-06-01')

        windowed = await missed_days(user_id, window_days=5, today=date(2026, 8, 8))
        assert windowed == [
            '2026-08-07', '2026-08-06', '2026-08-05', '2026-08-04', '2026-08-03'
        ]

        limited = await missed_days(user_id, window_days=5, today=date(2026, 8, 8), limit=2)
        assert limited == ['2026-08-07', '2026-08-06']


@pytest.mark.asyncio
async def test_has_daily_log():
    async with test_db():
        user_id = '900005'
        await _add_log(user_id, '2026-08-05')
        assert await db_module.has_daily_log(user_id, '2026-08-05') is True
        assert await db_module.has_daily_log(user_id, '2026-08-06') is False


@pytest.mark.asyncio
async def test_build_day_schedule_uses_daily_tasks_and_weekly_jobs():
    async with test_db():
        user_id = '900006'
        await db_module.create_profile(user_id)
        await db_module.replace_tasks_pool(user_id, ['зарядка', 'прогулка', 'брусья'])
        await db_module.batch_update_tasks(
            user_id,
            daily_tasks={'08:00': 'зарядка'},
            daily_tasks_not_time=['прогулка'],
        )
        await db_module.edit_database(
            user_id=user_id,
            scheduler_arguments={
                'напоминание : "брусья - 19:00 каждый вторник"': {
                    'trigger': 'cron',
                    'day_of_week': 'tue',
                }
            },
        )

        # 04.08.2026 — вторник: недельное напоминание попадает в расписание дня.
        # Название с суффиксом «каждый вторник» — ровно как в обычном заполнении,
        # иначе дело не совпало бы с историей в аналитике
        tasks, tasks_not_time = await build_day_schedule(user_id, date(2026, 8, 4))
        assert tasks == {'08:00': 'зарядка', '19:00': 'брусья каждый вторник'}
        assert tasks_not_time == ['прогулка']

        # 05.08.2026 — среда: только постоянные дела
        tasks_wed, not_time_wed = await build_day_schedule(user_id, date(2026, 8, 5))
        assert tasks_wed == {'08:00': 'зарядка'}
        assert not_time_wed == ['прогулка']


@pytest.mark.asyncio
async def test_refresh_personal_records_bumps_streak_after_gap_filled():
    async with test_db():
        user_id = '900007'
        await db_module.create_profile(user_id)
        await db_module.edit_database(user_id=user_id, personal_records={'бег': 2})
        for iso in ('2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04'):
            await _add_log(user_id, iso, activities='бег')

        records, improved = await refresh_personal_records(user_id)
        assert improved == {'бег': 4}
        assert records['бег'] == 4

        # Повторный пересчёт ничего не двигает
        records_again, improved_again = await refresh_personal_records(user_id)
        assert improved_again == {}
        assert records_again['бег'] == 4


@pytest.mark.asyncio
async def test_refresh_personal_records_keeps_record_when_streak_broken():
    async with test_db():
        user_id = '900008'
        await db_module.create_profile(user_id)
        await db_module.edit_database(user_id=user_id, personal_records={'бег': 5})
        await _add_log(user_id, '2026-08-01', activities='бег')
        await _add_log(user_id, '2026-08-02', activities='другое')  # серия оборвана
        await _add_log(user_id, '2026-08-03', activities='бег')

        records, improved = await refresh_personal_records(user_id)
        assert improved == {}
        assert records['бег'] == 5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
