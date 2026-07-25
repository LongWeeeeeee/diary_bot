"""Обработчики для работы с дневником."""
import logging
import os

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

from config import (
    bot, ClientState, has_user_data, TARGET_TZ,
    NEGATIVE_RESPONSES, MIN_DIARY_MESSAGE_LENGTH, PERSONAL_RATE_MIN, PERSONAL_RATE_MAX
)
from functions import (
    diary_out, add_day_to_excel, diary_excel_path, export_diary_excel,
    keyboard_builder, send_diary_analysis, send_word_report, start,
    tasks_pool_function
)
from sqlite import edit_database, replace_one_time_tasks, batch_update_tasks

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


@router.message(
    lambda message: message.text
    and message.text.lower().replace('📊', '').strip() == 'анализ'
)
async def diary_analysis(message: Message, state: FSMContext) -> None:
    """Разбор дневника: что связано с хорошими и плохими днями."""
    user_data = await state.get_data()
    if not has_user_data(user_data):
        await start(message=message, state=state)
        return
    await send_diary_analysis(message)
    await state.set_state(ClientState.greet)


@router.message(lambda message: message.text and message.text.lower().strip() in ('слова', 'все слова'))
async def diary_word_report(message: Message, state: FSMContext) -> None:
    """Полный список слов из «о дне» с их влиянием на оценку дня."""
    user_data = await state.get_data()
    if not has_user_data(user_data):
        await start(message=message, state=state)
        return
    await send_word_report(message)
    await state.set_state(ClientState.greet)


