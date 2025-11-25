"""Модуль обработчиков бота."""
from aiogram import Router

from . import common, diary, settings, tasks, scheduler_handlers

# Главный роутер, объединяющий все обработчики
router = Router(name="main")

# Порядок важен: более специфичные роутеры должны быть первыми
router.include_router(diary.router)
router.include_router(settings.router)
router.include_router(tasks.router)
router.include_router(scheduler_handlers.router)
router.include_router(common.router)  # Общие хендлеры последними (fallback)
