from aiogram.types.error_event import ErrorEvent
from aiogram.types import BufferedInputFile
import asyncio
import datetime
import os
import logging # <--- ДОБАВИТЬ
import traceback
from keys import ADMIN_ID
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlite import database_start, edit_database
from functions import generate_keyboard, diary_out, add_day_to_excel, normalized,\
    tasks_pool_function, keyboard_builder, generate_unique_id_from_args,\
    start, dp, ClientState, bot, negative_responses, remove_markup, scheduler, translate,\
    day_to_prefix, scheduler_list, TARGET_TZ




@dp.message(lambda message: message.text and message.text.lower() == 'вывести дневник')
async def diary_output(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if user_data is not None and isinstance(user_data, dict) and len(user_data):
        await diary_out(message)
        await state.set_state(ClientState.greet)
    else:
        await start(message=message, state=state)


@dp.message(lambda message: message.text and message.text.lower() == 'скачать дневник')
async def download_diary(message: Message, state: FSMContext):
    user_data = await state.get_data()
    if not user_data:
        await start(message=message, state=state)
        return
    file_path = f'{message.from_user.id}_Diary.xlsx'
    try:
        if os.path.exists(file_path):
            message = await message.answer_document(
                document=FSInputFile(file_path),
                disable_content_type_detection=True
            )
            return message
        else:
            await message.answer('Дневник еще не создан. Заполните его сначала!')
    except:
        await message.answer('Ошибка при отправке файла. Попробуйте позже.')
        # Optionally log the error for debugging


@dp.message(ClientState.new_today_tasks)
async def new_today_tasks(message: Message, state: FSMContext = None) -> None:
    data = message.text
    try:
        split_data = data.split(':')
        if len(split_data) == 2:
            hours = int(split_data[0])
            minutes = int(split_data[1])
        else:
            hours = int(split_data[0])
            data = split_data[0] + ':00'
        user_data = await state.get_data()
        today_tasks = user_data['today_tasks']
        task = user_data['temp']
        if data in today_tasks:
            await message.answer(f'У вас уже есть задача на {data}')
            return
        today_tasks[data] = task
        await state.update_data(today_tasks=today_tasks)
        await message.answer('Отлично! Дело добавлено в ваше расписание')
        await tasks_pool_function(message=message, state=state)
    except TypeError:
        await message.answer('Введите правильное время в формате часы:минуты')
        return


@dp.message(lambda message: message.text and message.text.lower() == 'настройки')
async def settings(message: Message, state: FSMContext = None) -> None:
    user_data = await state.get_data()
    if user_data is not None and isinstance(user_data, dict) and len(user_data):
        user_data = await state.get_data()
        inp = ['Напоминания', 'Дела в определенную дату', 'Опрашиваемые данные']

        # --- ИЗМЕНЕНИЕ НАЧАЛО ---
        # Добавляем новую кнопку для редактирования
        inp.append('Редактировать список дел')
        # --- ИЗМЕНЕНИЕ КОНЕЦ ---

        if not user_data['one_time_jobs']:
            inp.append('Добавить Разовые Дела')
        if 'personal_records' in user_data:
            inp.append('Мои рекорды')
        if os.path.exists(f'{message.from_user.id}_Diary.xlsx'):
            inp.append('Скачать Дневник')

        keyboard = generate_keyboard(buttons=inp, last_button='В Главное Меню')
        await message.answer(text='Ваши Настройки', reply_markup=keyboard)
        await state.set_state(ClientState.settings)
    else:
        await start(message=message, state=state)


@dp.message(lambda message: message.text and message.text.lower() == 'в главное меню')
async def go_to_main_menu(message: Message, state: FSMContext) -> None:
    await start(message=message, state=state)


@dp.message(lambda message: message.text and message.text.lower() == 'редактировать список дел', ClientState.settings)
async def edit_tasks_pool_handler(message: Message, state: FSMContext):
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])

    if not tasks_pool:
        await message.answer("Ваш список дел пуст. Сначала добавьте дела.")
        return

    # Инициализируем пустой список для выбранных на удаление задач
    await state.update_data(tasks_to_delete=[])

    # Используем немного измененный keyboard_builder или создадим новый
    builder = InlineKeyboardBuilder()
    for index, task in enumerate(tasks_pool):
        # В callback_data передаем индекс задачи в списке
        builder.button(text=f"❌ {task}", callback_data=f"del_task_{index}")

    builder.button(text="✔️ Готово", callback_data="confirm_deletion")
    builder.button(text="Отмена", callback_data="cancel_deletion")
    builder.adjust(1)  # Все кнопки в один столбец для наглядности

    await message.answer(
        "Выберите дела, которые хотите удалить навсегда из общего списка. Когда закончите, нажмите 'Готово'.",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ClientState.edit_tasks_pool)


@dp.callback_query(ClientState.edit_tasks_pool)
async def process_edit_tasks_pool_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    tasks_to_delete = user_data.get('tasks_to_delete', [])

    if call.data == "confirm_deletion":
        if not tasks_to_delete:
            await call.answer("Вы ничего не выбрали для удаления.", show_alert=True)
            return

        # Создаем новый список, исключая задачи с выбранными индексами
        # Сортируем индексы в обратном порядке, чтобы избежать проблем при удалении
        tasks_to_delete.sort(reverse=True)
        new_tasks_pool = tasks_pool.copy()

        deleted_tasks_names = []
        for index in tasks_to_delete:
            deleted_tasks_names.append(new_tasks_pool.pop(index))

        # Обновляем данные в состоянии и в БД
        await state.update_data(tasks_pool=new_tasks_pool, tasks_to_delete=[])
        await edit_database(tasks_pool=new_tasks_pool)

        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Следующие дела были удалены:\n- " + "\n- ".join(reversed(deleted_tasks_names)),
            reply_markup=None  # Убираем клавиатуру
        )
        await call.message.answer("Ваш список дел обновлен. Возврат в главное меню...")
        await tasks_pool_function(call.message, state)

    elif call.data == "cancel_deletion":
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Удаление отменено.",
            reply_markup=None
        )
        await start(message=call.message, state=state)

    elif call.data.startswith("del_task_"):
        index_to_toggle = int(call.data.split('_')[2])

        # Логика выбора/отмены выбора
        if index_to_toggle in tasks_to_delete:
            tasks_to_delete.remove(index_to_toggle)
        else:
            tasks_to_delete.append(index_to_toggle)

        await state.update_data(tasks_to_delete=tasks_to_delete)

        # Обновляем клавиатуру, чтобы показать выбор (✅)
        builder = InlineKeyboardBuilder()
        for index, task in enumerate(tasks_pool):
            if index in tasks_to_delete:
                builder.button(text=f"✅ {task}", callback_data=f"del_task_{index}")
            else:
                builder.button(text=f"❌ {task}", callback_data=f"del_task_{index}")

        builder.button(text="✔️ Готово", callback_data="confirm_deletion")
        builder.button(text="Отмена", callback_data="cancel_deletion")
        builder.adjust(1)

        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=builder.as_markup()
        )


