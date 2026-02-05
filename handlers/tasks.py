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
    replace_one_time_tasks, edit_database, batch_update_tasks
)

router = Router(name="tasks")


# Все состояния настроек для навигации между пунктами
SETTINGS_STATES = (
    ClientState.settings, ClientState.collected_data, ClientState.notification_proceed, 
    ClientState.notification_set_date, ClientState.edit_tasks_pool, ClientState.one_time_tasks_2,
    ClientState.one_time_tasks_3, ClientState.date_jobs, ClientState.date_jobs_1, ClientState.date_jobs_2,
    ClientState.date_jobs_week, ClientState.date_jobs_month, ClientState.date_jobs_year, ClientState.date_jobs_once,
    ClientState.add_tasks_pool
)


@router.message(lambda message: message.text and message.text.lower() == 'список дел', StateFilter(*SETTINGS_STATES))
async def edit_tasks_pool_handler(message: Message, state: FSMContext):
    """Редактирование общего списка дел."""
    user_data = await state.get_data()
    user_id = str(message.from_user.id)
    tasks_pool = await get_tasks_pool(user_id)
    edit_tasks_pool_chosen = user_data.get('edit_tasks_pool_chosen', [])
    await state.update_data(tasks_pool=tasks_pool, tasks_to_delete=[])

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
        deleted_names = []
        
        for name in edit_tasks_pool_chosen:
            if name in tasks_pool:
                tasks_pool.remove(name)
                deleted_names.append(name)
            if name in today_tasks.values():
                for key in list(today_tasks_copy.keys()):
                    if today_tasks_copy.get(key) == name:
                        daily_chosen_tasks = [k for k in daily_chosen_tasks if k != key]
                        today_tasks_chosen = [k for k in today_tasks_chosen if k != key]
                        del today_tasks_copy[key]
            if name in today_tasks_not_time:
                today_tasks_not_time.remove(name)
            today_tasks_not_time_chosen = [t for t in today_tasks_not_time_chosen if t != name]
            daily_tasks_not_time_chosen = [t for t in daily_tasks_not_time_chosen if t != name]
            if name in daily_tasks.values():
                for key in list(daily_tasks_copy.keys()):
                    if daily_tasks_copy.get(key) == name:
                        daily_chosen_tasks = [k for k in daily_chosen_tasks if k != key]
                        del daily_tasks_copy[key]

        if deleted_names:
            deleted_list = "\n".join(deleted_names)
            await call.message.answer(f'Вы удалили:\n{deleted_list}')

        # Фильтруем daily_tasks - только задачи которые остались в tasks_pool
        tasks_pool_set = set(tasks_pool)
        daily_tasks_copy = {k: v for k, v in daily_tasks_copy.items() if v in tasks_pool_set}
        daily_tasks_not_time = [task for task in user_data.get('daily_tasks_not_time', []) if task in tasks_pool_set]
        
        # Батч-обновление БД
        await batch_update_tasks(user_id, tasks_pool=tasks_pool, daily_tasks=daily_tasks_copy,
                                 daily_tasks_not_time=daily_tasks_not_time)
        
        await state.update_data(
            tasks_pool=tasks_pool, daily_tasks=daily_tasks_copy, 
            daily_chosen_tasks=daily_chosen_tasks, today_tasks=today_tasks_copy, 
            edit_tasks_pool_chosen=[], today_tasks_not_time=today_tasks_not_time,
            daily_tasks_not_time_chosen=daily_tasks_not_time_chosen, 
            daily_tasks_not_time=daily_tasks_not_time,
            today_tasks_chosen=today_tasks_chosen,
            today_tasks_not_time_chosen=today_tasks_not_time_chosen
        )
        
        keyboard = keyboard_builder(tasks_list=tasks_pool, add_dell=True, chosen=[])
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


# Кнопки настроек для проверки
SETTINGS_BUTTONS = [
    'в главное меню', 'напоминания', 'в определенную дату', 
    'опрашиваемые данные', 'список дел', 'разовые дела', 'настройки'
]


