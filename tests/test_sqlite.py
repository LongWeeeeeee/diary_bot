"""Тесты для sqlite.py"""
import pytest
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Используем тестовую БД
import sqlite as db_module


@pytest.fixture
def event_loop():
    """Создаём event loop для тестов."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_db(event_loop):
    """Сбрасывает пул соединений перед каждым тестом."""
    event_loop.run_until_complete(db_module.reset_pool())
    yield
    event_loop.run_until_complete(db_module.reset_pool())


@pytest.mark.asyncio
async def test_database_start():
    """Тест инициализации БД."""
    await db_module.database_start()
    # Если не упало - тест пройден


@pytest.mark.asyncio
async def test_create_profile():
    """Тест создания профиля."""
    db_module.DB_PATH = 'test_diary.db'
    try:
        await db_module.database_start()
        user = await db_module.create_profile('test_user_123')
        assert user is not None
        assert user[0] == 'test_user_123'
    finally:
        if os.path.exists('test_diary.db'):
            os.remove('test_diary.db')


@pytest.mark.asyncio
async def test_tasks_pool_crud():
    """Тест CRUD операций для tasks_pool."""
    db_module.DB_PATH = 'test_diary.db'
    try:
        await db_module.database_start()
        user_id = 'test_user_456'
        
        # Create
        await db_module.replace_tasks_pool(user_id, ['Task 1', 'Task 2', 'Task 3'])
        
        # Read
        tasks = await db_module.get_tasks_pool(user_id)
        assert len(tasks) == 3
        assert 'Task 1' in tasks
        assert 'Task 2' in tasks
        assert 'Task 3' in tasks
        
        # Update
        await db_module.replace_tasks_pool(user_id, ['Task 1', 'Task 4'])
        tasks = await db_module.get_tasks_pool(user_id)
        assert len(tasks) == 2
        assert 'Task 4' in tasks
        assert 'Task 2' not in tasks
        
        # Delete (replace with empty)
        await db_module.replace_tasks_pool(user_id, [])
        tasks = await db_module.get_tasks_pool(user_id)
        assert len(tasks) == 0
    finally:
        if os.path.exists('test_diary.db'):
            os.remove('test_diary.db')


@pytest.mark.asyncio
async def test_one_time_tasks():
    """Тест разовых задач."""
    db_module.DB_PATH = 'test_diary.db'
    try:
        await db_module.database_start()
        user_id = 'test_user_789'
        
        await db_module.replace_one_time_tasks(user_id, ['OneTime 1', 'OneTime 2'])
        tasks = await db_module.get_one_time_tasks(user_id)
        assert len(tasks) == 2
        
        await db_module.remove_one_time_task(user_id, 'OneTime 1')
        tasks = await db_module.get_one_time_tasks(user_id)
        assert len(tasks) == 1
        assert 'OneTime 2' in tasks
    finally:
        if os.path.exists('test_diary.db'):
            os.remove('test_diary.db')


@pytest.mark.asyncio
async def test_daily_log():
    """Тест записей дневника."""
    db_module.DB_PATH = 'test_diary.db'
    try:
        await db_module.database_start()
        user_id = 'test_user_log'
        
        await db_module.add_daily_log(
            user_id=user_id,
            date='2025-01-15',
            activities='Task1,Task2',
            steps=10000,
            sleep_quality=8.5,
            about_day='Great day!',
            personal_rate=9
        )
        
        logs = await db_module.get_last_logs(user_id, limit=1)
        assert len(logs) == 1
        assert logs[0][0] == '2025-01-15'
        assert logs[0][2] == 10000  # steps
        
        all_logs = await db_module.get_all_logs(user_id)
        assert len(all_logs) == 1
    finally:
        if os.path.exists('test_diary.db'):
            os.remove('test_diary.db')


@pytest.mark.asyncio
async def test_user_id_standardization():
    """Тест что user_id всегда приводится к str."""
    db_module.DB_PATH = 'test_diary.db'
    try:
        await db_module.database_start()
        
        # Передаём int, должен работать
        await db_module.replace_tasks_pool(123456, ['Task'])
        tasks = await db_module.get_tasks_pool(123456)
        assert len(tasks) == 1
        
        # Проверяем что можно получить по str тоже
        tasks = await db_module.get_tasks_pool('123456')
        assert len(tasks) == 1
    finally:
        if os.path.exists('test_diary.db'):
            os.remove('test_diary.db')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