@router.message(lambda message: message.text and message.text.lower() == 'скачать дневник')
async def download_diary(message: Message, state: FSMContext):
    """Скачивание дневника в Excel."""
    user_data = await state.get_data()
    if not user_data:
        await start(message=message, state=state)
        return
    
    file_path = diary_excel_path(message.from_user.id)
    try:
        if not os.path.exists(file_path):
            # Пересобираем файл из БД. Раньше здесь вызывался add_day_to_excel,
            # который писал в daily_logs пустую запись за сегодня и затирал настоящую.
            await export_diary_excel(message.from_user.id)

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
                'В чем ты лучше себя вчерашнего? Не обязательно быть супер-продуктивным, '
                'достаточно хотя бы мизерного процента и ты уже не зря прожил этот день. '
                'Также можешь выгрузить свои эмоции за этот день, это помогает расслабиться '
                'и не крутить в голове эти мысли'
            )
            await state.set_state(ClientState.about_day)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(sleep_quality=0)
        await message.answer(
            'В чем ты лучше себя вчерашнего? Не обязательно быть супер-продуктивным, '
            'достаточно хотя бы мизерного процента и ты уже не зря прожил этот день. '
            'Также можешь выгрузить свои эмоции за этот день, это помогает расслабиться '
            'и не крутить в голове эти мысли'
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
    user_id_str = str(call.from_user.id)
    
    today_tasks = user_data.get('today_tasks', {})
    today_tasks_chosen = user_data.get('today_tasks_chosen', [])
    activities = [today_tasks[key] for key in today_tasks_chosen if key in today_tasks]

    # Загружаем актуальные one_time_tasks из БД
    from sqlite import get_one_time_tasks
    one_time_tasks = await get_one_time_tasks(user_id_str)
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    # Галочки на делах без времени лежат в today_tasks_not_time_chosen.
    # Раньше здесь читался daily_tasks_not_time_chosen — он заполняется только
    # при удалении дел, поэтому дела без времени вообще не попадали в дневник.
    not_time_chosen = [
        task for task in user_data.get('today_tasks_not_time_chosen', [])
        if task in today_tasks_not_time
    ]
    activities += not_time_chosen
    activities = list(dict.fromkeys(activities))
    personal_rate = user_data.get('personal_rate', None)
    
    # Всегда используем from_user из call, т.к. call.message.from_user — это бот
    if call.message:
        message = MessageProxy(chat_id=call.message.chat.id, from_user=call.from_user, bot=bot)
    else:
        ctx = user_data.get('message_ctx', {})
        chat_id = ctx.get('chat_id', call.from_user.id)
        message = MessageProxy(chat_id=chat_id, from_user=call.from_user, bot=bot)
    
    # Собираем все обновления для батчинга
    db_profile_updates = {}
    batch_tasks_updates = {}
    
    # Удаляем только ВЫПОЛНЕННЫЕ разовые дела (отмеченные в chosen)
    if one_time_tasks:
        completed_one_time = set()
        # Проверяем выполненные дела с временем
        for time_key in today_tasks_chosen:
            task_name = today_tasks.get(time_key)
            if task_name and task_name in one_time_tasks:
                completed_one_time.add(task_name)
        # Проверяем выполненные дела без времени
        for task in not_time_chosen:
            if task in one_time_tasks:
                completed_one_time.add(task)
        
        if completed_one_time:
            logging.info(f"Removing completed one_time_tasks: {completed_one_time}")
            remaining_one_time = [t for t in one_time_tasks if t not in completed_one_time]
            batch_tasks_updates['one_time_tasks'] = remaining_one_time
            
            # Убираем выполненные разовые дела из today_tasks
            today_tasks = {k: v for k, v in today_tasks.items() if v not in completed_one_time}
            today_tasks_not_time = [t for t in today_tasks_not_time if t not in completed_one_time]
            
            daily_tasks = user_data.get('daily_tasks', {})
            daily_tasks_not_time = user_data.get('daily_tasks_not_time', [])
            tasks_pool_set = set(user_data.get('tasks_pool', []))
            # Фильтруем: убираем выполненные разовые и задачи не из tasks_pool
            daily_tasks = {k: v for k, v in daily_tasks.items() 
                          if v not in completed_one_time and v in tasks_pool_set}
            daily_tasks_not_time = [t for t in daily_tasks_not_time 
                                   if t not in completed_one_time and t in tasks_pool_set]
            
            db_profile_updates['daily_tasks'] = daily_tasks
            db_profile_updates['daily_tasks_not_time'] = daily_tasks_not_time
    
    data_for_excel = {
        # Дата записи — всегда в TZ расписания (MSK), а не в локальной TZ сервера
        'date': datetime.datetime.now(TARGET_TZ),
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
        db_profile_updates['previous_diary'] = send_message.message_id
    if answer is not None:
        db_profile_updates['personal_records'] = answer
    
    # Выполняем батч-обновления БД
    if batch_tasks_updates:
        await batch_update_tasks(user_id_str, **batch_tasks_updates)
    if db_profile_updates:
        await edit_database(**db_profile_updates, user_id=call.from_user.id)
    
    previous_diary = user_data.get('previous_diary', None)
    if previous_diary:
        try:
            await bot.delete_message(message.chat.id, previous_diary)
        except Exception as e:
            logging.debug(f"Could not delete previous diary message: {e}")
    
    # Сохраняем дату расписания для которой отправляем дневник
    # Это нужно чтобы при следующем открытии расписания система знала что дневник за этот день отправлен
    submitted_for_date = user_data.get('today_tasks_date', None)
    
    # Один вызов update_data для сброса состояния
    await state.update_data(
        today_tasks_chosen=[], today_tasks_not_time_chosen=[], 
        one_time_chosen_tasks=[], session_accrued_tasks=[],
        today_tasks={}, today_tasks_not_time=[], sunrise=None,
        today_tasks_date=None,
        today_tasks_deleted=[],  # Сбрасываем удалённые дела
        diary_submitted_date=submitted_for_date,  # Отмечаем что дневник отправлен за дату расписания
        one_time_tasks=batch_tasks_updates.get('one_time_tasks', one_time_tasks),
        daily_tasks=db_profile_updates.get('daily_tasks', user_data.get('daily_tasks', {})),
        daily_tasks_not_time=db_profile_updates.get('daily_tasks_not_time', user_data.get('daily_tasks_not_time', []))
    )
    
    await start(message=message, state=state)
