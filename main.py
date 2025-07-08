from asyncio import tasks

from aiogram.types.error_event import ErrorEvent
from aiogram.types import BufferedInputFile
import asyncio
import datetime
import os
import logging # <--- ДОБАВИТЬ
import traceback
import re
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



@dp.message(lambda message: message.text and message.text.lower() == 'настройки')
async def settings(message: Message, state: FSMContext = None) -> None:
    user_data = await state.get_data()
    if user_data is not None and isinstance(user_data, dict) and len(user_data):
        user_data = await state.get_data()
        inp = ['Напоминания', 'Дела в определенную дату', 'Опрашиваемые данные', 'Редактировать список дел', 'Разовые дела']

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
    edit_tasks_pool_chosen = user_data.get('edit_tasks_pool_chosen', [])

    # Инициализируем пустой список для выбранных на удаление задач
    await state.update_data(tasks_to_delete=[])

    # Используем немного измененный keyboard_builder или создадим новый
    keyboard = keyboard_builder(tasks_pool=tasks_pool, add_dell=True, chosen=edit_tasks_pool_chosen)
    await message.answer(
        "Ваш общий список дел",
        reply_markup=keyboard
    )
    await state.set_state(ClientState.edit_tasks_pool)


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
        today_tasks = user_data.get('today_tasks', {})
        task = user_data.get('temp', None)
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


@dp.callback_query(ClientState.edit_tasks_pool)
async def process_edit_tasks_pool_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    edit_tasks_pool_chosen = user_data.get('edit_tasks_pool_chosen', [])
    if call.data == "Удалить":
        if not edit_tasks_pool_chosen:
            await call.answer("Вы ничего не выбрали для удаления.", show_alert=True)
            return

        for name in edit_tasks_pool_chosen:
            tasks_pool.remove(name)

        # Обновляем данные в состоянии и в БД
        keyboard = keyboard_builder(tasks_pool=tasks_pool, add_dell=True, chosen=edit_tasks_pool_chosen)
        await state.update_data(tasks_pool=tasks_pool, edit_tasks_pool_chosen=[])
        await edit_database(tasks_pool=tasks_pool)
        await call.message.edit_reply_markup(reply_markup=keyboard)
    elif call.data == 'Добавить':
        await call.message.answer('Введите список дел который хотите добавить через запятую')
        await state.set_state(ClientState.add_tasks_pool)
        await state.update_data(call=call)
    else:
        data = int(call.data)
        if tasks_pool[data] in edit_tasks_pool_chosen:
            edit_tasks_pool_chosen.remove(tasks_pool[data])
        else:
            edit_tasks_pool_chosen.append(tasks_pool[data])
        keyboard = keyboard_builder(tasks_pool=tasks_pool, chosen=edit_tasks_pool_chosen, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)
        await state.update_data(edit_tasks_pool_chosen=edit_tasks_pool_chosen)


@dp.message(ClientState.add_tasks_pool)
async def add_tasks_pool(message, state: FSMContext):
    data = message.text
    normalized = re.sub(r'\s*,\s*', ', ', data).split(', ')
    user_data = await state.get_data()
    tasks_pool = user_data.get('tasks_pool', [])
    for word in normalized:
        tasks_pool.append(word)
    tasks_pool = list(set(tasks_pool))
    keyboard = keyboard_builder(tasks_pool=tasks_pool, add_dell=True)
    if 'call' in user_data:
        call = user_data.get('call', None)
        await call.message.edit_reply_markup(reply_markup=keyboard)
    else:
        await message.answer('Ваш список общих дел обновлен!', reply_markup=generate_keyboard(buttons=['В главное меню']))
    await state.update_data(tasks_pool=tasks_pool)
    await edit_database(tasks_pool=tasks_pool)