@dp.callback_query(ClientState.greet)
async def process_tasks_pool(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = call.data
    user_states_data = await state.get_data()

    tasks_pool_full = user_states_data.get('tasks_pool', [])
    today_tasks = user_states_data.get('today_tasks', {})
    daily_chosen_tasks = user_states_data.get('daily_chosen_tasks', [])

    # --- RECALCULATE UNSCHEDULED TASKS FOR CONTEXT ---
    scheduled_task_names = set(today_tasks.values())
    unscheduled_tasks = [task for task in tasks_pool_full if task not in scheduled_task_names]

    if data == 'Отправить':
        await state.update_data(daily_chosen_tasks=daily_chosen_tasks)
        collected_data = user_states_data.get('chosen_collected_data', {})
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
                'Подробно расскажи про свой день.\nВыгрузи все эмоции которые ты сегодня пережил и события связанные с ними. Это поможет тебе лучше заснуть')
            await state.set_state(ClientState.about_day)

    elif data == 'Сохранить':
        # Save the current temporary schedule (today_tasks) as the permanent one (daily_tasks)
        await state.update_data(daily_tasks=today_tasks)
        await edit_database(daily_tasks=today_tasks)
        await call.message.answer('Расписание на день сохранено!', show_alert=True)

    elif data == 'Удалить':
        if not daily_chosen_tasks:
            await call.answer('Сначала выберите дела для удаления из расписания.', show_alert=True)
            return

        # Remove chosen tasks from the daily schedule
        for time_key in daily_chosen_tasks:
            if time_key in today_tasks:
                del today_tasks[time_key]

        # Reset choices and update state
        await state.update_data(today_tasks=today_tasks, daily_chosen_tasks=[])

        # Re-render the keyboard with the updated lists
        updated_unscheduled = [task for task in tasks_pool_full if task not in today_tasks.values()]
        keyboard = keyboard_builder(
            tasks_pool=updated_unscheduled,
            today_tasks=today_tasks,
            chosen=[],  # Choices are now cleared
            grid=1,
            add_dell=True
        )
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard
        )

    elif data == 'Добавить':
        await call.message.answer(
            'Введите **общие** дела, которые вы хотели бы добавить в свой пул задач (не в расписание). Например:\nПодтягивания, Практика гитары')
        await state.update_data(call=call)
        await state.set_state(ClientState.change_tasks_pool_1)

    else:  # This block handles both checking a scheduled task and adding a new one
        try:
            # --- Case 1: An unscheduled task was clicked (data is its index in the unscheduled list) ---
            task_index = int(data)
            if 0 <= task_index < len(unscheduled_tasks):
                chosen_task_to_add = unscheduled_tasks[task_index]
                await call.message.answer(f'Вы выбрали: {chosen_task_to_add}\n'
                                          f'Введите время в формате ЧЧ:ММ, на которое вы хотите назначить это дело.')
                await state.update_data(temp=chosen_task_to_add)
                await state.set_state(ClientState.new_today_tasks)
            else:
                await call.answer("Ошибка: неверный индекс задачи.", show_alert=True)

        except ValueError:
            # --- Case 2: A scheduled task was clicked (data is the time string) ---
            # This is for marking a task as done/not done
            if data in daily_chosen_tasks:
                daily_chosen_tasks.remove(data)
            else:
                daily_chosen_tasks.append(data)

            await state.update_data(daily_chosen_tasks=daily_chosen_tasks)

            # Rebuild keyboard to show the checkmark
            keyboard = keyboard_builder(
                tasks_pool=unscheduled_tasks,
                today_tasks=today_tasks,
                chosen=daily_chosen_tasks,
                grid=1,
                add_dell=True
            )
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard
            )



async def rebuild_keyboard_with_chosen(data, call, chosen_tasks, state, tasks, today_tasks, grid=1):
    if data in chosen_tasks:
        chosen_tasks.remove(data)
    else:
        chosen_tasks.append(data)
    # Обновляем именно daily_chosen_tasks в состоянии
    await state.update_data(daily_chosen_tasks=chosen_tasks) # <--- Убедимся, что обновляем правильный ключ
    keyboard = keyboard_builder(tasks_pool=tasks, chosen=chosen_tasks, grid=grid, today_tasks=today_tasks)
    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard)


# @dp.callback_query(ClientState.one_time_jobs_proceed)
# async def process_one_time(call: types.CallbackQuery, state: FSMContext) -> None:
#     await call.answer()
#     data = call.data
#     user_states_data = await state.get_data()
#     # balance = user_states_data['balance']
#     one_time_chosen_tasks = user_states_data.get('one_time_chosen_tasks', [])
#     one_time_jobs = user_states_data.get('one_time_jobs', [])
#
#     if data == 'Отправить':
#         if one_time_chosen_tasks:
#             # Создаем список ключей (названий дел) для безопасного доступа по индексу
#             # Начисляем золото и собираем названия выполненных дел
#             # for job_index_str in one_time_chosen_tasks:
#             #     job_index = int(job_index_str)
#             #     if 0 <= job_index < len(job_keys):
#             #         job_name = job_keys[job_index]
#             #         completed_jobs_names.append(job_name)
#             #         balance['gold'] += int(one_time_jobs[job_name])
#
#             # Теперь удаляем выполненные дела из основного словаря
#             for job_name in one_time_chosen_tasks:
#                 one_time_jobs.pop(int(job_name))
#
#             # Сохраняем все изменения
#             await edit_database(one_time_jobs=one_time_jobs)
#             await state.update_data(
#                 one_time_jobs=one_time_jobs,
#                 excel_chosen_tasks=user_states_data.get('excel_chosen_tasks', []) + one_time_chosen_tasks,
#                 one_time_chosen_tasks=[]  # Очищаем список выбранных дел
#             )
#
#         # Переход к следующим шагам опроса
#         collected_data = user_states_data.get('chosen_collected_data', [])
#         if 'Шаги' in collected_data:
#             await call.message.answer("Сколько сделал шагов?")
#             await state.set_state(ClientState.steps)
#         elif 'Сон' in collected_data:
#             await state.update_data(my_steps='-')
#             await call.message.answer("Введите индекс качества сна")
#             await state.set_state(ClientState.total_sleep)
#         else:
#             await state.update_data(my_steps='-', sleep_quality='-')
#             await call.message.answer(
#                 'Подробно расскажи про свой день.\nВыгрузи все эмоции которые ты сегодня пережил и события связанные с ними. Это поможет тебе лучше заснуть')
#             await state.set_state(ClientState.about_day)
#     elif data == 'Удалить':
#         for index, job in enumerate(one_time_jobs.copy()):
#             if str(index) in one_time_chosen_tasks:
#                 del one_time_jobs[job]
#         if len(one_time_jobs):
#             keyboard = keyboard_builder(inp=one_time_jobs, grid=1, chosen=one_time_chosen_tasks)
#             await bot.edit_message_reply_markup(
#                 chat_id=call.message.chat.id,
#                 message_id=call.message.message_id,
#                 reply_markup=keyboard)
#             await state.update_data(one_time_jobs=one_time_jobs)
#             await edit_database(one_time_jobs=one_time_jobs)
#
#         else:
#             new_ot_builder = InlineKeyboardBuilder()
#             new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
#             del user_states_data['one_time_jobs']
#             await state.set_data(user_states_data)
#             await bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
#                                         reply_markup=new_ot_builder.as_markup(), text='Добавьте список дел')
#         await state.update_data(one_time_chosen_tasks=[])
#         await edit_database(one_time_jobs=one_time_jobs)
#
#     elif data == 'Добавить':
#         await call.message.answer('Введите разовые дела и их стоимость, которые вы хотели бы добавить через запятую. Например:\n\nСходить в баню : 60')
#         await state.update_data(one_time_call=call)
#         await state.set_state(ClientState.one_time_jobs_2)
#
#     else:
#         one_time_jobs = user_states_data['one_time_jobs']
#         one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']
#
#         if data in one_time_chosen_tasks:
#             one_time_chosen_tasks.remove(data)
#         else:
#             one_time_chosen_tasks.append(data)
#
#         keyboard = keyboard_builder(inp=one_time_jobs, chosen=one_time_chosen_tasks, grid=1)
#         await bot.edit_message_reply_markup(
#             chat_id=call.message.chat.id,
#             message_id=call.message.message_id,
#             reply_markup=keyboard)
#         await state.update_data(one_time_chosen_tasks=one_time_chosen_tasks)


