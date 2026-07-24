"""Тесты для functions.py"""
import pytest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import functions as functions_module
from functions import (
    normalized, parse_time_key, counter_positive, _plural_days,
    generate_keyboard, keyboard_builder, _is_scheduled_task,
    ensure_notification_job, get_notification_job_ids,
    notification_job_id, remove_notification_jobs, tasks_pool_function
)


class TestNormalized:
    """Тесты для normalized."""
    
    def test_basic(self):
        assert normalized("Привет") == "привет"
    
    def test_comma_spacing(self):
        assert normalized("a,b,c") == "a, b, c"
    
    def test_yo_replacement(self):
        assert normalized("Ёлка") == "елка"
    
    def test_strip(self):
        assert normalized("  test  ") == "test"


class TestParseTimeKey:
    """Тесты для parse_time_key."""
    
    def test_hours_only(self):
        assert parse_time_key("9") == 540  # 9 * 60
        assert parse_time_key("14") == 840  # 14 * 60
    
    def test_hours_minutes(self):
        assert parse_time_key("9:30") == 570  # 9 * 60 + 30
        assert parse_time_key("14:45") == 885  # 14 * 60 + 45
    
    def test_midnight(self):
        assert parse_time_key("0:00") == 0
    
    def test_end_of_day(self):
        assert parse_time_key("23:59") == 1439


class TestCounterPositive:
    """Тесты для counter_positive (серия подряд идущих календарных дней)."""

    def test_consecutive_days(self):
        history = [
            ("2026-07-01", "task1, task2"),
            ("2026-07-02", "task1, task2"),
            ("2026-07-03", "task1"),
        ]
        assert counter_positive("task1", history) == 3

    def test_broken_streak(self):
        history = [
            ("2026-07-01", "task1"),
            ("2026-07-02", "task2"),
            ("2026-07-03", "task1"),
        ]
        assert counter_positive("task1", history) == 1

    def test_no_match(self):
        history = [("2026-07-02", "task2"), ("2026-07-03", "task3")]
        assert counter_positive("task1", history) == 0

    def test_empty_column(self):
        assert counter_positive("task1", []) == 0

    def test_calendar_gap_breaks_streak(self):
        """Пропущенный день (нет записи) обрывает серию, а не склеивает её."""
        history = [
            ("2026-06-20", "task1"),
            ("2026-06-21", "task1"),
            ("2026-07-03", "task1"),
        ]
        assert counter_positive("task1", history) == 1

    def test_empty_day_breaks_streak(self):
        """День с пустым списком дел обрывает серию."""
        history = [
            ("2026-07-01", "task1"),
            ("2026-07-02", ""),
            ("2026-07-03", "task1"),
        ]
        assert counter_positive("task1", history) == 1

    def test_unsorted_history(self):
        history = [
            ("2026-07-03", "task1"),
            ("2026-07-01", "task1"),
            ("2026-07-02", "task1"),
        ]
        assert counter_positive("task1", history) == 3

    def test_extra_spaces_in_activities(self):
        history = [("2026-07-02", "task1 ,task2"), ("2026-07-03", " task1,task2 ")]
        assert counter_positive("task1", history) == 2

    def test_broken_date_ignored(self):
        history = [("не дата", "task1"), ("2026-07-03", "task1")]
        assert counter_positive("task1", history) == 1


class TestPluralDays:
    """Тесты для склонения слова «день»."""

    def test_forms(self):
        assert _plural_days(1) == "день"
        assert _plural_days(2) == "дня"
        assert _plural_days(5) == "дней"
        assert _plural_days(11) == "дней"
        assert _plural_days(21) == "день"
        assert _plural_days(22) == "дня"
        assert _plural_days(112) == "дней"


class TestGenerateKeyboard:
    """Тесты для generate_keyboard."""
    
    def test_basic_buttons(self):
        kb = generate_keyboard(["btn1", "btn2"])
        assert kb is not None
        assert len(kb.keyboard) == 1
        assert len(kb.keyboard[0]) == 2
    
    def test_with_last_button(self):
        kb = generate_keyboard(["btn1"], last_button="last")
        assert len(kb.keyboard) == 2
    
    def test_with_first_button(self):
        kb = generate_keyboard(["btn1"], first_button="first")
        assert len(kb.keyboard) == 2


class TestKeyboardBuilder:
    """Тесты для keyboard_builder."""
    
    def test_tasks_list_no_chosen(self):
        kb = keyboard_builder(tasks_list=["task1", "task2"])
        assert kb is not None
    
    def test_tasks_list_with_chosen(self):
        kb = keyboard_builder(tasks_list=["task1", "task2"], chosen=["task1"])
        assert kb is not None
    
    def test_tasks_dict(self):
        kb = keyboard_builder(tasks_dict={"9:00": "task1", "10:00": "task2"}, chosen=[])
        assert kb is not None
    
    def test_with_buttons(self):
        kb = keyboard_builder(tasks_list=["task1"], chosen=[], add_dell=True, add_save=True)
        assert kb is not None


