"""Обработчики для работы с дневником."""
import logging
import os
from datetime import datetime as dt

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

from config import (
    bot, ClientState, has_user_data,
    NEGATIVE_RESPONSES, MIN_DIARY_MESSAGE_LENGTH, PERSONAL_RATE_MIN, PERSONAL_RATE_MAX
)
from functions import (
    diary_out, add_day_to_excel, keyboard_builder, start,
    tasks_pool_function
)
from sqlite import edit_database, replace_one_time_tasks

from .common import MessageProxy

router = Router(name="diary")


@router.message(lambda message: message.text and message.text.lower() == 'заполнить дневник')
async def fill_diary_handler(message: Message, state: FSMContext):
    """Обработчик команды заполнения дневника."""
    await tasks_pool_function(message, state)


@router.message(lambda message: message.text and message.text.lower() == 'вывести дневник')
async def diary_output(message: Message, state: FSMContext) -> None:
    """Вывод последних записей дневника."""
    user_data = await state.get_data()
    if has_user_data(user_data):
        await diary_out(message)
        await state.set_state(ClientState.greet)
    else:
        await start(message=message, state=state)


@router.message(lambda message: message.text and message.text.lower() == 'скачать дневник')
async def download_diary(message: Message, state: FSMContext):
    """Скачивание дневника в Excel."""
    user_data = await state.get_data()
    if not user_data:
        await start(message=message, state=state)
        return
    
    file_path = f'{message.from_user.id}_Diary.xlsx'
    try:
        if not os.path.exists(file_path):
            data_for_excel = {
                'activities': user_data.get('activities', []),
                'sleep_quality': user_data.get('sleep_quality', '-'),
                'personal_rate': user_data.get('personal_rate', '-'),
                'my_steps': user_data.get('my_steps', '-'),
                'tasks_pool': user_data.get('tasks_pool', []),
                'user_message': user_data.get('user_message', '-'),
                'excel_chosen_tasks': user_data.get('excel_chosen_tasks', []),
                'personal_records': user_data.get('personal_records', {}),
            }
            await add_day_to_excel(date=dt.now(), message=message, today=True, **data_for_excel)
        
        if os.path.exists(file_path):
            sent = await message.answer_document(
                document=FSInputFile(file_path),
                disable_content_type_detection=True
            )
            return sent
        else:
            await message.answer('Дневник еще не создан. Заполните его сначала!')
    except Exception as e:
        logging.error(f"Error sending diary file {file_path}: {e}")
        await message.answer('Ошибка при отправке файла. Попробуйте позже.')


async def get_valid_number(message: Message, state: FSMContext, field: str, prompt: str, 
                           next_state, min_val=None, max_val=None):
    """Валидация и сохранение числового ввода."""
    try:
        value = float(message.text.replace(',', '.'))
        if (min_val is not None and value < min_val) or (max_val is not None and value > max_val):
            raise ValueError(f"Число должно быть между {min_val} и {max_val}")
        await state.update_data(**{field: value})
        await message.answer(prompt)
        await state.set_state(next_state)
    except ValueError:
        await message.answer(f'"{message.text}" должно быть числом. Попробуйте снова (например, 12.5).')


@router.message(StateFilter(ClientState.steps))
async def process_steps(message: Message, state: FSMContext):
    """Обработка ввода шагов."""
    if not message.text:
        await message.answer('Пожалуйста, введите число шагов или "-" чтобы пропустить.')
        return
    if message.text.lower() in NEGATIVE_RESPONSES or message.text in NEGATIVE_RESPONSES:
        await state.update_data(my_steps=0.0)
        await message.answer('Введите индекс качества сна')
        await state.set_state(ClientState.total_sleep)
    else:
        await get_valid_number(message, state, 'my_steps', 'Введите индекс качества сна', 
                              ClientState.total_sleep, min_val=0)


@router.message(StateFilter(ClientState.total_sleep))
async def process_total_sleep(message: Message, state: FSMContext) -> None:
    """Обработка ввода качества сна."""
    if not message.text:
        await message.answer('Пожалуйста, введите индекс качества сна или "-" чтобы пропустить.')
        return
    if message.text.lower() not in NEGATIVE_RESPONSES and message.text not in NEGATIVE_RESPONSES:
        try:
            user_message = float(message.text.replace(',', '.'))
            await state.update_data(sleep_quality=user_message)
            await message.answer(
                'Подробно расскажи про свой день.\n'
                'Выгрузи все эмоции которые ты сегодня пережил и события связанные с ними. '
                'Это поможет тебе лучше заснуть'
            )
            await state.set_state(ClientState.about_day)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(sleep_quality=0)
        await message.answer(
            'Подробно расскажи про свой день.\n'
            'Выгрузи все эмоции которые ты сегодня пережил и события связанные с ними. '
            'Это поможет тебе лучше заснуть'
        )
        await state.set_state(ClientState.about_day)


@router.message(StateFilter(ClientState.about_day))
async def process_about_day(message: Message, state: FSMContext) -> None:
    """Обработка описания дня."""
    user_message = message.text
    if not user_message or len(user_message) < MIN_DIARY_MESSAGE_LENGTH:
        await message.answer('Расскажите подробнее про свой день, не ленитесь.')
    else:
        await state.update_data(user_message=message.text)
        await message.answer('Насколько из 10 оцениваете день?')
        await state.set_state(ClientState.personal_rate)