async def get_valid_number(message: Message, state: FSMContext, field: str, prompt: str, next_state, min_val=None, max_val=None):
    try:
        value = float(message.text.replace(',', '.'))
        if (min_val is not None and value < min_val) or (max_val is not None and value > max_val):
            raise ValueError(f"Число должно быть между {min_val} и {max_val}")
        await state.update_data(**{field: value})
        await message.answer(prompt)
        await state.set_state(next_state)
    except ValueError:
        await message.answer(f'"{message.text}" должно быть числом. Попробуйте снова (например, 12.5).')
        # Остаёмся в текущем состоянии, ожидая новый ввод

@dp.message(ClientState.steps)
async def process_steps(message: Message, state: FSMContext):
    if message.text in negative_responses:
        await state.update_data(my_steps=0.0)
        await message.answer('Введите индекс качества сна')
        await state.set_state(ClientState.total_sleep)
    else:
        await get_valid_number(message, state, 'my_steps', 'Введите индекс качества сна', ClientState.total_sleep, min_val=0)


@dp.message(ClientState.total_sleep)
async def process_total_sleep(message: Message, state: FSMContext) -> None:
    if message.text not in negative_responses:
        try:
            user_message = float(message.text.replace(',', '.'))
            await state.update_data(sleep_quality=user_message)
            await message.answer('Подробно расскажи про свой день.\nВыгрузи все эмоции которые ты сегодня пережил и события связанные с ними. Это поможет тебе лучше заснуть')
            await state.set_state(ClientState.about_day)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(sleep_quality=0)
        await message.answer(
            'Подробно расскажи про свой день.\nВыгрузи все эмоции которые ты сегодня пережил и события связанные с ними. Это поможет тебе лучше заснуть')
        await state.set_state(ClientState.about_day)


@dp.message(ClientState.about_day)
async def process_about_day(message: Message, state: FSMContext) -> None:
    user_message = message.text
    if len(user_message) < 120:
        await message.answer('Расскажите подробнее про свой день, не ленитесь.')
    else:
        await state.update_data(user_message=message.text)
        await message.answer('Насколько из 10 оцениваете день?')
        await state.set_state(ClientState.personal_rate)


@dp.message(ClientState.personal_rate)
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    try:
        personal_rate = int(message.text)
        if not (0 <= personal_rate <= 10):
            raise ValueError
    except ValueError:
        await message.answer(f'"{message.text}" должен быть числом от 0 до 10')
        return

    user_states_data = await state.get_data()

    # Get activities from the *checked* items in today_tasks, not the whole tasks_pool
    today_tasks = user_states_data.get('today_tasks', {})
    daily_chosen_tasks_keys = user_states_data.get('daily_chosen_tasks', [])
    activities = [today_tasks[key] for key in daily_chosen_tasks_keys if key in today_tasks]

    # Note: I changed how `activities` is calculated to be more robust

    data_for_excel = {
        'tasks_pool': user_states_data['tasks_pool'],
        'date': datetime.datetime.now() - datetime.timedelta(days=1),  # Assuming this is for yesterday
        'activities': activities,
        'user_message': user_states_data['user_message'],
        'sleep_quality': user_states_data['sleep_quality'],
        'my_steps': user_states_data['my_steps'],
    }
    if 'personal_records' in user_states_data:
        data_for_excel['personal_records'] = user_states_data['personal_records']

    answer = await add_day_to_excel(message=message, personal_rate=personal_rate, **data_for_excel)
    if answer:
        personal_records = answer
        await edit_database(personal_records=personal_records)

    if 'previous_diary' in user_states_data and user_states_data['previous_diary']:
        try:
            await bot.delete_message(message.chat.id, user_states_data['previous_diary'])
        except:
            pass  # Ignore if message not found

    send_message = await download_diary(message, state)
    if send_message:
        await edit_database(previous_diary=send_message.message_id)

    # --- MODIFICATION START ---
    # Clean up session-specific data
    await state.update_data(daily_chosen_tasks=[], one_time_chosen_tasks=[], session_accrued_tasks=[])

    # Re-initialize today_tasks from the saved daily_tasks for the new session
    saved_daily_tasks = user_states_data.get('daily_tasks', {})
    await state.update_data(today_tasks=saved_daily_tasks.copy())

    # Instead of going to the main menu, show the user their schedule for the next day
    await message.answer("Отлично, дневник заполнен! Вот ваше стандартное расписание на день.")
    await tasks_pool_function(message, state)
    # --- MODIFICATION END ---


