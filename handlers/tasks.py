"""Обработчики для работы с задачами."""
from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import bot, ClientState
from functions import (
    generate_keyboard, keyboard_builder, tasks_pool_function, 
    start, normalized, _is_scheduled_task
)
from sqlite import (
    get_tasks_pool, replace_tasks_pool, get_one_time_tasks, 
    replace_one_time_tasks, edit_database
)

router = Router(name="tasks")


@router.message(lambda message: message.text and message.text.lower() == 'список дел', StateFilter(ClientState.settings))
async def edit_tasks_pool_handler(message: Message, state: FSMContext):
    """Редактирование общего списка дел."""
    user_data = await state.get_data()
    user_id = str(message.from_user.id)
    tasks_pool = await get_tasks_pool(user_id)
    await state.update_data(tasks_pool=tasks_pool)
    edit_tasks_pool_chosen = user_data.get('edit_tasks_pool_chosen', [])
    await state.update_data(tasks_to_delete=[])

    keyboard = keyboard_builder(tasks_list=tasks_pool, add_dell=True, chosen=edit_tasks_pool_chosen)
    await message.answer("Ваш общий список дел", reply_markup=keyboard)
    await state.set_state(ClientState.edit_tasks_pool)


@router.callback_query(StateFilter(ClientState.edit_tasks_pool))
async def process_edit_tasks_pool_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка редактирования списка дел."""
    await call.answer()
    user_id = str(call.from_user.id)
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    today_tasks = user_data.get('today_tasks', {})
    daily_tasks = user_data.get('daily_tasks', {})
    daily_chosen_tasks = user_data.get('daily_chosen_tasks', [])
    edit_tasks_pool_chosen = user_data.get('edit_tasks_pool_chosen', [])
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    daily_tasks_not_time_chosen = user_data.get('daily_tasks_not_time_chosen', [])

    if call.data == "Удалить":
        if not edit_tasks_pool_chosen:
            await call.answer("Вы ничего не выбрали для удаления.", show_alert=True)
            return
        
        today_tasks_copy, daily_tasks_copy = today_tasks.copy(), daily_tasks.copy()
        today_tasks_chosen = user_data.get('today_tasks_chosen', [])
        today_tasks_not_time_chosen = user_data.get('today_tasks_not_time_chosen', [])
        for name in edit_tasks_pool_chosen:
            if name in tasks_pool:
                tasks_pool.remove(name)
            if name in today_tasks.values():
                for key in today_tasks.keys():
                    if today_tasks[key] == name:
                        if key in daily_chosen_tasks:
                            daily_chosen_tasks.remove(key)
                        if key in today_tasks_chosen:
                            today_tasks_chosen.remove(key)
                        del today_tasks_copy[key]
            if name in today_tasks_not_time:
                today_tasks_not_time.remove(name)
            if name in today_tasks_not_time_chosen:
                today_tasks_not_time_chosen.remove(name)
            if name in daily_tasks_not_time_chosen:
                daily_tasks_not_time_chosen.remove(name)
            if name in daily_tasks.values():
                for key in daily_tasks.keys():
                    if daily_tasks[key] == name:
                        if key in daily_chosen_tasks:
                            daily_chosen_tasks.remove(key)
                        del daily_tasks_copy[key]
            await call.message.answer(f'Вы удалили "{name}"')

        keyboard = keyboard_builder(tasks_list=tasks_pool, add_dell=True, chosen=edit_tasks_pool_chosen)
        daily_tasks_not_time = user_data.get('daily_tasks_not_time', [])
        daily_tasks_not_time = [task for task in daily_tasks_not_time if task in tasks_pool]
        
        await replace_tasks_pool(user_id, tasks_pool)
        await state.update_data(
            tasks_pool=tasks_pool, daily_tasks=daily_tasks_copy, 
            daily_chosen_tasks=daily_chosen_tasks, today_tasks=today_tasks_copy, 
            edit_tasks_pool_chosen=[], today_tasks_not_time=today_tasks_not_time,
            daily_tasks_not_time_chosen=daily_tasks_not_time_chosen, 
            daily_tasks_not_time=daily_tasks_not_time,
            today_tasks_chosen=today_tasks_chosen,
            today_tasks_not_time_chosen=today_tasks_not_time_chosen
        )
        await edit_database(
            daily_tasks=daily_tasks_copy, 
            daily_tasks_not_time=daily_tasks_not_time, 
            user_id=call.from_user.id
        )
        try:
            await call.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise
    
    elif call.data == 'Добавить':
        await call.message.answer('Введите список дел который хотите добавить через запятую')
        await state.set_state(ClientState.add_tasks_pool)
        await state.update_data(tasks_pool_keyboard={
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id
        })
    
    else:
        try:
            data = int(call.data)
            if data < 0 or data >= len(tasks_pool):
                await call.answer("Задача не найдена.", show_alert=True)
                return
        except (ValueError, TypeError):
            await call.answer("Некорректный выбор.", show_alert=True)
            return
        if tasks_pool[data] in edit_tasks_pool_chosen:
            edit_tasks_pool_chosen.remove(tasks_pool[data])
        else:
            edit_tasks_pool_chosen.append(tasks_pool[data])
        keyboard = keyboard_builder(tasks_list=tasks_pool, chosen=edit_tasks_pool_chosen, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)
        await state.update_data(edit_tasks_pool_chosen=edit_tasks_pool_chosen)


@router.message(StateFilter(ClientState.add_tasks_pool))
async def add_tasks_pool(message, state: FSMContext):
    """Добавление новых дел в список."""
    if not message.text:
        await message.answer('Введите список дел через запятую.')
        return
    
    # Проверка на кнопку "В Главное Меню"
    if message.text.lower() == 'в главное меню':
        await start(message=message, state=state)
        return
    
    data = message.text
    normalized_tasks = [item.strip() for item in data.split(',') if item.strip()]
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    
    for word in normalized_tasks:
        tasks_pool.append(word)
    tasks_pool = list(set(tasks_pool))
    
    keyboard = keyboard_builder(tasks_list=tasks_pool, add_dell=True)
    keyboard_ctx = user_data.get('tasks_pool_keyboard')
    if keyboard_ctx:
        try:
            await bot.edit_message_reply_markup(
                chat_id=keyboard_ctx['chat_id'],
                message_id=keyboard_ctx['message_id'],
                reply_markup=keyboard
            )
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise
    else:
        await message.answer('Ваш список общих дел обновлен!', 
                           reply_markup=generate_keyboard(buttons=['В главное меню']))
    
    await replace_tasks_pool(str(message.from_user.id), tasks_pool)
    await state.update_data(tasks_pool=tasks_pool)
    
    if normalized_tasks:
        added_text = ', '.join(normalized_tasks)
        await message.answer(f'Вы добавили {added_text} в список дел. Возвращаю вас в главное меню.')
    else:
        await message.answer('Обновил список дел. Возвращаю вас в главное меню.')
    await start(message=message, state=state)


@router.callback_query(StateFilter(ClientState.greet))
async def process_tasks_pool(call: types.CallbackQuery, state: FSMContext, flag=False):
    """Обработка расписания на сегодня."""
    try:
        await call.answer()
    except Exception:
        pass
    data = call.data
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    today_tasks = user_data.get('today_tasks', {})
    one_time_tasks = user_data.get('one_time_tasks', [])
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    today_tasks_chosen = user_data.get('today_tasks_chosen', [])
    today_tasks_not_time_chosen = user_data.get('today_tasks_not_time_chosen', [])
    
    if data == 'Отправить':
        await state.update_data(
            today_tasks_chosen=today_tasks_chosen, 
            today_tasks_not_time_chosen=today_tasks_not_time_chosen
        )
        collected_data = user_data.get('chosen_collected_data', {})
        if 'Шаги' in collected_data:
            await call.message.answer("Сколько сделал шагов?")
            await state.set_state(ClientState.steps)
        elif 'Сон' in collected_data:
            await state.update_data(my_steps='-')
            await call.message.answer("Введите индекс качества сна")
            await state.set_state(ClientState.total_sleep)
        else:
            await state.update_data(my_steps='-', sleep_quality='-')
            await call.message.answer(
                'Подробно расскажи про свой день.\n'
                'Выгрузи все эмоции которые ты сегодня пережил и события связанные с ними.'
            )
            await state.set_state(ClientState.about_day)

    elif data == 'Сохранить':
        # Загружаем актуальные one_time_tasks из БД
        current_one_time = await get_one_time_tasks(str(call.from_user.id))
        
        for key, value in today_tasks.copy().items():
            # Не сохраняем задачи из scheduler и разовые дела
            if value not in tasks_pool or _is_scheduled_task(value) or value in current_one_time:
                del today_tasks[key]
        daily_snapshot = today_tasks.copy()
        # Фильтруем scheduled задачи и разовые дела из списка без времени
        daily_not_time_snapshot = [
            task for task in today_tasks_not_time 
            if not _is_scheduled_task(task) and task not in current_one_time
        ]
        await state.update_data(daily_tasks=daily_snapshot, daily_tasks_not_time=daily_not_time_snapshot)
        await edit_database(
            daily_tasks=daily_snapshot, 
            daily_tasks_not_time=daily_not_time_snapshot, 
            user_id=call.from_user.id
        )
        await call.message.answer('Расписание на день сохранено!')

    elif data == 'Удалить':
        if len(today_tasks_chosen) == 0 and len(today_tasks_not_time_chosen) == 0:
            await call.answer('Сначала выберите дела для удаления из расписания.', show_alert=True)
            return

        for time_key in today_tasks_chosen:
            if time_key in today_tasks:
                del today_tasks[time_key]
        for task in today_tasks_not_time_chosen:
            if task in today_tasks_not_time:
                today_tasks_not_time.remove(task)
        
        await state.update_data(
            today_tasks=today_tasks, today_tasks_not_time=today_tasks_not_time,
            today_tasks_chosen=[], today_tasks_not_time_chosen=[]
        )

        keyboard = keyboard_builder(
            tasks_dict=today_tasks, tasks_list=today_tasks_not_time,
            chosen=[], grid=1, add_dell=True, add_save=True, last_button="🚀Отправить 🚀"
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)

    elif data == 'Добавить':
        # Загружаем актуальные one_time_tasks из БД
        one_time_tasks = await get_one_time_tasks(str(call.from_user.id))
        await state.update_data(one_time_tasks=one_time_tasks)
        
        tasks_pool_clear = [
            i for i in (tasks_pool + one_time_tasks) 
            if i not in list(today_tasks.values()) + today_tasks_not_time
        ]
        keyboard = keyboard_builder(tasks_list=tasks_pool_clear, add_dell=False)
        await call.message.answer(
            'Ниже список ваших общих дел.\n'
            'Выберите те, которые хотите добавить в ваше расписание на сегодня',
            reply_markup=keyboard
        )
        await state.update_data(tasks_pool_keyboard={
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id
        })
        await state.set_state(ClientState.change_tasks_pool_1)

    else:
        # Выбор/снятие выбора задачи
        if ':' in data:
            if data in today_tasks_chosen:
                today_tasks_chosen.remove(data)
            else:
                today_tasks_chosen.append(data)
            await state.update_data(today_tasks_chosen=today_tasks_chosen)
        else:
            try:
                idx = int(data)
                if 0 <= idx < len(today_tasks_not_time):
                    temp = today_tasks_not_time[idx]
                    if temp in today_tasks_not_time_chosen:
                        today_tasks_not_time_chosen.remove(temp)
                    else:
                        today_tasks_not_time_chosen.append(temp)
                    await state.update_data(today_tasks_not_time_chosen=today_tasks_not_time_chosen)
                else:
                    await call.answer("Список задач обновился, выберите заново.", show_alert=True)
                    keyboard = keyboard_builder(
                        tasks_dict=today_tasks, tasks_list=today_tasks_not_time,
                        chosen=today_tasks_chosen + today_tasks_not_time_chosen,
                        grid=1, add_dell=True, last_button="🚀Отправить 🚀", add_save=True
                    )
                    await call.message.edit_reply_markup(reply_markup=keyboard)
                    return
            except (ValueError, IndexError):
                await call.answer("Некорректный выбор.", show_alert=True)
                return
        
        keyboard = keyboard_builder(
            tasks_dict=today_tasks, tasks_list=today_tasks_not_time,
            chosen=today_tasks_chosen + today_tasks_not_time_chosen,
            grid=1, add_dell=True, last_button="🚀Отправить 🚀", add_save=True
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(StateFilter(ClientState.change_tasks_pool_1))
async def proceed_tasks_pool_1(call, state: FSMContext) -> None:
    """Добавление задачи в расписание на сегодня."""
    await call.answer()
    user_data = await state.get_data()
    try:
        data = int(call.data)
        tasks_pool = user_data.get('tasks_pool', [])
        today_tasks = user_data.get('today_tasks', {})
        one_time_tasks = user_data.get('one_time_tasks', [])
        today_tasks_not_time = user_data.get('today_tasks_not_time', [])
        tasks_pool_clear = [
            i for i in (tasks_pool + one_time_tasks) 
            if i not in list(today_tasks.values()) + today_tasks_not_time
        ]
        
        if 0 <= data < len(tasks_pool_clear):
            await call.message.answer(
                f'Вы выбрали: {tasks_pool_clear[data]}\n'
                f'Введите время в формате ЧЧ:ММ\n'
                f'"-" если дело без времени'
            )
            await state.update_data(temp=tasks_pool_clear[data])
            await state.set_state(ClientState.new_today_tasks)
        else:
            await call.message.answer("Выбранная задача недоступна (список изменился).")
    except (ValueError, IndexError):
        await call.message.answer("Ошибка выбора задачи.")


@router.message(StateFilter(ClientState.new_today_tasks))
async def new_today_tasks(message: Message, state: FSMContext) -> None:
    """Добавление нового дела на сегодня с временем."""
    if not message.text:
        await message.answer('Введите время в формате ЧЧ:ММ или "-" для дела без времени.')
        return
    data = message.text
    user_data = await state.get_data()
    
    if data.replace(' ', '') == '-':
        today_tasks_not_time = user_data.get('today_tasks_not_time', [])
        temp = user_data.get('temp', None)
        if temp:
            today_tasks_not_time.append(temp)
            await state.update_data(today_tasks_not_time=today_tasks_not_time)
            await message.answer('Отлично! Дело добавлено в ваше расписание')
            await tasks_pool_function(message=message, state=state)
        else:
            await message.answer('Ошибка: не выбрана задача. Попробуйте выбрать снова.')
        return
    
    try:
        split_data = data.split(':')
        if len(split_data) == 2:
            hours = int(split_data[0])
            minutes = int(split_data[1])
            if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                await message.answer('Часы должны быть 0-23, минуты 0-59.')
                return
        else:
            raise TypeError

        today_tasks = user_data.get('today_tasks', {})
        task = user_data.get('temp', None)
        if data in today_tasks:
            await message.answer(f'У вас уже есть задача на {data}')
            return
        if task:
            today_tasks[data] = task
            await state.update_data(today_tasks=today_tasks)
            await message.answer('Отлично! Дело добавлено в ваше расписание')
            await tasks_pool_function(message=message, state=state)
        else:
            await message.answer('Ошибка: потеряна задача для добавления. Попробуйте снова.')
    except (TypeError, ValueError):
        await message.answer('Введите правильное время в формате часы:минуты')


@router.message(lambda message: message.text and message.text.lower() == 'разовые дела', StateFilter(ClientState.settings))
async def change_one_time_tasks(message: Message, state: FSMContext) -> None:
    """Управление разовыми делами."""
    user_data = await state.get_data()
    user_id = str(message.from_user.id)
    one_time_tasks = await get_one_time_tasks(user_id)
    await state.update_data(one_time_tasks=one_time_tasks)
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    keyboard = keyboard_builder(tasks_list=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
    await message.answer('Ваши разовые дела', reply_markup=keyboard)
    await state.set_state(ClientState.one_time_tasks_2)


@router.callback_query(StateFilter(ClientState.one_time_tasks_2))
async def change_one_time_tasks_2(call, state) -> None:
    """Обработка разовых дел."""
    await call.answer()
    data = call.data
    user_data = await state.get_data()
    one_time_tasks = user_data.get('one_time_tasks', [])
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    user_id = str(call.from_user.id)

    if data == 'Добавить':
        await call.message.answer(
            'Введите новый список разовых дел через запятую',
            reply_markup=generate_keyboard(['В Главное Меню'])
        )
        await state.update_data(one_time_keyboard={
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id
        })
        await state.set_state(ClientState.one_time_tasks_3)

    elif data == 'Удалить':
        updated_tasks = [task for task in one_time_tasks if task not in one_time_chosen_tasks]
        for task in one_time_chosen_tasks:
            await call.message.answer(f'Вы удалили "{task}"')
        await replace_one_time_tasks(user_id, updated_tasks)
        await state.update_data(one_time_chosen_tasks=[], one_time_tasks=updated_tasks)
        keyboard = keyboard_builder(tasks_list=updated_tasks, chosen=[], grid=1, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)

    else:
        try:
            idx = int(data)
            if idx < 0 or idx >= len(one_time_tasks):
                await call.answer("Задача не найдена.", show_alert=True)
                return
            task_to_toggle = one_time_tasks[idx]
        except (ValueError, TypeError):
            await call.answer("Некорректный выбор.", show_alert=True)
            return
        if task_to_toggle in one_time_chosen_tasks:
            one_time_chosen_tasks.remove(task_to_toggle)
        else:
            one_time_chosen_tasks.append(task_to_toggle)
        await state.update_data(one_time_chosen_tasks=one_time_chosen_tasks)
        keyboard = keyboard_builder(tasks_list=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)


@router.message(StateFilter(ClientState.one_time_tasks_3))
async def change_one_time_tasks_3(message: Message, state: FSMContext) -> None:
    """Добавление новых разовых дел."""
    if not message.text:
        await message.answer('Введите список разовых дел через запятую.')
        return
    
    # Проверка на кнопку "В Главное Меню"
    if message.text.lower() == 'в главное меню':
        await start(message=message, state=state)
        return
    
    user_tasks = normalized(message.text).split(', ')
    user_data = await state.get_data()
    user_id = str(message.from_user.id)
    one_time_tasks = user_data.get('one_time_tasks', [])
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    
    for i in user_tasks:
        num = len(i) - 64
        if num > 0:
            await message.answer(
                f'"{i}" Должно быть короче на {num} символ\n'
                'Попробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2'
            )
            return
        else:
            one_time_tasks.append(i)
    
    one_time_tasks = list(set(one_time_tasks))
    keyboard = keyboard_builder(tasks_list=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
    keyboard_ctx = user_data.get('one_time_keyboard')
    if keyboard_ctx:
        await bot.edit_message_reply_markup(
            chat_id=keyboard_ctx['chat_id'],
            message_id=keyboard_ctx['message_id'],
            reply_markup=keyboard
        )
    await replace_one_time_tasks(user_id, one_time_tasks)
    await state.update_data(one_time_tasks=one_time_tasks, one_time_chosen_tasks=[])
    await message.answer('Ваш список разовых дел обновлен')
    await state.set_state(ClientState.one_time_tasks_2)
