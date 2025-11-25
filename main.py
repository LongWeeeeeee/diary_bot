"""Основной модуль бота-дневника."""
import asyncio
import logging
import signal
import sys

from config import bot, dp, scheduler
from sqlite import database_start
from handlers import router
from handlers.common import on_error_handler

logger = logging.getLogger(__name__)


async def shutdown(sig: signal.Signals = None):
    """Graceful shutdown."""
    if sig:
        logger.info(f"Получен сигнал {sig.name}, завершаем работу...")
    
    logger.info("Останавливаем планировщик...")
    scheduler.shutdown(wait=False)
    
    logger.info("Закрываем сессию бота...")
    await bot.session.close()
    
    logger.info("Бот остановлен.")


async def clear_redis_cache():
    """Очищает кэш Redis."""
    from config import storage
    try:
        await storage.redis.flushdb()
        print("Кэш Redis очищен")
    except Exception as e:
        print(f"Ошибка очистки кэша: {e}")


async def check_redis():
    """Проверяет подключение к Redis."""
    from config import storage
    try:
        await storage.redis.ping()
        logger.info("Redis подключен успешно")
        return True
    except Exception as e:
        print("\n" + "=" * 70)
        print("ОШИБКА: Не удалось подключиться к Redis!")
        print(f"Причина: {e}")
        print("-" * 70)
        print("Запустите Redis одним из способов:")
        print("  • brew services start redis     (macOS с Homebrew)")
        print("  • sudo systemctl start redis    (Linux)")
        print("  • redis-server                  (вручную)")
        print("  • docker run -d -p 6379:6379 redis  (Docker)")
        print("=" * 70 + "\n")
        return False


async def main():
    """Точка входа приложения."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    )

    # Проверяем Redis
    if not await check_redis():
        return
    
    # Сброс кэша при запуске с аргументом --clear-cache
    if '--clear-cache' in sys.argv:
        await clear_redis_cache()
    
    print("Сброс кэша Redis: redis-cli FLUSHDB")

    # Обработка сигналов завершения
    loop = asyncio.get_event_loop()
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    # Подключаем роутеры из handlers
    dp.include_router(router)
    
    # Регистрация обработчика ошибок
    dp.errors.register(on_error_handler)
    logger.info("Бот запускается...")

    try:
        scheduler.start()
        await database_start()
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал завершения")
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