@dp.message(lambda message: message.text and message.text.lower() == 'опрашиваемые данные', ClientState.settings)
async def collected_data(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    chosen_collected_data = user_data['chosen_collected_data']
    keyboard = keyboard_builder(today_tasks=['Шаги', 'Сон'], add_dell=False, chosen=chosen_collected_data, grid=2, price_tag=False)
    await message.answer(reply_markup=keyboard, text='Зеленая галочка означает что настройки будут работать,'
                                                     ' серая - что выключены')
    await state.set_state(ClientState.collected_data)


@dp.callback_query(ClientState.collected_data)
async def collected_data_proceed(call, state):
    data = int(call.data)
    user_data = await state.get_data()
    if 'chosen_collected_data' in user_data:
        chosen_collected_data = user_data['chosen_collected_data']
        if ['Шаги', 'Сон'][data] in chosen_collected_data:
            chosen_collected_data.remove(['Шаги', 'Сон'][data])
        else:
            chosen_collected_data.append(['Шаги', 'Сон'][data])
    else:
        chosen_collected_data = [['Шаги', 'Сон'][data]]
    await state.update_data(chosen_collected_data=chosen_collected_data)
    await state.update_data(daily_chosen_tasks=[])
    await edit_database(chosen_collected_data=chosen_collected_data)
    keyboard = keyboard_builder(today_tasks=['Шаги', 'Сон'], chosen=chosen_collected_data,
                                add_dell=False, grid=2)
    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard)


