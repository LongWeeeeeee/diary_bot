import asyncio
import datetime
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlite import database_start, edit_database
from functions import generate_keyboard, diary_out, add_day_to_excel, normalized,\
    daily_jobs, handle_new_user, keyboard_builder, generate_unique_id_from_args,\
    start, dp, ClientState, bot, negative_responses, remove_markup, scheduler


@dp.message(lambda message: message.text is not None and message.text.lower() == 'заполнить дневник')
async def fill_diary(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if user_data:
        if 'daily_tasks' in user_data:
            await daily_jobs(message, state)
        else:
            await handle_new_user(message, state)
    else:
        await start(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'вывести дневник')
async def diary_output(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if len(user_data):
        await diary_out(message)
        await state.set_state(ClientState.greet)
    else:
        await start(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'скачать дневник')
async def download_diary(message: Message, state):
    user_data = await state.get_data()
    if len(user_data):
        try:
            sent_message = await message.answer_document(
                document=FSInputFile(f'{message.from_user.id}_Diary.xlsx'),
                disable_content_type_detection=True,
            )
            return sent_message
        except FileNotFoundError:
            await message.answer('Сначала заполните данные')
    else:
        await start(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'настройки')
async def settings(message: Message, state: FSMContext = None) -> None:
    user_data = await state.get_data()
    if len(user_data):
        user_data = await state.get_data()
        if 'one_time_jobs' in user_data:
            if 'personal_records' in user_data:
                inp = ['Дела в определенную дату', 'Мои рекорды', 'Напоминания', 'Опрашиваемые данные']
            else:
                inp = ['Дела в определенную дату', 'Опрашиваемые данные', 'Напоминания',]
        else:
            if 'personal_records' in user_data:
                inp = ['Добавить Разовые Дела', 'Дела в определенную дату', 'Мои рекорды', 'Напоминания', 'Опрашиваемые данные']
            else:
                inp = ['Добавить Разовые Дела', 'Дела в определенную дату', 'Напоминания', 'Опрашиваемые данные']

        keyboard = generate_keyboard(buttons=inp, last_button='В Главное Меню')
        await message.answer(text='Ваши Настройки', reply_markup=keyboard)
        await state.set_state(ClientState.settings)
    else:
        await start(message, state)


@dp.callback_query(ClientState.greet)
async def process_daily_jobs(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = call.data
    user_states_data = await state.get_data()
    daily_chosen_tasks = user_states_data['daily_chosen_tasks']
    if data == 'Отправить':
        try:
            await state.update_data(daily_chosen_tasks=daily_chosen_tasks)
            one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']
            one_time_jobs = user_states_data['one_time_jobs']
            keyboard = keyboard_builder(inp=one_time_jobs, chosen=one_time_chosen_tasks, grid=1)
            await call.message.answer('Отметьте разовые дела', reply_markup=keyboard)
            await state.set_state(ClientState.one_time_jobs_proceed)

        except KeyError:
            collected_data = user_states_data['chosen_collected_data']
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
                    'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
                await state.set_state(ClientState.about_day)

    elif data == 'Удалить':
        daily_tasks = user_states_data['daily_tasks']
        for index in daily_chosen_tasks:
            daily_tasks.remove(index)
        if len(daily_tasks):
            keyboard = keyboard_builder(inp=daily_tasks, grid=2)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)

            await state.update_data(chosen_tasks=[], daily_tasks=daily_tasks)
        else:
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            del user_states_data['daily_tasks']
            await state.set_data(user_states_data)
            await bot.edit_message_text(text='Добавьте список дел', message_id=call.message.id,
                                        chat_id=call.message.chat.id, reply_markup=new_ot_builder.as_markup())
        await edit_database(daily_tasks=daily_tasks)

    elif data == 'Добавить':
        await call.message.answer('Введите ежедневные дела, которые вы хотели бы добавить через запятую')
        await state.update_data(call=call)
        await state.set_state(ClientState.change_daily_jobs_1)

    else:
        daily_tasks = user_states_data['daily_tasks']
        data = int(data)
        if daily_tasks[data] in daily_chosen_tasks:
            daily_chosen_tasks.remove(daily_tasks[data])
        else:
            daily_chosen_tasks.append(daily_tasks[data])
        await state.update_data(daily_chosen_tasks=daily_chosen_tasks)
        keyboard = keyboard_builder(inp=daily_tasks, chosen=daily_chosen_tasks, grid=2)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard)


@dp.callback_query(ClientState.one_time_jobs_proceed)
async def process_one_time(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = call.data
    user_states_data = await state.get_data()
    one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']
    one_time_jobs = user_states_data['one_time_jobs']
    excel_chosen_tasks = user_states_data['excel_chosen_tasks']
    if data == 'Отправить':
        if len(one_time_chosen_tasks) != 0:
            excel_chosen_tasks += one_time_chosen_tasks
            await state.update_data(excel_chosen_tasks=one_time_chosen_tasks)
            user_states_data['excel_chosen_tasks'] = one_time_chosen_tasks
            for itr in one_time_chosen_tasks:
                one_time_jobs.remove(itr)
            if not len(one_time_jobs):
                await bot.delete_message(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    )
                del user_states_data['one_time_jobs']
                await state.set_data(user_states_data)

            else:
                keyboard = keyboard_builder(inp=one_time_jobs, chosen=one_time_chosen_tasks)
                await bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=keyboard
                )
                await state.update_data(one_time_jobs=one_time_jobs)
        await edit_database(one_time_jobs=one_time_jobs)
        user_states_data = await state.get_data()
        await state.set_data(user_states_data)
        collected_data = user_states_data['chosen_collected_data']

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
                'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
            await state.set_state(ClientState.about_day)
    elif data == 'Удалить':
        for index in one_time_chosen_tasks:
            one_time_jobs.remove(index)
        if len(one_time_jobs):
            keyboard = keyboard_builder(inp=one_time_jobs, grid=1)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)
            await state.update_data(one_time_jobs=one_time_jobs)

        else:
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            del user_states_data['one_time_jobs']
            await state.set_data(user_states_data)
            await bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                        reply_markup=new_ot_builder.as_markup(), text='Добавьте список дел')
        await state.update_data(chosen_tasks=[])
        await edit_database(one_time_jobs=one_time_jobs)

    elif data == 'Добавить':
        await call.message.answer('Введите разовые дела, которые вы хотели бы добавить через запятую')
        await state.update_data(one_time_call=call)
        await state.set_state(ClientState.one_time_jobs_2)

    else:
        data = int(call.data)
        one_time_jobs = user_states_data['one_time_jobs']
        one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']

        if one_time_jobs[data] in one_time_chosen_tasks:
            one_time_chosen_tasks.remove(one_time_jobs[data])
        else:
            one_time_chosen_tasks.append(one_time_jobs[data])

        keyboard = keyboard_builder(inp=one_time_jobs, chosen=one_time_chosen_tasks, grid=1)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard)
        await state.update_data(one_time_chosen_tasks=one_time_chosen_tasks)


