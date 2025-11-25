"""Общие обработчики: навигация, старт, ошибки."""
import logging
import traceback
from types import SimpleNamespace

from aiogram import Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message
from aiogram.types.error_event import ErrorEvent

from config import bot, ClientState, has_user_data
from functions import start
from keys import ADMIN_ID

router = Router(name="common")


class MessageProxy:
    """Прокси для отправки сообщений без объекта Message."""
    def __init__(self, chat_id: int, from_user, bot):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = from_user
        self.bot = bot

    async def answer(self, text, **kwargs):
        return await self.bot.send_message(self.chat.id, text, **kwargs)

    async def answer_document(self, *args, **kwargs):
        return await self.bot.send_document(self.chat.id, *args, **kwargs)

    async def answer_sticker(self, sticker, **kwargs):
        return await self.bot.send_sticker(self.chat.id, sticker, **kwargs)


@router.message(lambda message: message.text and message.text.lower() == 'в главное меню')
async def go_to_main_menu(message: Message, state: FSMContext) -> None:
    """Возврат в главное меню."""
    await start(message=message, state=state)


@router.message(lambda message: message.text and message.text == '/reset')
async def reset_state(message: Message, state: FSMContext) -> None:
    """Сброс состояния пользователя (для отладки)."""
    await state.clear()
    await message.answer('Состояние сброшено. Напишите что-нибудь для начала.')


@router.message(lambda message: message.text)
async def handle_message(message: Message, state: FSMContext):
    """Fallback handler для любых текстовых сообщений."""
    await start(message=message, state=state)


async def on_error_handler(event: ErrorEvent):
    """Обработчик ошибок - отправляет traceback администратору."""
    logging.error("Произошла ошибка в боте!", exc_info=event.exception)

    tb_str = "".join(traceback.format_exception(
        type(event.exception), event.exception, event.exception.__traceback__
    ))

    short_error_message = (
        f"<b>❗️ Произошла ошибка!</b>\n\n"
        f"<b>Тип:</b> {type(event.exception).__name__}\n"
        f"<b>Текст:</b> {event.exception}\n\n"
        f"Полный traceback и данные Update в прикрепленных файлах."
    )

    traceback_file = BufferedInputFile(tb_str.encode('utf-8'), filename="traceback.txt")
    update_file = BufferedInputFile(
        event.update.model_dump_json(indent=2, exclude_none=True).encode('utf-8'),
        filename="update.json"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=short_error_message, parse_mode='HTML')
        await bot.send_document(chat_id=ADMIN_ID, document=traceback_file)
        await bot.send_document(chat_id=ADMIN_ID, document=update_file)

        # Уведомляем пользователя
        user_chat_id = None
        try:
            if event.update.message:
                user_chat_id = event.update.message.chat.id
            elif event.update.callback_query and event.update.callback_query.message:
                user_chat_id = event.update.callback_query.message.chat.id
        except Exception:
            pass
        
        if user_chat_id:
            await bot.send_message(
                chat_id=user_chat_id,
                text="😕 Ой, что-то пошло не так. Я уже сообщил разработчику о проблеме."
            )

    except Exception as e:
        logging.error(f"Критическая ошибка: не удалось отправить уведомление. Причина: {e}")