class FakeJob:
    def __init__(self, job_id, func=None, args=()):
        self.id = job_id
        self.func = func
        self.args = args


class FakeScheduler:
    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.removed = []
        self.added = []

    def get_jobs(self):
        return list(self.jobs)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs = [job for job in self.jobs if job.id != job_id]

    def add_job(self, func, **kwargs):
        job = FakeJob(kwargs["id"], func=func, args=kwargs.get("args", ()))
        self.added.append({"func": func, **kwargs})
        self.jobs.append(job)
        return job


def _message_for_user(user_id):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=user_id),
    )


def _proxy_for_user(user_id):
    return SimpleNamespace(
        from_user=None,
        chat=SimpleNamespace(id=user_id),
        chat_id=user_id,
    )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class TestIsScheduledTask:
    """Тесты для _is_scheduled_task."""
    
    def test_regular_task(self):
        assert _is_scheduled_task("зарядка") is False
        assert _is_scheduled_task("завтрак") is False
    
    def test_task_with_pipe_only(self):
        # Задача с | но без "каждый" - НЕ scheduled
        assert _is_scheduled_task("Читать/слушать книгу | воздержание") is False
        assert _is_scheduled_task("задача | что-то") is False
    
    def test_task_with_kazhdiy_only(self):
        # Задача с "каждый" но без | - НЕ scheduled
        assert _is_scheduled_task("дело каждый день") is False
    
    def test_scheduled_task(self):
        # Настоящие scheduled задачи - с | И "каждый"
        assert _is_scheduled_task("подтягивания | брусья каждый четверг") is True
        assert _is_scheduled_task("задача | описание каждую пятницу") is True
        assert _is_scheduled_task("дело | тест каждое воскресенье") is True
    
    def test_empty_and_none(self):
        assert _is_scheduled_task("") is False
        assert _is_scheduled_task(None) is False


class TestNotificationJobs:
    def test_notification_job_id(self):
        assert notification_job_id(123) == "notify:123"

    def test_get_notification_job_ids_collects_stable_and_legacy_jobs(self, monkeypatch):
        fake_scheduler = FakeScheduler(
            [
                FakeJob("notify:42", func=tasks_pool_function, args=(_message_for_user(42), object())),
                FakeJob("legacy-42", func=tasks_pool_function, args=(_proxy_for_user(42), object())),
                FakeJob("legacy-7", func=tasks_pool_function, args=(_message_for_user(7), object())),
                FakeJob("date-job", func=object(), args=(_message_for_user(42), object())),
            ]
        )
        monkeypatch.setattr(functions_module, "scheduler", fake_scheduler)

        assert set(get_notification_job_ids(42)) == {"notify:42", "legacy-42"}

    def test_remove_notification_jobs_deletes_all_duplicates_for_user(self, monkeypatch):
        fake_scheduler = FakeScheduler(
            [
                FakeJob("notify:42", func=tasks_pool_function, args=(_message_for_user(42), object())),
                FakeJob("legacy-42", func=tasks_pool_function, args=(_proxy_for_user(42), object())),
                FakeJob("legacy-7", func=tasks_pool_function, args=(_message_for_user(7), object())),
            ]
        )
        monkeypatch.setattr(functions_module, "scheduler", fake_scheduler)

        removed = remove_notification_jobs(42)

        assert set(removed) == {"notify:42", "legacy-42"}
        assert set(fake_scheduler.removed) == {"notify:42", "legacy-42"}
        assert [job.id for job in fake_scheduler.jobs] == ["legacy-7"]

    def test_ensure_notification_job_replaces_duplicates_with_single_stable_job(self, monkeypatch):
        fake_scheduler = FakeScheduler(
            [
                FakeJob("legacy-42", func=tasks_pool_function, args=(_proxy_for_user(42), object())),
                FakeJob("notify:42", func=tasks_pool_function, args=(_message_for_user(42), object())),
            ]
        )
        monkeypatch.setattr(functions_module, "scheduler", fake_scheduler)

        job = ensure_notification_job(
            user_id=42,
            hours=10,
            minutes=0,
            message=_message_for_user(42),
            state=object(),
        )

        assert job.id == "notify:42"
        assert set(fake_scheduler.removed) == {"legacy-42", "notify:42"}
        assert len(fake_scheduler.added) == 1
        assert fake_scheduler.added[0]["trigger"] == "cron"
        assert fake_scheduler.added[0]["hour"] == 10
        assert fake_scheduler.added[0]["minute"] == 0
        assert fake_scheduler.added[0]["replace_existing"] is True
        assert [stored_job.id for stored_job in fake_scheduler.jobs] == ["notify:42"]