@dp.callback_query(ClientState.greet)
async def process_tasks_pool(call: types.CallbackQuery, state: FSMContext, flag=False):
    await call.answer()
    data = call.data
    user_data = await state.get_data()

    tasks_pool = user_data.get('tasks_pool', [])
    today_tasks = user_data.get('today_tasks', {})
    daily_chosen_tasks = user_data.get('daily_chosen_tasks', [])
    one_time_tasks = user_data.get('one_time_tasks', [])
    daily_tasks = user_data.get('daily_tasks', {})

    if data == 'Отправить':
        await state.update_data(daily_chosen_tasks=daily_chosen_tasks)
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
        keyboard = keyboard_builder(
            today_tasks=today_tasks,
            chosen=daily_chosen_tasks,  # Choices are now cleared
            grid=1,
            add_dell=True,
            add_save=True,
            last_button="🚀Отправить 🚀",
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)

    elif data == 'Добавить':
        tasks_pool_clear = [i for i in (tasks_pool+one_time_tasks) if i not in today_tasks.values()]
        keyboard = keyboard_builder(tasks_pool=tasks_pool_clear,
                                    add_dell=False,
                                    )

        await call.message.answer(
            'Ниже список ваших общих дел.\nВыберите те, которые хотите добавить в ваше расписание на сегодня',
        reply_markup=keyboard)
        await state.update_data(call=call)
        await state.set_state(ClientState.change_tasks_pool_1)

    else:
        if data in daily_chosen_tasks:
            daily_chosen_tasks.remove(data)
        else:
            daily_chosen_tasks.append(data)
        await state.update_data(daily_chosen_tasks=daily_chosen_tasks)

        # Rebuild keyboard to show the checkmark
        keyboard = keyboard_builder(
            today_tasks=today_tasks,
            chosen=daily_chosen_tasks,
            grid=1,
            add_dell=True,
            last_button="🚀Отправить 🚀",
            add_save=True,
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)

@dp.callback_query(ClientState.change_tasks_pool_1)
async def proceed_tasks_pool_1(call, state: FSMContext) -> None:
    await call.answer()
    user_data = await state.get_data()
    data = int(call.data)
    tasks_pool = user_data.get('tasks_pool', [])
    today_tasks = user_data.get('today_tasks', {})
    one_time_tasks = user_data.get('one_time_tasks', [])
    tasks_pool_clear = [i for i in tasks_pool+one_time_tasks if i not in today_tasks.values()]
    await call.message.answer(f'Вы выбрали: {tasks_pool_clear[data]}\n'
                              f'Введите время в формате ЧЧ:ММ, на которое вы хотите назначить это дело.')
    await state.update_data(temp=tasks_pool_clear[data])
    await state.set_state(ClientState.new_today_tasks)



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
async def process_personal_rate(message: Message, state: FSMContext, flag=False) -> None:
    try:
        personal_rate = int(message.text)
        if not (0 <= personal_rate <= 10):
            raise ValueError
    except ValueError:
        await message.answer(f'"{message.text}" должен быть числом от 0 до 10')
        return
    await state.update_data(personal_rate=personal_rate, message=message)
    await message.answer('За вчера или за сегодня?', reply_markup=keyboard_builder(tasks_pool=['За вчера', 'За сегодня'], grid=2))
    await state.set_state(ClientState.personal_rate_1)