@dp.message(lambda message: message.text and message.text.lower() == 'мои рекорды', ClientState.settings)
async def my_records(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if user_data:
        data = await state.get_data()
        personal_records = data.get('personal_records', {})
        output = [f'{key} : {value}' for key, value in personal_records.items()]
        await message.answer('Ваши рекорды:\n' + '\n'.join(output))
    else:
        await start(message=message, state=state)

# @dp.message(lambda message: message.text and message.text.lower() == 'потратить золото')
# async def market_init(message, state):
#     user_data = await state.get_data()
#     balance = user_data['balance']
#     gold = balance['gold']
#     store = user_data['market']['store']
#     chosen_store = user_data['chosen_store']
#     keyboard = keyboard_builder(checks=False, inp=store, add_dell=True, chosen=chosen_store, grid=2, last_button="💰Потратить 💰")
#     await message.answer(f'Ваш баланс: {gold}💰', reply_markup=keyboard)
#     keyboard = generate_keyboard(buttons=['Рюкзак'], last_button='В Главное Меню')
#     await message.answer(f'Купленные товары можно посмотреть в "Рюкзаке"', reply_markup=keyboard)
#     await state.set_state(ClientState.market)
#
#
# @dp.message(lambda message: message.text and message.text.lower() == 'рюкзак', ClientState.market)
# async def backpack(message, state):
#     user_data = await state.get_data()
#     purchase_history = user_data['market']['purchase_history']
#     out = []
#     path = f"{message.from_user.id}_Diary.xlsx"
#     if os.path.exists(path):
#         out_keyboard = generate_keyboard(
#         ['Вывести Дневник', 'Настройки', 'Потратить Золото'],
#         first_button='Заполнить Дневник')
#     else: out_keyboard = generate_keyboard(['Заполнить Дневник'], last_button='Настройки')
#
#     for key, value in purchase_history.items():
#         for i in value:
#             price = i['price']
#             time = i['time']
#             out += [f"{time} {key} - {price}💰"]
#     out = '\n'.join(out)
#     if any(not item['used'] for value in purchase_history.values() for item in value):
#         date_builder = InlineKeyboardBuilder()
#         for product in purchase_history:
#             product_data = purchase_history[product]
#             for index, purchase in enumerate(product_data):
#                 if purchase['used'] is False:
#                     # price = product_data[date]['price']
#                     date_builder.button(text=f"{purchase['time']} {product} ✔️", callback_data=f"{product} : {index}")
#         date_builder.adjust(2, 2)
#         d_new_builder = InlineKeyboardBuilder()
#         d_new_builder.button(text="🎂Использовать 🎂", callback_data="Использовать")
#         d_new_builder.adjust(2, 1)
#         date_builder.attach(d_new_builder)
#         keyboard = date_builder.as_markup()
#         # keyboard = keyboard_builder(inp=purchase_history, checks=False, last_button="☀️Использовать☀️", add_dell=False)
#         await message.answer('Ваш рюкзак', reply_markup=keyboard)
#         await message.answer(f'История покупок:\n{out}', reply_markup=out_keyboard)
#         await state.set_state(ClientState.backpack)
#     else:
#
#         await message.answer(f'Рюкзак пуст\n\nИстория покупок:\n{out}', reply_markup=out_keyboard)
#
#
# @dp.callback_query(ClientState.backpack)
# async def proceed_backpack(call: types.CallbackQuery, state: FSMContext):
#     await call.answer()
#     data = call.data
#     user_data = await state.get_data()
#     message = user_data['message']
#     market = user_data['market']
#     backpack_chosen = user_data['backpack_chosen']
#     purchase_history = user_data['market']['purchase_history']
#     if data == 'Использовать':
#         for i in backpack_chosen:
#             data_splited = i.split(' : ')
#             index = int(data_splited[1])
#             product = data_splited[0]
#             purchase_history[product][index]['used'] = True
#         if any(not item['used'] for value in purchase_history.values() for item in value):
#             date_builder = InlineKeyboardBuilder()
#             for product in purchase_history:
#                 product_data = purchase_history[product]
#                 for index, purchase in enumerate(product_data):
#                     if purchase['used'] is False:
#                         # price = product_data[date]['price']
#                         date_builder.button(text=f"{purchase['time']} {product} ✔️",
#                                             callback_data=f"{product} : {index}")
#             date_builder.adjust(2, 2)
#             d_new_builder = InlineKeyboardBuilder()
#             d_new_builder.button(text="🎂Использовать 🎂", callback_data="Использовать")
#             d_new_builder.adjust(2, 1)
#             date_builder.attach(d_new_builder)
#             keyboard = date_builder.as_markup()
#             await bot.edit_message_reply_markup(
#                 chat_id=call.message.chat.id,
#                 message_id=call.message.message_id,
#                 reply_markup=keyboard)
#         else:
#             path = str(message.from_user.id) + '_Diary.xlsx'
#             if os.path.exists(path):
#                 keyboard = generate_keyboard(['Вывести Дневник', 'Настройки', 'Скачать Дневник'],
#                                              first_button='Заполнить Дневник')
#             else:
#                 keyboard = generate_keyboard(['Настройки', 'Заполнить Дневник'])
#             await bot.delete_message(
#                 chat_id=call.message.chat.id,
#                 message_id=call.message.message_id
#             )
#             await call.message.answer('Рюкзак пуст', keyboard=keyboard)
#         await edit_database(market=market)
#         await start(message=message, state=state)
#     else:
#         if data in backpack_chosen:
#             backpack_chosen.remove(data)
#         else:
#             backpack_chosen.append(data)
#         date_builder = InlineKeyboardBuilder()
#         for product in purchase_history:
#             product_data = purchase_history[product]
#             for index, purchase in enumerate(product_data):
#                 if purchase['used'] is False:
#                     foo = f'{str(product)} : {str(index)}'
#                     if foo in backpack_chosen:
#                         date_builder.button(text=f"{purchase['time']} {product} ✅️️",
#                                             callback_data=f"{product} : {index}")
#                     else:
#                         date_builder.button(text=f"{purchase['time']} {product} ✔️", callback_data=f"{product} : {index}")
#         date_builder.adjust(2, 2)
#         d_new_builder = InlineKeyboardBuilder()
#         d_new_builder.button(text="🎂Использовать 🎂", callback_data="Использовать")
#         d_new_builder.adjust(2, 1)
#         date_builder.attach(d_new_builder)
#         keyboard = date_builder.as_markup()
#         await bot.edit_message_reply_markup(
#             chat_id=call.message.chat.id,
#             message_id=call.message.message_id,
#             reply_markup=keyboard)
#
#
# async def spend_money(gold, market, chosen_store, call):
#     now = datetime.datetime.now()
#     store = market['store']
#     # Форматируем дату и время в нужный формат
#     formatted_date = now.strftime("%Y-%-m-%-d")
#     purchased_products = []
#     if chosen_store:
#         for index, i in enumerate(store):
#             price = int(store[i])
#             product = i
#             if str(index) in chosen_store:
#                 if gold >= price:
#                     gold -= price
#                     market['purchase_history'].setdefault(product, []).append({'price': price, 'time': formatted_date, 'used': False})
#                     purchased_products.append(product)
#                     market['store'][i] = int(market['store'][i]) * 1.05
#                 else:
#                     await call.message.answer('Недостаточно золота на балансе!\n Выполняйте задачи, чтобы его заработать')
#                     return
#     return purchased_products, gold, market
#
#
# @dp.callback_query(ClientState.market)
# async def proceed_market(call: types.CallbackQuery, state: FSMContext):
#     await call.answer()
#     data = call.data
#     user_data = await state.get_data()
#     balance = user_data['balance']
#     gold = int(balance['gold'])
#     market = user_data['market']
#     store = user_data['market']['store']
#     chosen_store = user_data['chosen_store']
#     if data == 'Добавить':
#         await call.message.answer('Введите товары их стоимость через запятую. Например:\nКупить шоколадку : 300, Сходить в кафе: 1000')
#         await state.set_state(ClientState.new_market_product)
#     elif data == 'Удалить':
#         for i in chosen_store:
#             del store[i]
#
#         keyboard = keyboard_builder(checks=False, inp=store, add_dell=True, last_button="💰Потратить 💰",
#                                     chosen=chosen_store, grid=2)
#         await bot.edit_message_reply_markup(
#             chat_id=call.message.chat.id,
#             message_id=call.message.message_id,
#             reply_markup=keyboard)
#         await edit_database(market=market)
#         await state.update_data(chosen_store=[])
#     elif data == 'Потратить':
#         answer = await spend_money(gold, market, chosen_store, call)
#         if answer:
#             purchased_products, gold, market = answer
#             balance['gold'] = gold
#             purchased_products_out = "\n".join(purchased_products)
#             await edit_database(market=market)
#             await call.message.answer(f'Вы приобрели:\n{purchased_products_out}')
#             await state.update_data(balance=balance)
#             await edit_database(balance=balance)
#             keyboard = keyboard_builder(checks=False, inp=store, add_dell=True, last_button="💰Потратить 💰", chosen=chosen_store, grid=2)
#             await bot.edit_message_text(
#                 chat_id=call.message.chat.id,
#                 message_id=call.message.message_id,
#                 text=f"Ваш баланс: {gold}💰",
#                 reply_markup=keyboard
#             )
#
#     else:
#         if data in chosen_store:
#             chosen_store.remove(data)
#         else:
#             chosen_store.append(data)
#         await state.update_data(chosen_store=chosen_store)
#         keyboard = keyboard_builder(checks=False, inp=store, add_dell=True, last_button="💰Потратить 💰",
#                                     chosen=chosen_store, grid=2)
#         await bot.edit_message_reply_markup(
#             chat_id=call.message.chat.id,
#             message_id=call.message.message_id,
#             reply_markup=keyboard)
#
#
# @dp.message(ClientState.new_market_product)
# async def new_market_goods(message, state):
#     user_data = await state.get_data()
#     product_names = message.text.split(',')
#     for product in product_names:
#         product_splited = product.split(' : ')
#         if len(product_splited) != 2 or not product_splited[1].isdigit():
#             await message.answer(f'Соблюдайте порядок,{product} должен выглядеть как "товар : стоимость"')
#             return
#         product_name = product_splited[0]
#         try:
#             price = int(product_splited[1])
#         except ValueError:
#             await message.answer(f'Цена должна быть числом: {product_splited[1]}')
#             return
#         num = len(product_name) - 44
#         if num > 0:
#             await message.answer(
#                 f'"{product_name}" Должно быть короче на {num} cимвол\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
#             return
#         store = user_data['market']['store']
#         store[product_name] = price
#     await state.update_data(market=user_data['market'])
#     await edit_database(market=user_data['market'])
#     await start(state=state, message=message)
#
#
# @dp.message(ClientState.new_market_product_2)
# async def new_market_goods_2(message, state):
#     price = message.text
#     try:
#         price = int(price)
#     except:
#         await message.answer('Стоимость должна быть больше 0')
#     if price <= 0:
#         await message.answer('Стоимость должна быть больше 0')
#     else:
#         user_data = await state.get_data()
#         product_name = user_data['product_name']
#         store = user_data['market']['store']
#         store[product_name] = price
#         await state.update_data(market=user_data['market'])
#         await edit_database(market=user_data['market'])
#         await start(state=state, message=message)


@dp.message(lambda message: message.text and message.text.lower() == 'напоминания', ClientState.settings)
async def notifications(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    await state.update_data(message=message)
    notifications_data = user_data['notifications_data']
    chosen_notifications = notifications_data.get('chosen_notifications', [])
    inp = ['Включено']
    date_builder = InlineKeyboardBuilder()
    for index, job in enumerate(inp):
        if chosen_notifications:
            if job in chosen_notifications:
                date_builder.button(text=f"{job} ✅️", callback_data=f"{index}")
            else:
                date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        else:
            date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
    date_builder.button(text=f"{'Выбрать дату'}", callback_data=f"{1}")
    date_builder.adjust(2, 1)
    notifications_data = user_data['notifications_data']
    if notifications_data.get('hours', ''):
        hours = notifications_data['hours']
        minutes = notifications_data['minutes']
    else:
        hours = 9
        minutes = 0
    await message.answer(reply_markup=date_builder.as_markup(),
                         text=f'Текущее время ежедневных уведомлений {hours}:{minutes}')
    await state.set_state(ClientState.notification_proceed)


@dp.callback_query(ClientState.notification_proceed)
async def notifications_proceed(call, state):
    data = int(call.data)
    user_data = await state.get_data()
    message = user_data['message']
    notifications_data = user_data['notifications_data']
    if notifications_data.get('hours', ''):
        hours = notifications_data['hours']
        minutes = notifications_data['minutes']
    else:
        hours = 9
        minutes = 0
    if data == 0:
        chosen_notifications = notifications_data.setdefault('chosen_notifications', [])
        if 'Включено' in chosen_notifications:
            notifications_data['chosen_notifications'] = []
        else:
            notifications_data['chosen_notifications'] = ['Включено']
        await state.update_data(notifications_data=notifications_data)
        await edit_database(notifications_data=notifications_data)
        date_builder = InlineKeyboardBuilder()
        inp = ['Включено']
        for index, job in enumerate(inp):
            if notifications_data['chosen_notifications']:
                if job in notifications_data['chosen_notifications']:
                    date_builder.button(text=f"{job} ✅️", callback_data=f"{index}")
                else:
                    date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            else:
                date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        date_builder.button(text=f"{'Выбрать дату'}", callback_data=f"{1}")
        date_builder.adjust(2, 1)

        if notifications_data['chosen_notifications'] == ['Включено']:

            job_id = scheduler.add_job(
                tasks_pool_function,
                trigger='cron',
                hour=hours,
                minute=minutes,
                args=(message, state)
                # Replace with user IDs and message
            )
            await state.update_data(job_id=job_id.id)
        else:
            job_id = user_data.get('job_id', '')
            if job_id:
                scheduler.remove_job(job_id=job_id)
                await state.update_data(job_id='')
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=date_builder.as_markup())
    elif data == 1:
        await message.answer('Введите время ежедневных уведомлений заполнить дневник\nв формете часы:минуты')
        await state.set_state(ClientState.notification_set_date)



@dp.message(ClientState.notification_set_date)
async def notification_set_date(message, state):
    user_data = await state.get_data()
    notifications_data = user_data['notifications_data']
    notification_time = message.text.split(':')
    if len(notification_time) != 2:
        await message.answer(f'{message.text} должно быть датой, например 14:20')
        return
    try:
        hours = int(notification_time[0])
        minutes = int(notification_time[1])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            await message.answer('Часы должны быть 0-23, минуты 0-59.')
            return
    except ValueError:
        await message.answer('Часы и минуты должны быть числами.')
        return
    hours = notification_time[0]
    minutes = notification_time[1]
    notifications_data['hours'] = hours
    notifications_data['minutes'] = minutes
    await edit_database(notifications_data=notifications_data)
    await state.update_data(notifications_data=notifications_data)
    job_id = user_data.get('job_id', '')
    if job_id:
        scheduler.remove_job(job_id=job_id)
    job_id = scheduler.add_job(
        tasks_pool_function,
        trigger='cron',
        hour=hours,
        minute=minutes,
        args=(message, state))
    notifications_data['chosen_notifications'] = ['Включено']
    await state.update_data(job_id=job_id.id, notifications_data=notifications_data)
    user_data = await state.get_data()
    await edit_database(notifications_data=notifications_data)
    await message.answer(f'Отлично! Теперь напоминания будут приходить каждый день в {hours}:{minutes}')
    await start(message=message, state=state)


@dp.message(lambda message: message.text and message.text.lower() == 'дела в определенную дату', ClientState.settings)
async def date_jobs_keyboard(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()

    if user_data is not None and isinstance(user_data, dict) and len(user_data):
        # locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
        data = await state.get_data()
        if 'scheduler_arguments' in data:
            output = {key.split('Я напомню вам : ')[1].replace('"', ''):300 for key in data['scheduler_arguments'].keys()}
            keyboard = keyboard_builder(today_tasks=output, chosen=[])
            await message.answer('Ваши задачи', reply_markup=keyboard)
            await message.answer(
                'Для удаления выберите интересующие вас дела и нажмите "Удалить"\n'
                '"Добавить" - если хотите добавить новую задачу',
                reply_markup=generate_keyboard(['В Главное Меню']))
            await state.set_state(ClientState.date_jobs)
        else:
            await message.answer('Введите запланированную задачу', reply_markup=generate_keyboard(['В Главное Меню']))
            await state.set_state(ClientState.date_jobs_1)
    else:
        await start(message=message, state=state)


@dp.callback_query(ClientState.date_jobs)
async def date_jobs_keyboard_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = call.data

    if data == 'Удалить':
        await state.update_data(date_jobs_call=call)
        user_states_data = await state.get_data()
        date_chosen_tasks = user_states_data['date_chosen_tasks']
        scheduler_arguments = user_states_data['scheduler_arguments']
        for itr in date_chosen_tasks:
            for key in list(scheduler_arguments.keys()):
                values = scheduler_arguments[key]
                values_copy = values.copy()
                values_copy['args'] = (state, key)
                if 'date' in values_copy:
                    values_copy['date'] = datetime.datetime.strptime(values['date'], '%Y-%m-%d')
                elif 'run_date' in values_copy:
                    values_copy['run_date'] = datetime.datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                unique_id = generate_unique_id_from_args(values_copy)
                if any(job.id == unique_id for job in scheduler.get_jobs()):
                    scheduler.remove_job(job_id=unique_id)
            for key in scheduler_arguments.keys():
                if key.split('Я напомню вам : ')[1].replace('"', '') == itr:
                    del scheduler_arguments[key]
                    break

        if len(scheduler_arguments) == 0:
            del user_states_data['scheduler_arguments']
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=new_ot_builder.as_markup())
            await state.set_data(user_states_data)


        else:
            scheduler_arguments_inp = [key.split('Я напомню вам : ')[1].replace('"', '')
                                       for key in user_states_data['scheduler_arguments']]
            keyboard = keyboard_builder(today_tasks=scheduler_arguments_inp, chosen=date_chosen_tasks, price_tag=False)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)

        await state.update_data(scheduler_arguments=scheduler_arguments, date_chosen_tasks=[])
        await edit_database(scheduler_arguments=scheduler_arguments)

    elif data == 'Добавить':
        await call.message.answer('Введите новое дело',)
        await state.update_data(date_jobs_call=call)
        await state.set_state(ClientState.date_jobs_1)

    else:
        data = data
        user_states_data = await state.get_data()
        scheduler_arguments = {key.split('Я напомню вам : ')[1].replace('"', ''):300
                               for key in user_states_data['scheduler_arguments'].keys()}
        date_chosen_tasks = user_states_data['date_chosen_tasks']
        if data in date_chosen_tasks:
            date_chosen_tasks.remove(data)
        else:
            date_chosen_tasks.append(data)
        await state.update_data(date_chosen_tasks=date_chosen_tasks)
        keyboard = keyboard_builder(today_tasks=scheduler_arguments, chosen=date_chosen_tasks)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard)


