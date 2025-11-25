"""Тесты для config.py"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    day_to_prefix, has_user_data, should_task_run_today,
    TARGET_TZ, WEEKDAY_TRANSLATE, NEGATIVE_RESPONSES
)


class TestDayToPrefix:
    """Тесты для day_to_prefix."""
    
    def test_monday(self):
        assert day_to_prefix('понедельник') == 'каждый'
    
    def test_friday(self):
        assert day_to_prefix('пятницу') == 'каждую'
    
    def test_sunday(self):
        assert day_to_prefix('воскресенье') == 'каждое'
    
    def test_unknown_day(self):
        assert day_to_prefix('несуществующий') == 'каждый'


class TestHasUserData:
    """Тесты для has_user_data."""
    
    def test_none(self):
        assert has_user_data(None) is False
    
    def test_empty_dict(self):
        assert has_user_data({}) is False
    
    def test_non_dict(self):
        assert has_user_data([1, 2, 3]) is False
        assert has_user_data("string") is False
    
    def test_valid_dict(self):
        assert has_user_data({'key': 'value'}) is True
        assert has_user_data({'tasks_pool': []}) is True


class TestShouldTaskRunToday:
    """Тесты для should_task_run_today."""
    
    def test_weekly_task_matches(self):
        # Создаём дату понедельника
        monday = datetime(2025, 1, 6, 12, 0, tzinfo=TARGET_TZ)  # 6 января 2025 - понедельник
        values = {'day_of_week': 'mon'}
        assert should_task_run_today(values, monday) is True
    
    def test_weekly_task_not_matches(self):
        monday = datetime(2025, 1, 6, 12, 0, tzinfo=TARGET_TZ)
        values = {'day_of_week': 'tue'}
        assert should_task_run_today(values, monday) is False
    
    def test_monthly_task_matches(self):
        day_15 = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'day': '15'}
        assert should_task_run_today(values, day_15) is True
    
    def test_monthly_task_not_matches(self):
        day_15 = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'day': '20'}
        assert should_task_run_today(values, day_15) is False
    
    def test_yearly_task_matches(self):
        jan_15 = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'day': '15', 'month': '1'}
        assert should_task_run_today(values, jan_15) is True
    
    def test_yearly_task_not_matches(self):
        jan_15 = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'day': '15', 'month': '2'}
        assert should_task_run_today(values, jan_15) is False
    
    def test_onetime_task_matches(self):
        today = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'run_date': '2025-01-15T10:00:00+03:00'}
        assert should_task_run_today(values, today) is True
    
    def test_onetime_task_not_matches(self):
        today = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        values = {'run_date': '2025-01-16T10:00:00+03:00'}
        assert should_task_run_today(values, today) is False
    
    def test_empty_values(self):
        today = datetime(2025, 1, 15, 12, 0, tzinfo=TARGET_TZ)
        assert should_task_run_today({}, today) is False


class TestConstants:
    """Тесты констант."""
    
    def test_weekday_translate_has_all_days(self):
        days = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье']
        for day in days:
            assert day in WEEKDAY_TRANSLATE
    
    def test_negative_responses_contains_common(self):
        assert 'нет' in NEGATIVE_RESPONSES
        assert '-' in NEGATIVE_RESPONSES
        assert 'не' in NEGATIVE_RESPONSES


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