@dp.callback_query(ClientState.personal_rate_1)
async def personal_rate_1(call, state) -> None:
    await call.answer()
    user_data = await state.get_data()
    data = call.data
    if data == '0':
        today=False
    else:
        today=True
    db_updates = {}
    today_tasks = user_data.get('today_tasks', {})
    daily_chosen_tasks_keys = user_data.get('daily_chosen_tasks', [])
    activities = [today_tasks[key] for key in daily_chosen_tasks_keys if key in today_tasks]
    daily_chosen_tasks = user_data.get('daily_chosen_tasks', {})
    one_time_tasks = user_data.get('one_time_tasks', [])
    personal_rate = user_data.get('personal_rate', None)
    message = user_data.get('message', None)
    for time in daily_chosen_tasks:
        if today_tasks[time] in one_time_tasks:
            one_time_tasks.remove(today_tasks[time])
            flag = True
    if flag:
        await state.update_data(one_time_tasks=one_time_tasks)
        db_updates['one_time_tasks'] = one_time_tasks
    data_for_excel = {
        'tasks_pool': user_data['tasks_pool'],
        'date': datetime.datetime.now(),  # Assuming this is for yesterday
        'activities': activities,
        'user_message': user_data['user_message'],
        'sleep_quality': user_data['sleep_quality'],
        'my_steps': user_data['my_steps'],
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
        await edit_database(**db_updates)
    previous_diary = user_data.get('previous_diary', None)
    if previous_diary:
        try:
            await bot.delete_message(message.chat.id, previous_diary)
        except:
            pass
    saved_daily_tasks = user_data.get('daily_tasks', {})
    await state.update_data(daily_chosen_tasks=[], one_time_chosen_tasks=[], session_accrued_tasks=[],
                            today_tasks=saved_daily_tasks.copy())
    await tasks_pool_function(message, state)


@dp.message(lambda message: message.text and message.text.lower() == 'опрашиваемые данные', ClientState.settings)
async def collected_data(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    chosen_collected_data = user_data.get('chosen_collected_data', [])
    keyboard = keyboard_builder(tasks_pool=['Шаги', 'Сон'], add_dell=False, chosen=chosen_collected_data, grid=2, price_tag=False)
    await message.answer(reply_markup=keyboard, text='Зеленая галочка означает что настройки будут работать,'
                                                     ' серая - что выключены')
    await state.set_state(ClientState.collected_data)


@dp.callback_query(ClientState.collected_data)
async def collected_data_proceed(call, state):
    await call.answer()
    data = int(call.data)
    user_data = await state.get_data()
    if 'chosen_collected_data' in user_data:
        chosen_collected_data = user_data.get('chosen_collected_data', [])
        if ['Шаги', 'Сон'][data] in chosen_collected_data:
            chosen_collected_data.remove(['Шаги', 'Сон'][data])
        else:
            chosen_collected_data.append(['Шаги', 'Сон'][data])
    else:
        chosen_collected_data = [['Шаги', 'Сон'][data]]
    await state.update_data(chosen_collected_data=chosen_collected_data)
    await state.update_data(daily_chosen_tasks=[])
    await edit_database(chosen_collected_data=chosen_collected_data)
    keyboard = keyboard_builder(tasks_pool=['Шаги', 'Сон'], chosen=chosen_collected_data,
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


@dp.message(lambda message: message.text and message.text.lower() == 'напоминания', ClientState.settings)
async def notifications(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    await state.update_data(message=message)
    notifications_data = user_data.get('notifications_data', {})
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
    notifications_data = user_data.get('notifications_data', {})
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
    await call.answer()
    data = int(call.data)
    user_data = await state.get_data()
    message = user_data.get('message', None)
    notifications_data = user_data.get('notifications_data', {})
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
    notifications_data = user_data.get('notifications_data', {})
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
    await state.update_data(message=message)
    if user_data is not None and isinstance(user_data, dict) and len(user_data):
        # locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
        data = await state.get_data()
        if 'scheduler_arguments' in data:
            output = [key.split('Я напомню вам : ')[1].replace('"', '') for key in data['scheduler_arguments'].keys()]
            keyboard = keyboard_builder(tasks_pool=output, chosen=[])
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
        user_data = await state.get_data()
        date_chosen_tasks = user_data.get('date_chosen_tasks', [])
        scheduler_arguments = user_data.get('scheduler_arguments', {})
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
            del user_data['scheduler_arguments']
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=new_ot_builder.as_markup())
            await state.set_data(user_data)


        else:
            scheduler_arguments_inp = [key.split('Я напомню вам : ')[1].replace('"', '')
                                       for key in user_data['scheduler_arguments']]
            keyboard = keyboard_builder(tasks_pool=scheduler_arguments_inp, chosen=date_chosen_tasks, price_tag=False)
            await call.message.edit_reply_markup(reply_markup=keyboard)

        await state.update_data(scheduler_arguments=scheduler_arguments, date_chosen_tasks=[])
        await edit_database(scheduler_arguments=scheduler_arguments)

    elif data == 'Добавить':
        await call.message.answer('Введите новое дело',)
        await state.update_data(date_jobs_call=call)
        await state.set_state(ClientState.date_jobs_1)

    else:
        data = int(data)
        user_data = await state.get_data()
        scheduler_arguments = [key.split('Я напомню вам : ')[1].replace('"', '')
                               for key in user_data['scheduler_arguments'].keys()]
        date_chosen_tasks = user_data.get('date_chosen_tasks', [])
        if scheduler_arguments[data] in date_chosen_tasks:
            date_chosen_tasks.remove(scheduler_arguments[data])
        else:
            date_chosen_tasks.append(scheduler_arguments[data])
        await state.update_data(date_chosen_tasks=date_chosen_tasks)
        keyboard = keyboard_builder(tasks_pool=scheduler_arguments, chosen=date_chosen_tasks, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)


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
        keyboard = keyboard_builder(tasks_pool=['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу',
                                         'воскресенье'], grid=1, add_dell=False, price_tag=False, chosen=[], last_button="🚀Отправить 🚀")
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
    user_data = await state.get_data()
    message = user_data.get('message', None)
    date_jobs_week_list = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу',
     'воскресенье']
    date_jobs_week_chosen_tasks = user_data.get('date_jobs_week_chosen_tasks', [])
    if data == 'Отправить':
        if len(date_jobs_week_chosen_tasks) != 0:
            new_date_jobs = user_data.get('new_date_jobs', {})
            for day in date_jobs_week_chosen_tasks:
                day_of_week = translate[day]
                out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(day)} {day}'
                await scheduler_list(call, state, out_message, user_data, trigger="cron",
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
        date_jobs_week_chosen_tasks = user_data.get('date_jobs_week_chosen_tasks', [])

        if data in date_jobs_week_chosen_tasks:
            date_jobs_week_chosen_tasks.remove(data)
        else:
            date_jobs_week_chosen_tasks.append(data)

        keyboard = keyboard_builder(tasks_pool=date_jobs_week_list, chosen=date_jobs_week_chosen_tasks, grid=1, add_dell=False, price_tag=False, last_button="🚀Отправить 🚀")
        await call.message.edit_reply_markup(reply_markup=keyboard)
        await state.update_data(date_jobs_week_chosen_tasks=date_jobs_week_chosen_tasks)


# @dp.message(ClientState.date_jobs_week)
# async def date_jobs_week(message: Message, state: FSMContext) -> None:
#     user_message = normalized(message.text)
#     user_data = await state.get_data()
#     new_date_jobs = user_data.get('new_date_jobs']
#     day_of_week = translate[user_message]
#     out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
#     # now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
#     # hours = now.hour
#     # minutes = (now + timedelta(minutes=1)).minute
#     # await scheduler_list(message, state, out_message, user_data, trigger="cron",
#     #                      day_of_week=day_of_week,
#                          # args=new_date_jobs, .hour=hours, minute=minutes)
#     await scheduler_list(message, state, out_message, user_data, trigger="cron",
#                          day_of_week=day_of_week,
#                          args=new_date_jobs)
#
#     # if 'call' in user_data:
#     #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_month)
async def date_jobs_month(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', {})
    day_of_month = message.text
    out_message = f'Я напомню вам : "{new_date_jobs}" каждый {day_of_month} день месяца'
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    await scheduler_list(message, state, out_message, user_data, day=day_of_month, trigger="cron",
                         args=new_date_jobs)
    await start(message=message, state=state)
    # if 'call' in user_data:
    #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_year)
async def date_jobs_year(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', {})
    date = datetime.datetime.strptime(message.text, '%d-%m')
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    out_message = f'Я напомню вам : "{new_date_jobs}" каждое {date.day} {date.strftime("%B")}'
    await scheduler_list(message, state, out_message, user_data, trigger="cron", day=date.day, month=date.month,
                         args=new_date_jobs)
    await start(message=message, state=state)
    # if 'call' in user_data:
    #     await rebuild_keyboard(state, 'date_chosen_tasks')


@dp.message(ClientState.date_jobs_once)
async def date_jobs_once(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', 'Напоминание') # Безопасное получение

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
            await scheduler_list(message, state, out_message, user_data, trigger="date",
                                 run_date=scheduled_dt_aware.strftime("%Y-%m-%d %H:%M"),
                                 args=new_date_jobs)

        except Exception as e:
             await message.answer("Не удалось запланировать напоминание.")

        await start(message=message, state=state) # Возврат в начальное состояние

    else:
        # Сообщаем пользователю точное время, которое не подошло
        await message.answer(f'Рассчитанное время {scheduled_dt_aware.strftime("%Y-%m-%d %H:%M %Z%z")} уже в прошлом.')


@dp.message(lambda message: message.text and message.text.lower() == 'разовые дела', ClientState.settings)
async def change_one_time_tasks(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    one_time_tasks = user_data.get('one_time_tasks', [])
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    keyboard = keyboard_builder(tasks_pool=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
    await message.answer('Ваши разовые дела', reply_markup=keyboard)
    # if one_time_tasks:
    #     await message.answer(
    #         'Введите разовые дела, которые вы хотели бы добавить через запятую', reply_markup=remove_markup)
    # else:
    #     await message.answer('Введите новый список разовых дел через запятую',
    #                          reply_markup=generate_keyboard(['В Главное Меню']))
    await state.set_state(ClientState.one_time_tasks_2)


@dp.callback_query(ClientState.one_time_tasks_2)
async def change_one_time_tasks_2(call, state) -> None:
    await call.answer()
    data = call.data
    user_data = await state.get_data()
    one_time_tasks = user_data.get('one_time_tasks', [])
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    if data == 'Добавить':
        await call.message.answer('Введите новый список разовых дел через запятую',
                                                      reply_markup=generate_keyboard(['В Главное Меню']))
        await state.update_data(call=call)
        await state.set_state(ClientState.one_time_tasks_3)
    elif data == 'Удалить':
        for i in one_time_chosen_tasks:
            one_time_tasks.remove(i)
        await edit_database(one_time_tasks=one_time_tasks)
        await state.update_data(one_time_chosen_tasks=[], one_time_tasks=one_time_tasks)
        keyboard = keyboard_builder(tasks_pool=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)
    else:
        data = int(data)
        if one_time_tasks[data] in one_time_chosen_tasks:
            one_time_chosen_tasks.remove(one_time_tasks[data])
        else:
            one_time_chosen_tasks.append(one_time_tasks[data])
        keyboard = keyboard_builder(tasks_pool=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
        await call.message.edit_reply_markup(reply_markup=keyboard)


@dp.message(ClientState.one_time_tasks_3)
async def change_one_time_tasks_3(message: Message, state: FSMContext) -> None:
    user_tasks = normalized(message.text).split(', ')
    user_data = await state.get_data()
    one_time_tasks = user_data.get('one_time_tasks', [])
    one_time_chosen_tasks = user_data.get('one_time_chosen_tasks', [])
    for i in user_tasks:
        num = len(i) - 64
        if num > 0:
            await message.answer(
                f'"{i}" Должно быть короче на {num} cимвол\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
            return
        else:
            one_time_tasks.append(i)
    one_time_tasks = list(set(one_time_tasks))
    call = user_data.get('call', None)
    keyboard = keyboard_builder(tasks_pool=one_time_tasks, chosen=one_time_chosen_tasks, grid=1, add_dell=True)
    await call.message.edit_reply_markup(reply_markup=keyboard)
    await edit_database(one_time_tasks=one_time_tasks)
    await state.update_data(one_time_tasks=one_time_tasks, one_time_chosen_tasks=[])
    await call.message.answer('Ваш список разовых дел обновлен')
    # await go_to_main_menu(call.message, state)









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