@router.message(StateFilter(ClientState.personal_rate))
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    """Обработка оценки дня."""
    if not message.text:
        await message.answer(f'Введите число от {PERSONAL_RATE_MIN} до {PERSONAL_RATE_MAX}')
        return
    try:
        personal_rate = int(message.text)
        if not (PERSONAL_RATE_MIN <= personal_rate <= PERSONAL_RATE_MAX):
            raise ValueError
    except (ValueError, TypeError):
        await message.answer(f'"{message.text}" должен быть числом от {PERSONAL_RATE_MIN} до {PERSONAL_RATE_MAX}')
        return
    
    await state.update_data(personal_rate=personal_rate, message_ctx={'chat_id': message.chat.id})
    await message.answer(
        'За вчера или за сегодня?', 
        reply_markup=keyboard_builder(tasks_list=['За вчера', 'За сегодня'], grid=2)
    )
    await state.set_state(ClientState.personal_rate_1)


@router.callback_query(StateFilter(ClientState.personal_rate_1))
async def personal_rate_1(call, state, flag=False) -> None:
    """Финализация записи дневника."""
    import datetime
    await call.answer()
    user_data = await state.get_data()
    data = call.data
    today = data != '0'
    
    db_updates = {}
    today_tasks = user_data.get('today_tasks', {})
    today_tasks_chosen = user_data.get('today_tasks_chosen', [])
    activities = [today_tasks[key] for key in today_tasks_chosen if key in today_tasks]
    # Загружаем актуальные one_time_tasks из БД
    from sqlite import get_one_time_tasks
    one_time_tasks = await get_one_time_tasks(str(call.from_user.id))
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    daily_tasks_not_time_chosen = user_data.get('daily_tasks_not_time_chosen', [])
    today_tasks_not_time_activities = [task for task in daily_tasks_not_time_chosen]
    activities += today_tasks_not_time_activities
    personal_rate = user_data.get('personal_rate', None)
    
    if call.message:
        message = call.message
    else:
        ctx = user_data.get('message_ctx', {})
        chat_id = ctx.get('chat_id', call.from_user.id)
        message = MessageProxy(chat_id=chat_id, from_user=call.from_user, bot=bot)
    
    # Удаляем только те разовые дела, которые были в расписании на сегодня
    logging.info(f"one_time_tasks: {one_time_tasks}")
    logging.info(f"today_tasks: {today_tasks}")
    logging.info(f"today_tasks_not_time: {today_tasks_not_time}")
    
    if one_time_tasks:
        # Собираем разовые дела которые были добавлены в расписание
        used_one_time = []
        for task_name in today_tasks.values():
            if task_name in one_time_tasks:
                used_one_time.append(task_name)
        for task in today_tasks_not_time:
            if task in one_time_tasks:
                used_one_time.append(task)
        
        logging.info(f"used_one_time: {used_one_time}")
        
        if used_one_time:
            logging.info(f"Removing used one_time_tasks: {used_one_time}")
            # Оставляем только неиспользованные разовые дела
            remaining_one_time = [t for t in one_time_tasks if t not in used_one_time]
            await replace_one_time_tasks(str(call.from_user.id), remaining_one_time)
            await state.update_data(one_time_tasks=remaining_one_time)
            db_updates['one_time_tasks'] = remaining_one_time
            
            # Удаляем использованные разовые дела из today_tasks и today_tasks_not_time
            today_tasks = {k: v for k, v in today_tasks.items() if v not in used_one_time}
            today_tasks_not_time = [t for t in today_tasks_not_time if t not in used_one_time]
            
            # И из daily_tasks (сохранённое расписание)
            daily_tasks = user_data.get('daily_tasks', {})
            daily_tasks_not_time = user_data.get('daily_tasks_not_time', [])
            daily_tasks = {k: v for k, v in daily_tasks.items() if v not in used_one_time}
            daily_tasks_not_time = [t for t in daily_tasks_not_time if t not in used_one_time]
            
            await state.update_data(
                today_tasks=today_tasks, today_tasks_not_time=today_tasks_not_time,
                daily_tasks=daily_tasks, daily_tasks_not_time=daily_tasks_not_time
            )
            db_updates['daily_tasks'] = daily_tasks
            db_updates['daily_tasks_not_time'] = daily_tasks_not_time
    
    data_for_excel = {
        'tasks_pool': user_data.get('tasks_pool', []),
        'date': datetime.datetime.now(),
        'activities': activities,
        'user_message': user_data.get('user_message', ''),
        'sleep_quality': user_data.get('sleep_quality', 0),
        'my_steps': user_data.get('my_steps', 0),
    }
    if 'personal_records' in user_data:
        data_for_excel['personal_records'] = user_data.get('personal_records', {})

    answer = await add_day_to_excel(message=message, personal_rate=personal_rate, **data_for_excel, today=today)
    send_message = await download_diary(message, state)
    
    if send_message:
        db_updates['previous_diary'] = send_message.message_id
    if answer:
        db_updates['personal_records'] = answer
    if db_updates:
        await edit_database(**db_updates, user_id=call.from_user.id)
    
    previous_diary = user_data.get('previous_diary', None)
    if previous_diary:
        try:
            await bot.delete_message(message.chat.id, previous_diary)
        except Exception as e:
            logging.debug(f"Could not delete previous diary message: {e}")
    
    await state.update_data(
        today_tasks_chosen=[], today_tasks_not_time_chosen=[], 
        one_time_chosen_tasks=[], session_accrued_tasks=[],
        today_tasks={}, today_tasks_not_time=[], sunrise=None,
        today_tasks_date=None  # Сбрасываем дату, чтобы при следующем открытии загрузились daily_tasks
    )
    
    await start(message=message, state=state)