@dp.message(ClientState.steps)
async def process_steps(message: Message, state: FSMContext) -> None:
    if message.text not in negative_responses:
        try:
            await state.update_data(my_steps=int(message.text))
            await message.answer('Введите индекс качества сна')
            await state.set_state(ClientState.total_sleep)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(my_steps=0.0)
        await message.answer('Введите индекс качества сна')

        await state.set_state(ClientState.total_sleep)


@dp.message(ClientState.total_sleep)
async def process_total_sleep(message: Message, state: FSMContext) -> None:
    if message.text not in negative_responses:
        try:
            user_message = float(message.text.replace(',', '.'))
            await state.update_data(sleep_quality=user_message)
            await message.answer('Хочешь рассказать как прошел день?'
                                 ' Это поможет отслеживать почему день был хороший или нет')
            await state.set_state(ClientState.about_day)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(sleep_quality=0)
        await message.answer(
            'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
        await state.set_state(ClientState.about_day)


@dp.message(ClientState.about_day)
async def process_about_day(message: Message, state: FSMContext) -> None:
    user_message = message.text
    if user_message not in negative_responses:
        await state.update_data(user_message=message.text)
        await message.answer('Насколько из 10 оцениваете день?')
        await state.set_state(ClientState.personal_rate)
    else:
        await state.update_data(user_message='-')
        await message.answer('Насколько из 10 оцениваете день?')
        await state.set_state(ClientState.personal_rate)


@dp.message(ClientState.personal_rate)
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    personal_rate = int(message.text)
    if 0 <= personal_rate <= 10:
        user_states_data = await state.get_data()
        data = {
            'daily_tasks': user_states_data['daily_tasks'],
            'date': datetime.datetime.now(),
            'activities': user_states_data['daily_chosen_tasks'],
            'user_message': user_states_data['user_message'],
            'sleep_quality': user_states_data['sleep_quality'],
            'my_steps': user_states_data['my_steps'],
        }
        if 'personal_records' in user_states_data:
            data['personal_records'] = user_states_data['personal_records']
        personal_records = await add_day_to_excel(message=message, personal_rate=personal_rate, **data)
        await edit_database(personal_records=personal_records)
        if 'previous_diary' in user_states_data:
            try:
                previous_diary = user_states_data['previous_diary']
                await bot.delete_message(message.chat.id, previous_diary)
                del user_states_data['previous_diary']
            except:
                pass
        sent_message = await download_diary(message, state)
        await edit_database(previous_diary=sent_message.message_id)
        await state.update_data(daily_chosen_tasks=[], one_time_chosen_tasks=[])
        await start(message, state)
    else:
        raise ValueError
    # except ValueError:
    #     await message.answer(f'"{message.text}" должен быть числом от 0 до 10')


@dp.message(lambda message: message.text.lower() == 'опрашиваемые данные', ClientState.settings)
async def collected_data(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    chosen_collected_data = user_data['chosen_collected_data']
    keyboard = keyboard_builder(inp=['Шаги', 'Сон'], add_dell=False, chosen=chosen_collected_data, grid=2,
                                add_sent=False)
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
    await edit_database(chosen_collected_data=chosen_collected_data)
    keyboard = keyboard_builder(inp=['Шаги', 'Сон'], chosen=chosen_collected_data,
                                add_dell=False, grid=2, add_sent=False)
    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'мои рекорды', ClientState.settings)
async def my_records(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if len(user_data):
        data = await state.get_data()
        personal_records = data['personal_records']
        output = [f'{key} : {value}' for key, value in personal_records.items()]
        await message.answer('Ваши рекорды:\n' + '\n'.join(output))
    else:
        await start(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'напоминания', ClientState.settings)
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
                fill_diary,
                trigger='cron',
                hour=hours,
                minute=minutes,
                args=(message, state)
                # Replace with user IDs and message
            )
            notifications_data.setdefault('job_id', job_id.id)
            await state.update_data(notifications_data=notifications_data)
        else:
            if 'job_id' in user_data['notifications_data']:
                job_id = user_data['notifications_data']['job_id']
                scheduler.remove_job(job_id=job_id)
                del user_data['notifications_data']['job_id']
                await state.update_data(notifications_data=notifications_data)
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
    hours = notification_time[0]
    minutes = notification_time[1]
    notifications_data['hours'] = hours
    notifications_data['minutes'] = minutes
    await edit_database(notifications_data=notifications_data)
    await state.update_data(notifications_data=notifications_data)
    if notifications_data.setdefault('chosen_notifications', []) == ['Включено']:
        if 'job_id' in notifications_data:
            job_id = notifications_data['job_id']
            scheduler.remove_job(job_id=job_id)
        job_id = scheduler.add_job(
            fill_diary,
            trigger='cron',
            hour=hours,
            minute=minutes,
            args=(message, state))
        await state.update_data(job_id=job_id.id)
        await message.answer(f'Отлично! Теперь напоминания будут приходить каждый день в {hours}:{minutes}')
        await start(message, state)
    else:
        if 'job_id' in notifications_data:
            job_id = notifications_data['job_id']
            scheduler.remove_job(job_id=job_id)


@dp.message(lambda message: message.text.lower() == 'дела в определенную дату', ClientState.settings)
async def date_jobs_keyboard(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()

    if len(user_data):
        # locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
        data = await state.get_data()
        if 'scheduler_arguments' in data:
            output = [key.split('Я напомню вам : ')[1].replace('"', '') for key in data['scheduler_arguments'].keys()]
            keyboard = keyboard_builder(inp=output, add_sent=False)
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
        await start(message, state)


@dp.callback_query(ClientState.date_jobs)
async def date_jobs_keyboard_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = call.data

    if data == 'Удалить':
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
            del scheduler_arguments[itr]

        if len(scheduler_arguments) == 0:
            del user_states_data['scheduler_arguments']
            await state.set_data(user_states_data)

        else:
            scheduler_arguments_inp = [key.split('Я напомню вам : ')[1].replace('"', '')
                                       for key in user_states_data['scheduler_arguments']]
            keyboard = keyboard_builder(inp=scheduler_arguments_inp, add_sent=False)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)

        await state.update_data(scheduler_arguments=scheduler_arguments, date_chosen_tasks=[])
        await edit_database(scheduler_arguments=scheduler_arguments)

    elif data == 'Добавить':
        await call.message.answer('Введите новое дело',)
        await state.update_data(call=call)
        await state.set_state(ClientState.date_jobs_1)

    else:
        data = int(data)
        user_states_data = await state.get_data()
        scheduler_arguments = [key.split('Я напомню вам : ')[1].replace('"', '')
                               for key in user_states_data['scheduler_arguments'].keys()]
        date_chosen_tasks = user_states_data['date_chosen_tasks']
        if scheduler_arguments[data] in date_chosen_tasks:
            date_chosen_tasks.remove(scheduler_arguments[data])
        else:
            date_chosen_tasks.append(scheduler_arguments[data])
        await state.update_data(date_chosen_tasks=date_chosen_tasks)
        keyboard = keyboard_builder(inp=scheduler_arguments, chosen=date_chosen_tasks, add_sent=False)
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
        keyboard = generate_keyboard(
            ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье'])
        await message.answer(
            'В какой день недели?', reply_markup=keyboard)
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


# @dp.message(ClientState.date_jobs_week)
# async def date_jobs_week(message: Message, state: FSMContext) -> None:
#     user_message = normalized(message.text)
#     user_states_data = await state.get_data()
#     new_date_jobs = user_states_data['new_date_jobs']
#     day_of_week = translate[user_message]
#     # now = datetime.datetime.now()
#     # hours = now.hour
#     # minutes = (now + timedelta(minutes=2)).minute
#     out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
#     await scheduler_list(message, state, out_message, user_states_data, trigger="cron",
#                          day_of_week=day_of_week,
#                          args=new_date_jobs)
#     if 'call' in user_states_data:
#         await rebuild_keyboard(state, 'date_chosen_tasks')


# @dp.message(ClientState.date_jobs_month)
# async def date_jobs_month(message: Message, state: FSMContext) -> None:
#     user_states_data = await state.get_data()
#     new_date_jobs = user_states_data['new_date_jobs']
#     day_of_month = message.text
#     out_message = f'Я напомню вам : "{new_date_jobs}" каждый {day_of_month} день месяца'
#     # now = datetime.datetime.now()
#     # hours = now.hour
#     # minutes = (now + timedelta(minutes=2)).minute
#     await scheduler_list(message, state, out_message, user_states_data, day=day_of_month, trigger="cron",
#                          args=new_date_jobs)
#     if 'call' in user_states_data:
#         await rebuild_keyboard(state, 'date_chosen_tasks')


# @dp.message(ClientState.date_jobs_year)
# async def date_jobs_year(message: Message, state: FSMContext) -> None:
#     user_states_data = await state.get_data()
#     new_date_jobs = user_states_data['new_date_jobs']
#     date = datetime.datetime.strptime(message.text, '%d-%m')
#     # now = datetime.datetime.now()
#     # hours = now.hour
#     # minutes = (now + timedelta(minutes=2)).minute
#     out_message = f'Я напомню вам : "{new_date_jobs}" каждое {date.day} {date.strftime("%B")}'
#     await scheduler_list(message, state, out_message, user_states_data, trigger="cron", day=date.day, month=date.month,
#                          args=new_date_jobs)
#     if 'call' in user_states_data:
#         await rebuild_keyboard(state, 'date_chosen_tasks')


# @dp.message(ClientState.date_jobs_once)
# async def date_jobs_once(message: Message, state: FSMContext) -> None:
#     user_states_data = await state.get_data()
#     new_date_jobs = user_states_data['new_date_jobs']
#
#     date = datetime.datetime.strptime(message.text, '%Y-%m-%d')
#     # Текущие часы и минуты
#     # now = datetime.datetime.now()
#     # current_time = now.time()
#
#     # # Объединение заданной даты с текущим временем
#     # date = datetime.datetime.combine(date, current_time)
#     #
#     # # Добавление 2 минут
#     # date += datetime.timedelta(minutes=2)
#
#     if datetime.datetime.now() < date:
#         out_message = f'Я напомню вам : "{new_date_jobs}" {date.day} {date.strftime("%B")} {date.year}'
#         await scheduler_list(message, state, out_message, user_states_data, trigger="date",
#                              run_date=date.strftime("%Y-%m-%d %H:%M"),
#                              args=new_date_jobs)
#         if 'call' in user_states_data:
#             await rebuild_keyboard(state, 'date_chosen_tasks')
#     else:
#         await message.answer(f'{message.text} меньше текущей даты')


@dp.message(lambda message: message.text.lower() == 'добавить разовые дела', ClientState.settings)
async def change_one_time_jobs(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'one_time_jobs' in user_data:
        await message.answer(
            'Введите ежедневные дела, которые вы хотели бы добавить через запятую',
            reply_markup=remove_markup)
    else:
        await message.answer('Введите новый список разовых дел через запятую',
                             reply_markup=generate_keyboard(['В Главное Меню']))
    await state.set_state(ClientState.one_time_jobs_2)


@dp.message(ClientState.one_time_jobs_2)
async def change_one_time_jobs_2(message: Message, state: FSMContext) -> None:
    to_add_one_time_jobs = normalized(message.text).split(', ')
    user_states_data = await state.get_data()
    for i in to_add_one_time_jobs:
        num = len(i) - 44
        if num > 0:
            await message.answer(
                f'"{i}" Должно быть короче на {num} cимвол\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
            return
    one_time_chosen_tasks = user_states_data['one_time_chosen_tasks']
    one_time_jobs = user_states_data['one_time_jobs'] + to_add_one_time_jobs
    if 'one_time_call' in user_states_data:
        call = user_states_data['one_time_call']
        keyboard = keyboard_builder(inp=one_time_jobs, chosen=one_time_chosen_tasks, grid=1)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard
        )
    else:
        await message.answer('Отлично! Ваш список разовых дел обновлен')
    await edit_database(one_time_jobs=one_time_jobs)
    await state.update_data(one_time_jobs=one_time_jobs)
    await start(message, state)


@dp.message(ClientState.change_daily_jobs_1)
async def change_daily_jobs_1(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'daily_tasks' in user_data:
        daily_tasks = user_data['daily_tasks']
    else:
        daily_tasks = []
    user_message = normalized(message.text)
    str_data = user_message.split(', ')
    for i in str_data:
        num = len(i) - 22
        if num > 0:
            await message.answer(f'"{i}" Должно быть короче на {num} cимвол\n Попробуйте использовать эмодзи 🎸🕺🍫')
            return
    for one_jobs in str_data:
        daily_tasks.append(one_jobs)
    if 'daily_chosen_tasks' in user_data:
        daily_chosen_tasks = user_data['daily_chosen_tasks']
        if 'call' in user_data:
            call = user_data['call']
            keyboard = keyboard_builder(inp=daily_tasks, chosen=daily_chosen_tasks, grid=2)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard)

            if len(daily_tasks) == 0:
                if 'messages_to_edit' in user_data:
                    messages_to_edit = user_data['messages_to_edit']
                    await bot.delete_message(message.chat.id, messages_to_edit['message'])
                    await bot.edit_message_text('Добавьте список дел', message.chat.id, messages_to_edit['keyboard'])
    await state.update_data(daily_tasks=daily_tasks)
    await edit_database(daily_tasks=daily_tasks)
    await message.answer('Отлично, ваш список ежедневных дел обновлен!')
    await start(message, state)




@dp.message(lambda message: message)
async def handle_message(message: Message, state: FSMContext):
    await start(message, state)


async def main():
    scheduler.start()
    await database_start()
    await dp.start_polling(bot)
    await scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