@dp.message(ClientState.date_jobs_1)
async def change_date_jobs_job(message: Message, state: FSMContext) -> None:
    await state.update_data(new_date_jobs=message.text)
    keyboard = generate_keyboard(['В день недели', 'Число месяца', 'Каждый год', 'Разово'])
    await message.answer('Выберите как и когда вы бы желали чтобы вам напомнили об этом деле', reply_markup=keyboard)
    await state.set_state(ClientState.date_jobs_2)


@dp.message(ClientState.date_jobs_2)
async def date_jobs_job_2(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    if user_message == 'в день недели':
        keyboard = keyboard_builder(today_tasks=['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу',
                                         'воскресенье'], grid=1, add_dell=False, price_tag=False, chosen=[])
        await message.answer(
            'В какой день недели?', reply_markup=keyboard)
        await message.answer('можно выбрать сразу несколько', reply_markup=generate_keyboard(buttons=['В Главное Меню']))
        await state.set_state(ClientState.date_jobs_week)
    elif user_message == 'число месяца':
        await message.answer(
            'Какого числа месяца вам нужно напомнить об этом деле?')
        await state.set_state(ClientState.date_jobs_month)
    elif user_message == 'каждый год':
        await message.answer(
            'Введите дату когда вам о нем напомнить в формате день-месяц, например:')
        next_day = datetime.date.today()
        await message.answer(next_day.strftime("%d-%m"))
        await state.set_state(ClientState.date_jobs_year)
    elif user_message == 'разово':
        await message.answer(
            'Введите дату когда вам о нем напомнить в формате год-месяц-день, например:')
        await message.answer(str(datetime.date.today()))
        await state.set_state(ClientState.date_jobs_once)



@dp.callback_query(ClientState.date_jobs_week)
async def date_jobs_week(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = call.data
    user_states_data = await state.get_data()
    message = user_states_data['message']

    date_jobs_week_list = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу',
     'воскресенье']
    date_jobs_week_chosen_tasks = user_states_data['date_jobs_week_chosen_tasks']
    if data == 'Отправить':
        if len(date_jobs_week_chosen_tasks) != 0:
            new_date_jobs = user_states_data['new_date_jobs']
            for day in date_jobs_week_chosen_tasks:
                day_of_week = translate[day]
                out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(day)} {day}'
                await scheduler_list(call, state, out_message, user_states_data, trigger="cron",
                                     day_of_week=day_of_week,
                                     args=new_date_jobs)
            all_days = ''.join(f'\n{day_to_prefix(day)} {day}' for day in date_jobs_week_chosen_tasks)
            out_message = f'Я напомню вам "{new_date_jobs}":{all_days}'
            await call.message.answer(out_message)
            await state.update_data(date_jobs_week_chosen_tasks=[])
            await start(message=message, state=state)

            # global_out_message = f'Я напомню вам : "{new_date_jobs}":\n {(day_to_prefix(day) for day in date_jobs_week_chosen_tasks)} {day}'
    else:
        data = call.data
        data = date_jobs_week_list[int(data)]
        date_jobs_week_chosen_tasks = user_states_data['date_jobs_week_chosen_tasks']

        if data in date_jobs_week_chosen_tasks:
            date_jobs_week_chosen_tasks.remove(data)
        else:
            date_jobs_week_chosen_tasks.append(data)

        keyboard = keyboard_builder(today_tasks=date_jobs_week_list, chosen=date_jobs_week_chosen_tasks, grid=1, add_dell=False, price_tag=False)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard)
        await state.update_data(date_jobs_week_chosen_tasks=date_jobs_week_chosen_tasks)


