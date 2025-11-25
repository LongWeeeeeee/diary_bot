"""Тесты для functions.py"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from functions import (
    normalized, parse_time_key, counter_positive, counter_negative,
    generate_keyboard, keyboard_builder, _is_scheduled_task
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
    """Тесты для counter_positive."""
    
    def test_consecutive_days(self):
        column = ["task1, task2", "task1, task2", "task1"]
        assert counter_positive("task1", column) == 3
    
    def test_broken_streak(self):
        column = ["task1", "task2", "task1"]
        assert counter_positive("task1", column) == 1
    
    def test_no_match(self):
        column = ["task2", "task3"]
        assert counter_positive("task1", column) == 0
    
    def test_empty_column(self):
        assert counter_positive("task1", []) == 0


class TestCounterNegative:
    """Тесты для counter_negative."""
    
    def test_days_since_last(self):
        column = ["task1", "task2", "task2"]
        assert counter_negative(column, "task1") == 2
    
    def test_done_today(self):
        column = ["task1", "task2", "task1"]
        assert counter_negative(column, "task1") == 0
    
    def test_never_done(self):
        column = ["task2", "task3", "task4"]
        assert counter_negative(column, "task1") == 3


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