class _FakeBot:
    def __init__(self):
        self.pinned = []

    async def pin_chat_message(self, chat_id, message_id):
        self.pinned.append((chat_id, message_id))


class _FakeMessage:
    def __init__(self):
        self.sent = []
        self.chat = SimpleNamespace(id=1)
        self.bot = _FakeBot()

    async def answer(self, text, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))


class TestCounterMaxDays:
    """Тесты для итогового сообщения по сериям."""

    @pytest.mark.asyncio
    async def test_reports_streaks_without_negative_block(self):
        message = _FakeMessage()
        history = [
            ("2026-07-01", "Растяжка, Бег"),
            ("2026-07-02", "Растяжка"),
            ("2026-07-03", "Растяжка, Бег"),
        ]
        records = await functions_module.counter_max_days(
            activity_history=history,
            message=message,
            activities=["Растяжка", "Бег"],
            personal_records={},
        )
        assert len(message.sent) == 1
        text = message.sent[0]
        assert "не делали" not in text
        assert "Растяжка : 3 дня" in text
        assert "Бег" not in text  # серия Бега прервалась 2 июля
        assert records["Растяжка"] == 3
        assert records["Бег"] == 1

    @pytest.mark.asyncio
    async def test_records_survive_without_streaks(self):
        message = _FakeMessage()
        history = [("2026-07-03", "Бег")]
        records = await functions_module.counter_max_days(
            activity_history=history,
            message=message,
            activities=["Бег"],
            personal_records={"Бег": 4},
        )
        assert records == {"Бег": 4}
        assert "Отличное начало" in message.sent[0]

    @pytest.mark.asyncio
    async def test_empty_history(self):
        message = _FakeMessage()
        records = await functions_module.counter_max_days(
            activity_history=[],
            message=message,
            activities=["Бег"],
            personal_records={"Бег": 2},
        )
        assert records == {"Бег": 2}
        assert message.sent == ["Поздравляю! дневник заполнен"]


class TestDedupePreserveOrder:
    """Порядок дел не должен тасоваться (раньше был list(set(...)))."""

    def test_keeps_first_occurrence_order(self):
        assert functions_module.dedupe_preserve_order(
            ["ужин", "бег", "ужин", "сон"]
        ) == ["ужин", "бег", "сон"]

    def test_empty(self):
        assert functions_module.dedupe_preserve_order([]) == []


class TestFormatRecords:
    """Рекорды выводятся со склонением и без мусора."""

    def test_plural_and_filtering(self):
        out = functions_module.format_records(
            {"Бег": 3, "Сон": 1, "Пусто": 0, "Кривое": "abc"}
        )
        assert out == "Бег : 3 дня\nСон : 1 день"

    def test_empty(self):
        assert functions_module.format_records({}) == ""
        assert functions_module.format_records(None) == ""


class TestChunkLines:
    """Нарезка длинного дневника не должна резать строки посередине."""

    def test_splits_on_line_boundary(self):
        lines = ["x" * 1000 for _ in range(10)]
        chunks = functions_module._chunk_lines(lines, limit=2500)
        assert len(chunks) == 5
        for chunk in chunks:
            assert len(chunk) <= 2500
            # каждая строка целая
            assert all(len(part) == 1000 for part in chunk.split("\n"))

    def test_single_long_line_is_cut(self):
        chunks = functions_module._chunk_lines(["y" * 5000], limit=2000)
        assert "".join(chunks) == "y" * 5000


class TestDiaryOutEscaping:
    """Вывод дневника не должен ломаться на HTML в тексте пользователя."""

    @pytest.mark.asyncio
    async def test_escapes_user_text(self, monkeypatch):
        async def fake_logs(user_id, limit=7):
            return [("2026-07-03", "Бег & <ходьба>", 1000.0, 5.0, "день был <b>ок</b>", 7.0)]

        monkeypatch.setattr(functions_module, "get_last_logs", fake_logs)
        message = _FakeMessage()
        message.from_user = SimpleNamespace(id=1)
        await functions_module.diary_out(message)
        text = "\n".join(message.sent)
        assert "&lt;b&gt;" in text and "<b>ок</b>" not in text
        assert "&amp;" in text

    @pytest.mark.asyncio
    async def test_bad_personal_rate_does_not_crash(self, monkeypatch):
        async def fake_logs(user_id, limit=7):
            return [("2026-07-03", "Бег", None, None, "-", "-")]

        monkeypatch.setattr(functions_module, "get_last_logs", fake_logs)
        message = _FakeMessage()
        message.from_user = SimpleNamespace(id=1)
        await functions_module.diary_out(message)
        assert "0/10" in "\n".join(message.sent)