@router.message(StateFilter(ClientState.add_tasks_pool))
async def add_tasks_pool(message, state: FSMContext):
    """Добавление новых дел в список."""
    if not message.text:
        await message.answer('Введите список дел через запятую.')
        return
    
    # Проверка на кнопки настроек
    if message.text.lower() in SETTINGS_BUTTONS:
        if message.text.lower() == 'в главное меню':
            await start(message=message, state=state)
        elif message.text.lower() == 'настройки':
            from handlers.settings import settings
            await settings(message=message, state=state)
        elif message.text.lower() == 'напоминания':
            from handlers.settings import notifications
            await notifications(message=message, state=state)
        elif message.text.lower() == 'опрашиваемые данные':
            from handlers.settings import collected_data
            await collected_data(message=message, state=state)
        elif message.text.lower() == 'список дел':
            await edit_tasks_pool_handler(message=message, state=state)
        elif message.text.lower() == 'разовые дела':
            await change_one_time_tasks(message=message, state=state)
        elif message.text.lower() == 'в определенную дату':
            from handlers.scheduler_handlers import date_jobs_keyboard
            await date_jobs_keyboard(message=message, state=state)
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
                'В чем ты лучше себя вчерашнего? Не обязательно быть супер-продуктивным, '
                'достаточно хотя бы мизерного процента и ты уже не зря прожил этот день. '
                'Также можешь выгрузить свои эмоции за этот день, это помогает расслабиться '
                'и не крутить в голове эти мысли'
            )
            await state.set_state(ClientState.about_day)

    elif data == 'Сохранить':
        # Загружаем актуальные one_time_tasks из БД
        current_one_time = await get_one_time_tasks(str(call.from_user.id))
        tasks_pool_set = set(tasks_pool)
        
        # Фильтруем: только задачи из tasks_pool, не scheduled, не разовые
        daily_snapshot = {
            k: v for k, v in today_tasks.items()
            if v in tasks_pool_set and not _is_scheduled_task(v) and v not in current_one_time
        }
        daily_not_time_snapshot = [
            task for task in today_tasks_not_time 
            if task in tasks_pool_set and not _is_scheduled_task(task) and task not in current_one_time
        ]
        await state.update_data(daily_tasks=daily_snapshot, daily_tasks_not_time=daily_not_time_snapshot)
        # Сохраняем в обе таблицы для синхронизации
        await edit_database(
            daily_tasks=daily_snapshot, 
            daily_tasks_not_time=daily_not_time_snapshot, 
            user_id=call.from_user.id
        )
        await batch_update_tasks(
            str(call.from_user.id),
            daily_tasks=daily_snapshot,
            daily_tasks_not_time=daily_not_time_snapshot
        )
        await call.message.answer('Расписание на день сохранено!')

    elif data == 'Удалить':
        if len(today_tasks_chosen) == 0 and len(today_tasks_not_time_chosen) == 0:
            await call.answer('Сначала выберите дела для удаления из расписания.', show_alert=True)
            return

        # Отслеживаем удалённые дела чтобы не восстанавливать их
        today_tasks_deleted = set(user_data.get('today_tasks_deleted', []))
        
        for time_key in today_tasks_chosen:
            if time_key in today_tasks:
                task_name = today_tasks[time_key]
                today_tasks_deleted.add(task_name)
                del today_tasks[time_key]
        for task in today_tasks_not_time_chosen:
            if task in today_tasks_not_time:
                today_tasks_deleted.add(task)
                today_tasks_not_time.remove(task)
        
        await state.update_data(
            today_tasks=today_tasks, today_tasks_not_time=today_tasks_not_time,
            today_tasks_chosen=[], today_tasks_not_time_chosen=[],
            today_tasks_deleted=list(today_tasks_deleted)
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
        state_update_key = None
        if ':' in data:
            if data in today_tasks_chosen:
                today_tasks_chosen.remove(data)
            else:
                today_tasks_chosen.append(data)
            state_update_key = 'today_tasks_chosen'
            state_update_val = today_tasks_chosen
        else:
            try:
                idx = int(data)
                if 0 <= idx < len(today_tasks_not_time):
                    temp = today_tasks_not_time[idx]
                    if temp in today_tasks_not_time_chosen:
                        today_tasks_not_time_chosen.remove(temp)
                    else:
                        today_tasks_not_time_chosen.append(temp)
                    state_update_key = 'today_tasks_not_time_chosen'
                    state_update_val = today_tasks_not_time_chosen
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
        
        if state_update_key:
            await state.update_data(**{state_update_key: state_update_val})
        
        keyboard = keyboard_builder(
            tasks_dict=today_tasks, tasks_list=today_tasks_not_time,
            chosen=today_tasks_chosen + today_tasks_not_time_chosen,
            grid=1, add_dell=True, last_button="🚀Отправить 🚀", add_save=True
        )
        try:
            await call.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise


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
            # Убираем из удалённых, если было удалено ранее
            today_tasks_deleted = set(user_data.get('today_tasks_deleted', []))
            today_tasks_deleted.discard(temp)
            await state.update_data(
                today_tasks_not_time=today_tasks_not_time,
                today_tasks_deleted=list(today_tasks_deleted)
            )
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
            # Убираем из удалённых, если было удалено ранее
            today_tasks_deleted = set(user_data.get('today_tasks_deleted', []))
            today_tasks_deleted.discard(task)
            await state.update_data(
                today_tasks=today_tasks,
                today_tasks_deleted=list(today_tasks_deleted)
            )
            await message.answer('Отлично! Дело добавлено в ваше расписание')
            await tasks_pool_function(message=message, state=state)
        else:
            await message.answer('Ошибка: потеряна задача для добавления. Попробуйте снова.')
    except (TypeError, ValueError):
        await message.answer('Введите правильное время в формате часы:минуты')


@router.message(lambda message: message.text and message.text.lower() == 'разовые дела', StateFilter(*SETTINGS_STATES))
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
        if one_time_chosen_tasks:
            deleted_list = "\n".join(one_time_chosen_tasks)
            await call.message.answer(f'Вы удалили:\n{deleted_list}')
            
            # Удаляем разовые дела из today_tasks и today_tasks_not_time
            today_tasks = user_data.get('today_tasks', {})
            today_tasks_not_time = user_data.get('today_tasks_not_time', [])
            today_tasks_chosen = user_data.get('today_tasks_chosen', [])
            today_tasks_not_time_chosen = user_data.get('today_tasks_not_time_chosen', [])
            
            # Удаляем из today_tasks (дела с временем)
            keys_to_delete = [k for k, v in today_tasks.items() if v in one_time_chosen_tasks]
            for key in keys_to_delete:
                del today_tasks[key]
                if key in today_tasks_chosen:
                    today_tasks_chosen.remove(key)
            
            # Удаляем из today_tasks_not_time (дела без времени)
            today_tasks_not_time = [t for t in today_tasks_not_time if t not in one_time_chosen_tasks]
            today_tasks_not_time_chosen = [t for t in today_tasks_not_time_chosen if t not in one_time_chosen_tasks]
            
            await state.update_data(
                today_tasks=today_tasks,
                today_tasks_not_time=today_tasks_not_time,
                today_tasks_chosen=today_tasks_chosen,
                today_tasks_not_time_chosen=today_tasks_not_time_chosen
            )
        
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
    
    # Проверка на кнопки настроек
    if message.text.lower() in SETTINGS_BUTTONS:
        if message.text.lower() == 'в главное меню':
            await start(message=message, state=state)
        elif message.text.lower() == 'настройки':
            from handlers.settings import settings
            await settings(message=message, state=state)
        elif message.text.lower() == 'напоминания':
            from handlers.settings import notifications
            await notifications(message=message, state=state)
        elif message.text.lower() == 'опрашиваемые данные':
            from handlers.settings import collected_data
            await collected_data(message=message, state=state)
        elif message.text.lower() == 'список дел':
            await edit_tasks_pool_handler(message=message, state=state)
        elif message.text.lower() == 'разовые дела':
            await change_one_time_tasks(message=message, state=state)
        elif message.text.lower() == 'в определенную дату':
            from handlers.scheduler_handlers import date_jobs_keyboard
            await date_jobs_keyboard(message=message, state=state)
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