# @dp.message(ClientState.date_jobs_week)
# async def date_jobs_week(message: Message, state: FSMContext) -> None:
#     user_message = normalized(message.text)
#     user_states_data = await state.get_data()
#     new_date_jobs = user_states_data['new_date_jobs']
#     day_of_week = translate[user_message]
#     out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
#     # now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
#     # hours = now.hour
#     # minutes = (now + timedelta(minutes=1)).minute
#     # await scheduler_list(message, state, out_message, user_states_data, trigger="cron",
#     #                      day_of_week=day_of_week,
#                          # args=new_date_jobs, .hour=hours, minute=minutes)
#     await scheduler_list(message, state, out_message, user_states_data, trigger="cron",
#                          day_of_week=day_of_week,
#                          args=new_date_jobs)
#
#     # if 'call' in user_states_data:
#     #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_month)
async def date_jobs_month(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    day_of_month = message.text
    out_message = f'Я напомню вам : "{new_date_jobs}" каждый {day_of_month} день месяца'
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    await scheduler_list(message, state, out_message, user_states_data, day=day_of_month, trigger="cron",
                         args=new_date_jobs)
    await start(message=message, state=state)
    # if 'call' in user_states_data:
    #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_year)
async def date_jobs_year(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    date = datetime.datetime.strptime(message.text, '%d-%m')
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    out_message = f'Я напомню вам : "{new_date_jobs}" каждое {date.day} {date.strftime("%B")}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron", day=date.day, month=date.month,
                         args=new_date_jobs)
    await start(message=message, state=state)
    # if 'call' in user_states_data:
    #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_once)
async def date_jobs_once(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data.get('new_date_jobs', 'Напоминание') # Безопасное получение

    try:
        # Получаем только дату, время будет текущим
        user_date_part = datetime.datetime.strptime(message.text, '%Y-%m-%d').date()
    except ValueError:
        await message.answer('Неверный формат даты. Используйте ГГГГ-ММ-ДД, например, 2025-12-31.')
        return

    # Получаем текущее время в целевом часовом поясе
    now_aware = datetime.datetime.now(TARGET_TZ)
    current_time_part = now_aware.time()

    # Комбинируем дату от пользователя и текущее время (получаем наивный datetime)
    naive_dt = datetime.datetime.combine(user_date_part, datetime.time(0, 0))

    # Делаем datetime "знающим" о часовом поясе (локализуем)
    # Важно: localize используется для наивных dt, которые УЖЕ представляют время в этом поясе
    scheduled_dt_aware = TARGET_TZ.localize(naive_dt)

    # Добавляем 2 минуты
    # scheduled_dt_aware += datetime.timedelta(minutes=2)

    # Проверяем, что рассчитанное время > текущего времени (оба aware)
    if now_aware < scheduled_dt_aware:
        # Форматируем сообщение для пользователя (можно добавить время)
        # Для русских месяцев лучше использовать locale или ручной маппинг
        month_ru = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"][scheduled_dt_aware.month - 1]
        out_message = f'Я напомню вам : "{new_date_jobs}" {scheduled_dt_aware.day} {month_ru} {scheduled_dt_aware.year} в {scheduled_dt_aware.strftime("%H:%M")}'

        # Передаем задачу в планировщик
        # Убедитесь, что функция scheduler_list или та, что вызывает scheduler.add_job,
        # принимает и использует aware datetime объект.
        # НЕ передавайте строку strftime!
        try:
            await scheduler_list(message, state, out_message, user_states_data, trigger="date",
                                 run_date=scheduled_dt_aware.strftime("%Y-%m-%d %H:%M"),
                                 args=new_date_jobs)

        except Exception as e:
             await message.answer("Не удалось запланировать напоминание.")

        await start(message=message, state=state) # Возврат в начальное состояние

    else:
        # Сообщаем пользователю точное время, которое не подошло
        await message.answer(f'Рассчитанное время {scheduled_dt_aware.strftime("%Y-%m-%d %H:%M %Z%z")} уже в прошлом.')


@dp.message(lambda message: message.text and message.text.lower() == 'добавить разовые дела', ClientState.settings)
async def change_one_time_jobs(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'one_time_jobs' in user_data:
        await message.answer(
            'Введите разовые дела, которые вы хотели бы добавить через запятую', reply_markup=remove_markup)
    else:
        await message.answer('Введите новый список разовых дел через запятую',
                             reply_markup=generate_keyboard(['В Главное Меню']))
    await state.set_state(ClientState.one_time_jobs_2)


@dp.message(ClientState.one_time_jobs_2)
async def change_one_time_jobs_2(message: Message, state: FSMContext) -> None:
    to_add_one_time_jobs = normalized(message.text).split(', ')
    user_states_data = await state.get_data()
    if 'one_time_jobs' in user_states_data:
        one_time_jobs = user_states_data['one_time_jobs']
    else:
        one_time_jobs = []
    for i in to_add_one_time_jobs:
        num = len(i) - 67
        if num > 0:
            await message.answer(
                f'"{i}" Должно быть короче на {num} cимвол\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
            return
        else:
            one_time_jobs.append(i)

    one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']
    if 'one_time_call' in user_states_data:
        call = user_states_data['one_time_call']
        keyboard = keyboard_builder(today_tasks=one_time_jobs, chosen=one_time_chosen_tasks, grid=1)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard
        )
    else:
        await message.answer('Отлично! Ваш список разовых дел обновлен')
    await edit_database(one_time_jobs=one_time_jobs)
    await state.update_data(one_time_jobs=one_time_jobs)
    await start(message=message, state=state)


@dp.message(ClientState.change_tasks_pool_1)
async def change_tasks_pool_1(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'tasks_pool' in user_data:
        tasks_pool = user_data['tasks_pool']
    else:
        tasks_pool = []
    user_message = normalized(message.text)
    str_data = user_message.split(', ')
    for i in str_data:
        num = len(i) - 44
        if num > 0:
            await message.answer(f'"{i}" Должно быть короче на {num} cимвол\n Попробуйте использовать эмодзи 🎸🕺🍫')
            return
        else:
            tasks_pool.append(i)
    if 'daily_chosen_tasks' in user_data:
        daily_chosen_tasks = user_data['daily_chosen_tasks']
        if 'call' in user_data:
            call = user_data['call']
            keyboard = keyboard_builder(tasks_pool=list(set(tasks_pool)), chosen=daily_chosen_tasks, grid=1)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)

            if len(tasks_pool) == 0:
                if 'messages_to_edit' in user_data:
                    messages_to_edit = user_data['messages_to_edit']
                    await bot.delete_message(message.chat.id, messages_to_edit['message'])
                    await bot.edit_message_text('Добавьте список дел', message.chat.id, messages_to_edit['keyboard'])
    await state.update_data(tasks_pool=list(set(tasks_pool)))
    await edit_database(tasks_pool=list(set(tasks_pool)))
    path = f"{message.from_user.id}_Diary.xlsx"
    if os.path.exists(path):
        keyboard = generate_keyboard(
            ['Вывести Дневник', 'Настройки'],
            first_button='Заполнить Дневник')
    else:
        keyboard = generate_keyboard(['Заполнить Дневник'], last_button='Настройки')
    await message.answer('Отлично, ваш список ежедневных дел обновлен!', keyboard=keyboard)
    await start(message=message, state=state)





@dp.message(lambda message: message.text)
async def handle_message(message: Message, state: FSMContext):
    await start(message=message, state=state)


async def on_error_handler(event: ErrorEvent):
    """
    Обработчик для перехвата и отправки ошибок администратору.
    Отправляет traceback отдельным файлом.
    """
    logging.error(f"Произошла ошибка в боте!", exc_info=event.exception)

    # Формируем полный traceback
    tb_str = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))

    # Формируем краткое сообщение для администратора
    short_error_message = (
        f"<b>❗️ Произошла ошибка!</b>\n\n"
        f"<b>Тип:</b> {type(event.exception).__name__}\n"
        f"<b>Текст:</b> {event.exception}\n\n"
        f"Полный traceback и данные Update в прикрепленных файлах."
    )

    # Создаем файлы в памяти
    traceback_file = BufferedInputFile(tb_str.encode('utf-8'), filename="traceback.txt")
    update_file = BufferedInputFile(
        event.update.model_dump_json(indent=2, exclude_none=True).encode('utf-8'),
        filename="update.json"
    )

    try:
        # Сначала отправляем краткое сообщение
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=short_error_message,
            parse_mode='HTML'
        )
        # Затем отправляем файлы
        await bot.send_document(chat_id=ADMIN_ID, document=traceback_file)

        # (Опционально) Отправляем пользователю сообщение
        user_chat_id = event.update.message.chat.id if event.update.message else event.update.callback_query.message.chat.id
        if user_chat_id:
             await bot.send_message(
                chat_id=user_chat_id,
                text="😕 Ой, что-то пошло не так. Я уже сообщил разработчику о проблеме. Пожалуйста, попробуйте снова позже."
             )

    except Exception as e:
        logging.error(f"Критическая ошибка: не удалось отправить уведомление об ошибке. Причина: {e}")

async def main():
    # --- ДОБАВЬТЕ ЭТО ---
    # Настройка логирования для вывода информации в консоль
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    )

    # Регистрация обработчика ошибок
    dp.error.register(on_error_handler)
    logging.info("Обработчик ошибок зарегистрирован.")
    # --------------------

    scheduler.start()

    await database_start()
    await dp.start_polling(bot)
    await scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())