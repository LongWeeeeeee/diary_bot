import asyncio
import datetime
import hashlib
import json
# import locale
import logging
import os
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys
from sqlite import database_start, create_profile, edit_database
from functions import generate_keyboard, diary_out, add_day_to_excel, normalized, day_to_prefix

bot = Bot(token=keys.Token)
dp = Dispatcher()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
already_started = False
# locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
remove_markup = types.ReplyKeyboardRemove()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
scheduler.start()
negative_responses = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}
translate = {'понедельник': 'mon', 'вторник': 'tue', 'среду': 'wed', 'четверг': 'thu', 'пятницу': 'fri',
             'субботу': 'sat',
             'воскресенье': 'sun'}


class ClientState(StatesGroup):
    greet = State()
    change_daily_jobs_1 = State()
    steps = State()
    total_sleep = State()
    deep_sleep = State()
    about_day = State()
    personal_rate = State()
    settings = State()
    download = State()
    one_time_jobs_2 = State()
    one_time_jobs_proceed = State()
    date_jobs = State()
    del_date_job = State()
    date_jobs_1 = State()
    date_jobs_2 = State()
    date_jobs_3 = State()
    date_jobs_week = State()
    date_jobs_year = State()
    date_jobs_once = State()
    date_jobs_month = State()


@dp.message(lambda message: message.text is not None and message.text.lower() in ['в главное меню', '/start'])
async def start(message: Message, state: FSMContext) -> None:
    answer = await create_profile(user_id=message.from_user.id)
    if answer is not None:
        data = {}
        daily_scores, one_time_jobs, scheduler_arguments, personal_records, previous_diary = json.loads(answer[1]), json.loads(answer[2]), json.loads(answer[3]), json.loads(answer[4]), answer[5]
        if len(daily_scores) != 0:
            data['daily_scores'] = daily_scores
        else:
            await handle_new_user(message, state)
            return
        if len(one_time_jobs) != 0:
            data['one_time_jobs'] = one_time_jobs
        if len(scheduler_arguments) != 0:
            data['scheduler_arguments'] = scheduler_arguments
        if len(personal_records) != 0:
            data['personal_records'] = personal_records
        if len(previous_diary) != 0:
            data['previous_diary'] = previous_diary
        await state.update_data(**data)
        path = str(message.from_user.id) + '_Diary.xlsx'
        if os.path.exists(path):
            keyboard = generate_keyboard(['Вывести Дневник', 'Настройки', 'Скачать Дневник'], first_button='Заполнить Дневник')
        else:
            keyboard = generate_keyboard(['Настройки', 'Заполнить Дневник'])
        await message.answer('Главное меню', reply_markup=keyboard)
        #загрузка данных в scheduler из scheduler_arguments from database
        await scheduler_in(data, state)
    else:
        await handle_new_user(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'настройки')
async def settings(message: Message, state: FSMContext = None) -> None:
    user_data = await state.get_data()
    if 'one_time_jobs' in user_data:
        if 'personal_records' in user_data:
            keyboard = generate_keyboard(['Дела в определенную дату', 'Мои рекорды'],
                                         last_button="В Главное Меню")
        else:
            keyboard = generate_keyboard(['Дела в определенную дату'],
                                         last_button="В Главное Меню")

    else:
        if 'personal_records' in user_data:
            keyboard = generate_keyboard(['Добавить Разовые Дела', 'Дела в определенную дату', 'Мои рекорды'],
                                         last_button="В Главное Меню")
        else:
            keyboard = generate_keyboard(['Добавить Разовые Дела', 'Дела в определенную дату'],
                                         last_button="В Главное Меню")


    await message.answer(text='Ваши Настройки', reply_markup=keyboard)
    await state.set_state(ClientState.settings)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'заполнить дневник')
async def fill_diary(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'daily_scores' in user_data:
        await existing_user(message, state)
    else:
        await handle_new_user(message, state)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'вывести дневник')
async def diary_output(message: Message, state: FSMContext) -> None:
    await diary_out(message)
    await state.set_state(ClientState.greet)


@dp.message(lambda message: message.text is not None and message.text.lower() == 'скачать дневник')
async def download_diary(message: Message):
    try:
        sent_message = await message.answer_document(
            document=FSInputFile(f'{message.from_user.id}_Diary.xlsx'),
            disable_content_type_detection=True,
        )
        return sent_message
    except FileNotFoundError:
        await message.answer('Сначала заполните данные')


async def scheduler_in(data, state):
    if 'scheduler_arguments' in data:
        # загрузка в scheduler заданий из database
        for key in list(data['scheduler_arguments'].keys()):
            values = data['scheduler_arguments'][key]
            values_copy = values.copy()
            values_copy['args'] = (state, key)
            if 'date' in values_copy:
                values_copy['date'] = datetime.datetime.strptime(values['date'], '%Y-%m-%d')
            elif 'run_date' in values_copy:
                values_copy['run_date'] = datetime.datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                current_date = datetime.datetime.now()
                if current_date > (values_copy['run_date'] + timedelta(minutes=1)):
                    del data['scheduler_arguments'][key]
                    continue
            unique_id = generate_unique_id_from_args(values_copy)
            if not any(job.id == unique_id for job in scheduler.get_jobs()):
                values_copy['id'] = unique_id
                scheduler.add_job(executing_scheduler_job, **values_copy)

        if len(data['scheduler_arguments']) == 0:
            del data['scheduler_arguments']
            await state.set_data(data)
            await edit_database(scheduler_arguments={})


@dp.message(lambda message: message.text is not None and message.text.lower() == 'мои рекорды', ClientState.settings)
async def my_records(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    personal_records = data['personal_records']
    output = [f'{key} : {value}' for key, value in personal_records.items()]
    await message.answer('Ваши рекорды:\n' + '\n'.join(output))

@dp.message(lambda message: message.text is not None and message.text.lower() == 'дела в определенную дату', ClientState.settings)
async def date_jobs_keyboard(message: Message, state: FSMContext) -> None:
    # locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    data = await state.get_data()
    if 'scheduler_arguments' in data:
        output = data['scheduler_arguments']
        date_builder = InlineKeyboardBuilder()
        for index, job in enumerate(output.keys()):
            job = job.split('Я напомню вам : ')[1].replace('"', '')
            date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        date_builder.adjust(1, 1)
        d_new_builder = InlineKeyboardBuilder()
        d_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        d_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        d_new_builder.adjust(2)
        date_builder.attach(d_new_builder)
        await message.answer('Ваши задачи', reply_markup=date_builder.as_markup())
        await message.answer(
            'Для удаления выберите интересующие вас дела и нажмите "Удалить"\n"Добавить" - если хотите добавить новую задачу', reply_markup=generate_keyboard(['В Главное Меню']))
        await state.set_state(ClientState.date_jobs)
    else:
        await message.answer('Введите запланированную задачу', reply_markup=generate_keyboard(['В Главное Меню']))
        await state.set_state(ClientState.date_jobs_1)


@dp.callback_query(ClientState.date_jobs)
async def date_jobs_keyboard_callback(call: types.CallbackQuery, state: FSMContext):
    data = call.data
    try:
        await call.answer()
        data = int(data)
        user_states_data = await state.get_data()
        scheduler_arguments = list(user_states_data['scheduler_arguments'].keys())
        chosen_tasks = user_states_data['chosen_tasks']
        if scheduler_arguments[data] in chosen_tasks:
            chosen_tasks.remove(scheduler_arguments[data])
        else:
            chosen_tasks.append(scheduler_arguments[data])
        await state.update_data(chosen_tasks=chosen_tasks)
        a_builder = InlineKeyboardBuilder()
        for index, job in enumerate(scheduler_arguments):
            if job in chosen_tasks:
                job = job.split('Я напомню вам : ')[1].replace('"', '')
                a_builder.button(text=f"{job} ✅️️", callback_data=f"{index}")
            else:
                job = job.split('Я напомню вам : ')[1].replace('"', '')
                a_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        a_builder.adjust(1, 1)
        a_new_builder = InlineKeyboardBuilder()
        a_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        a_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        a_new_builder.adjust(2)
        a_builder.attach(a_new_builder)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=a_builder.as_markup()
        )

    except:
        await call.answer()
        if data == 'Удалить':
            user_states_data = await state.get_data()
            chosen_tasks = user_states_data['chosen_tasks']
            scheduler_arguments = user_states_data['scheduler_arguments']
            for iter in chosen_tasks:
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
                del scheduler_arguments[iter]

            a_builder = InlineKeyboardBuilder()
            for index, job in enumerate(scheduler_arguments):
                job = job.split('Я напомню вам : ')[1].replace('"', '')
                a_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            a_builder.adjust(1, 1)
            a_new_builder = InlineKeyboardBuilder()
            if len(scheduler_arguments) == 0:
                del user_states_data['scheduler_arguments']
                await state.set_data(user_states_data)
            else:
                a_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
            a_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            a_new_builder.adjust(2)
            a_builder.attach(a_new_builder)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=a_builder.as_markup()
            )

            await call.answer()
            await state.update_data(scheduler_arguments=scheduler_arguments)
            await state.update_data(chosen_tasks=[])
            await edit_database(scheduler_arguments=scheduler_arguments)
        elif data == 'Добавить':
            await call.message.answer('Введите новое дело',)
            await state.update_data(call=call)
            await state.set_state(ClientState.date_jobs_1)


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


async def scheduler_list(message, state, out_message, user_states_data, **kwargs):
    # загрузка аргументов в database
    await message.answer(out_message)
    try:
        scheduler_arguments = user_states_data['scheduler_arguments']
        scheduler_arguments[out_message] = {**kwargs}
    except KeyError:
        scheduler_arguments = {out_message: {**kwargs}}
    await edit_database(scheduler_arguments=scheduler_arguments)
    await state.update_data(scheduler_arguments=scheduler_arguments)
    await start(message, state)


@dp.message(ClientState.date_jobs_week)
async def date_jobs_week(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    day_of_week = translate[user_message]
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron",
                         day_of_week=day_of_week,
                         args=new_date_jobs)
    if 'call' in user_states_data:
        await rebuild_keyboard(state)


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
    if 'call' in user_states_data:
        await rebuild_keyboard(state)


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
    if 'call' in user_states_data:
        await rebuild_keyboard(state)


@dp.message(ClientState.date_jobs_once)
async def date_jobs_once(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']

    date = datetime.datetime.strptime(message.text, '%Y-%m-%d')
    # Текущие часы и минуты
    # now = datetime.datetime.now()
    # current_time = now.time()

    # # Объединение заданной даты с текущим временем
    # date = datetime.datetime.combine(date, current_time)
    #
    # # Добавление 2 минут
    # date += datetime.timedelta(minutes=2)

    if datetime.datetime.now() < date:
        out_message = f'Я напомню вам : "{new_date_jobs}" {date.day} {date.strftime("%B")} {date.year}'
        await scheduler_list(message, state, out_message, user_states_data, trigger="date",
                             run_date=date.strftime("%Y-%m-%d %H:%M"),
                             args=new_date_jobs)
        if 'call' in user_states_data:
            await rebuild_keyboard(state)
    else:
        await message.answer(f'{message.text} меньше текущей даты')


async def rebuild_keyboard(state: FSMContext):
    user_states_data = await state.get_data()
    chosen_tasks = user_states_data['chosen_tasks']
    call = user_states_data['call']
    scheduler_arguments = user_states_data['scheduler_arguments']
    for iter in chosen_tasks:
        del scheduler_arguments[iter]

    a_builder = InlineKeyboardBuilder()
    for index, job in enumerate(scheduler_arguments):
        job = job.split('Я напомню вам : ')[1].replace('"', '')
        a_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
    a_builder.adjust(1, 1)
    a_new_builder = InlineKeyboardBuilder()
    if len(scheduler_arguments) == 0:
        del user_states_data['scheduler_arguments']
        await state.set_data(user_states_data)
    else:
        a_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
    a_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
    a_new_builder.adjust(2)
    a_builder.attach(a_new_builder)
    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=a_builder.as_markup()
    )

    await call.answer()


async def executing_scheduler_job(state, out_message):
    # функция которая срабатывает когда срабатывает scheduler
    user_states_data = await state.get_data()
    scheduler_arguments = user_states_data['scheduler_arguments']
    if scheduler_arguments[out_message]['trigger'] == 'date':
        del scheduler_arguments[out_message]
        await edit_database(scheduler_arguments=scheduler_arguments)
    # Я напомню вам : "тес" 14 января 2024
    job = normalized(out_message.split(' : ')[1]).replace('"', '')
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        one_time_jobs.append(job)
        await state.update_data(one_time_jobs=one_time_jobs)
        await edit_database(one_time_jobs=one_time_jobs)
    except KeyError:
        await state.update_data(one_time_jobs=job)
        await edit_database(one_time_jobs=job)

@dp.message(lambda message: message.text is not None and message.text.lower() == 'добавить разовые дела', ClientState.settings)
async def change_one_time_jobs(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'one_time_jobs' in user_data:
        await message.answer(
            'Введите ежедневные дела, которые вы хотели бы добавить через запятую',
            reply_markup=remove_markup)
    else:
        await message.answer('Введите новый список разовых дел через запятую', reply_markup=generate_keyboard(['В Главное Меню']))
    await state.set_state(ClientState.one_time_jobs_2)


@dp.message(ClientState.one_time_jobs_2)
async def change_one_time_jobs_2(message: Message, state: FSMContext) -> None:
    to_add_one_time_jobs = normalized(message.text).split(', ')
    user_states_data = await state.get_data()
    for i in to_add_one_time_jobs:
        num = len(i) - 44
        if num > 0:
            await message.answer(
                f'"{i}" Должно быть короче на {num} cимвола\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
            return
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        one_time_jobs += to_add_one_time_jobs
    except KeyError:
        one_time_jobs = to_add_one_time_jobs
    if 'one_time_call' in user_states_data:
        call = user_states_data['one_time_call']
        one_time_builder = InlineKeyboardBuilder()
        for index, job in enumerate(one_time_jobs):
            one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        one_time_builder.adjust(1, 1)
        new_ot_builder = InlineKeyboardBuilder()
        new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
        new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        new_ot_builder.adjust(2, 1)
        one_time_builder.attach(new_ot_builder)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=one_time_builder.as_markup()
        )
    else:
        await message.answer('Отлично! Ваш список разовых дел обновлен')
    await edit_database(one_time_jobs=one_time_jobs)
    await state.update_data(one_time_jobs=one_time_jobs)
    await start(message, state)




@dp.message(ClientState.change_daily_jobs_1)
async def change_daily_jobs_1(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if 'daily_scores' in user_data:
        daily_scores = user_data['daily_scores']
    else:
        daily_scores = []
    user_message = normalized(message.text)
    str_data = user_message.split(', ')
    for i in str_data:
        num = len(i) - 22
        if num > 0:
            await message.answer(f'"{i}" Должно быть короче на {num} cимвола\n Попробуйте использовать эмодзи 🎸🕺🍫')
            return
    for one_jobs in str_data:
        daily_scores.append(one_jobs)
    if 'call' in user_data:
        call = user_data['call']
        one_time_builder = InlineKeyboardBuilder()
        for index, job in enumerate(daily_scores):
            one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        one_time_builder.adjust(2, 2)
        new_ot_builder = InlineKeyboardBuilder()
        new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
        new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        new_ot_builder.adjust(2, 1)
        one_time_builder.attach(new_ot_builder)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=one_time_builder.as_markup()
        )
        if len(daily_scores) == 0:
            if 'messages_to_edit' in user_data:
                messages_to_edit = user_data['messages_to_edit']
                await bot.delete_message(message.chat.id, messages_to_edit['message'])
                await bot.edit_message_text('Добавьте список дел', message.chat.id, messages_to_edit['keyboard'])
    await state.update_data(daily_scores=daily_scores)
    await edit_database(daily_scores=daily_scores)
    await message.answer('Отлично, ваш список ежедневных дел обновлен!')
    await start(message, state)
    # await message.answer('Отлично, ваш список ежедневных дел обновлен!',
    #                      reply_markup=generate_keyboard(['Настройки', 'Заполнить Дневник']))


def keyboard_builder(input: list, grid=1):
    date_builder = InlineKeyboardBuilder()
    for index, job in enumerate(input):
        date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
    date_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()
    d_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
    d_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
    d_new_builder.adjust(2)
    date_builder.attach(d_new_builder)
    return date_builder.as_markup()


@dp.callback_query(ClientState.greet)
async def process_daily_jobs(call: types.CallbackQuery, state: FSMContext):
    data = call.data
    user_states_data = await state.get_data()
    try:
        chosen_tasks = user_states_data['chosen_tasks']
    except:
        chosen_tasks = []
    if data == 'Отправить':
        await call.answer()
        try:
            await state.update_data(activities=chosen_tasks)
            one_time_jobs = user_states_data['one_time_jobs']
            messages_to_edit = user_states_data['messages_to_edit']
            one_time_builder = InlineKeyboardBuilder()
            for index, job in enumerate(one_time_jobs):
                one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            one_time_builder.adjust(1, 1)
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
            new_ot_builder.adjust(2, 1)
            one_time_builder.attach(new_ot_builder)
            await call.message.answer('Отметьте разовые дела', reply_markup=one_time_builder.as_markup())
            usr_message = await call.message.answer('Если вы ничего не выполняли можете "Отправить" ничего не выбирая')
            messages_to_edit['message'] = usr_message.message_id
            await state.set_state(ClientState.one_time_jobs_proceed)
        except KeyError:
            await call.message.answer("Сколько сделал шагов?")
            await state.set_state(ClientState.steps)
    elif data == 'Удалить':
        await call.answer()
        daily_scores = user_states_data['daily_scores']
        for index in chosen_tasks:
            daily_scores.remove(index)
        if len(daily_scores) != 0:
            one_time_builder = InlineKeyboardBuilder()
            for index, job in enumerate(daily_scores):
                one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            one_time_builder.adjust(2, 2)
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
            new_ot_builder.adjust(2, 1)
            one_time_builder.attach(new_ot_builder)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=one_time_builder.as_markup()
            )
            await state.update_data(chosen_tasks=[])
            await state.update_data(daily_scores=daily_scores)
        else:
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            del user_states_data['daily_scores']
            await state.set_data(user_states_data)
            messages_to_edit = user_states_data['messages_to_edit']
            await bot.delete_message(call.message.chat.id, messages_to_edit['message'])
            await bot.edit_message_text('Добавьте список дел', call.message.chat.id, messages_to_edit['keyboard'])
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=new_ot_builder.as_markup()
            )
        await edit_database(daily_scores=daily_scores)

    elif data == 'Добавить':
        await call.answer()
        await call.message.answer('Введите ежедневные дела, которые вы хотели бы добавить через запятую')
        await state.update_data(call=call)
        await state.set_state(ClientState.change_daily_jobs_1)

    else:
        await call.answer()
        daily_scores = user_states_data['daily_scores']
        data = int(data)
        if daily_scores[data] in chosen_tasks:
            chosen_tasks.remove(daily_scores[data])
        else:
            chosen_tasks.append(daily_scores[data])
        await state.update_data(chosen_tasks=chosen_tasks)
        builder = InlineKeyboardBuilder()
        for index, job in enumerate(daily_scores):
            if job in chosen_tasks:
                builder.button(text=f"{job} ✅️️", callback_data=f"{index}")
            else:
                builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        builder.adjust(2, 2)
        new_builder = InlineKeyboardBuilder()
        new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        new_builder.adjust(2, 1)
        builder.attach(new_builder)

        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=builder.as_markup()
        )




@dp.callback_query(ClientState.one_time_jobs_proceed)
async def process_one_time(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data
    user_states_data = await state.get_data()
    try:
        chosen_tasks = user_states_data['chosen_tasks']
    except:
        chosen_tasks = []
    one_time_jobs = user_states_data['one_time_jobs']
    if data == 'Отправить':
        await call.answer()
        if len(chosen_tasks) != 0:
            await state.update_data(excel_chosen_tasks=chosen_tasks)
            user_states_data['excel_chosen_tasks']=chosen_tasks
            for iter in chosen_tasks:
                one_time_jobs.remove(iter)
            if len(one_time_jobs) == 0:
                messages_to_edit = user_states_data['messages_to_edit']
                await bot.delete_message(call.message.chat.id, messages_to_edit['message'])
                await bot.delete_message(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    )
                del user_states_data['one_time_jobs']
                await state.set_data(user_states_data)
            else:
                one_time_builder = InlineKeyboardBuilder()
                for index, job in enumerate(one_time_jobs):
                    one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
                one_time_builder.adjust(1, 1)
                new_ot_builder = InlineKeyboardBuilder()
                new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
                new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
                new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
                new_ot_builder.adjust(2, 1)
                one_time_builder.attach(new_ot_builder)
                await bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=one_time_builder.as_markup()
                )
                await state.update_data(one_time_jobs=one_time_jobs)
        await edit_database(one_time_jobs=one_time_jobs)
        user_states_data = await state.get_data()
        del user_states_data['messages_to_edit']
        await state.set_data(user_states_data)
        await call.message.answer("Сколько сделал шагов?")
        await state.set_state(ClientState.steps)
    elif data == 'Удалить':
        await call.answer()
        for index in chosen_tasks:
            one_time_jobs.remove(index)
        if len(one_time_jobs) != 0:
            one_time_builder = InlineKeyboardBuilder()
            for index, job in enumerate(one_time_jobs):
                one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            one_time_builder.adjust(1, 1)
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="❌Удалить❌", callback_data="Удалить")
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
            new_ot_builder.adjust(2, 1)
            one_time_builder.attach(new_ot_builder)
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=one_time_builder.as_markup()
            )
            await state.update_data(one_time_jobs=one_time_jobs)
        else:
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            del user_states_data['one_time_jobs']
            await state.set_data(user_states_data)
            messages_to_edit = user_states_data['messages_to_edit']
            await bot.delete_message(call.message.chat.id, messages_to_edit['message'])
            await bot.edit_message_text('Добавьте список дел', call.message.chat.id, messages_to_edit['keyboard'])
            await bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=new_ot_builder.as_markup()
            )
        await state.update_data(chosen_tasks=[])
        await edit_database(one_time_jobs=one_time_jobs)
    elif data == 'Добавить':
        await call.answer()
        await call.message.answer('Введите разовые дела, которые вы хотели бы добавить через запятую')
        await state.update_data(one_time_call=call)
        await state.set_state(ClientState.one_time_jobs_2)
    else:
        await call.answer()
        one_time_jobs = user_states_data['one_time_jobs']
        chosen_tasks = user_states_data['chosen_tasks']
        data = int(data)
        if one_time_jobs[data] in chosen_tasks:
            chosen_tasks.remove(one_time_jobs[data])
        else:
            chosen_tasks.append(one_time_jobs[data])
        a_builder = InlineKeyboardBuilder()
        for index, job in enumerate(one_time_jobs):
            if job in chosen_tasks:
                a_builder.button(text=f"{job} ✅️️", callback_data=f"{index}")
            else:
                a_builder.button(text=f"{job} ✔️", callback_data=f"{index}")

        a_builder.adjust(1, 1)
        a_new_builder = InlineKeyboardBuilder()
        a_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        a_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        a_new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        a_new_builder.adjust(2, 1)
        a_builder.attach(a_new_builder)

        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=a_builder.as_markup()
        )
        await state.update_data(chosen_tasks=chosen_tasks)


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
            await message.answer('Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
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


# async def start_scheduler(message, state):
#     global already_started
#     if already_started:
#         return
#     scheduler.add_job(start, 'cron', hour=8, minute=00, args=(message, state))
#     scheduler.start()
#     already_started = True


@dp.message(ClientState.personal_rate)
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    personal_rate = int(message.text)
    if 0 <= personal_rate <= 10:
        user_states_data = await state.get_data()

        data = {
            'daily_scores': user_states_data['daily_scores'],
            'date': datetime.datetime.now(),
            'activities': user_states_data['activities'],
            'user_message': user_states_data['user_message'],
            'sleep_quality': user_states_data['sleep_quality'],
            'my_steps': user_states_data['my_steps'],
        }
        if 'personal_records' in user_states_data:
            data['personal_records'] = user_states_data['personal_records']
        personal_records = await add_day_to_excel(message=message, personal_rate=personal_rate, **data)
        await edit_database(personal_records=personal_records)
        if 'previous_diary' in user_states_data:
            previous_diary = user_states_data['previous_diary']
            await bot.delete_message(message.chat.id, previous_diary)
            del user_states_data['previous_diary']
        sent_message = await download_diary(message)
        await edit_database(previous_diary=sent_message.message_id)
        await state.update_data(chosen_tasks=[])
        await start(message, state)
    else:
        raise ValueError
    # except ValueError:
    #     await message.answer(f'"{message.text}" должен быть числом от 0 до 10')


async def existing_user(message, state: FSMContext):
    user_data = await state.get_data()
    if 'daily_scores' in user_data:
        daily_scores = user_data['daily_scores']
        builder = InlineKeyboardBuilder()
        if 'chosen_tasks' in user_data:
            chosen_tasks = user_data['chosen_tasks']
            for index, job in enumerate(daily_scores):
                if job in chosen_tasks:
                    builder.button(text=f"{job} ✅️️", callback_data=f"{index}")
                else:
                    builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        else:
            for index, job in enumerate(daily_scores):
                builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        builder.adjust(2, 2)
        new_builder = InlineKeyboardBuilder()
        new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        new_builder.adjust(2, 1)
        builder.attach(new_builder)
        keyboard_message = await message.answer(
            'Отметьте вчерашние дела', reply_markup=builder.as_markup())
        # keyboard = generate_keyboard(['Вывести Дневник', 'Настройки', 'Заполнить Дневник'])
        # sent_message = await message.answer(
        #     'После этого нажмите кнопку "Отправить"', reply_markup=keyboard)
        sent_message = await message.answer(
            'После этого нажмите кнопку "Отправить"')
        messages_to_edit = {'keyboard': keyboard_message.message_id, 'message': sent_message.message_id}
        await state.update_data(messages_to_edit=messages_to_edit)

        await state.set_state(ClientState.greet)
    else:
        await handle_new_user(message, state)


def generate_unique_id_from_args(args_dict):
    # Сериализуем аргументы в строку в формате JSON
    copy_args_dict = args_dict.copy()
    copy_args_dict['args'] = args_dict['args'][1]
    serialized_args = json.dumps(copy_args_dict, sort_keys=True)
    # Используем хэш-функцию для генерации уникального идентификатора
    return hashlib.sha256(serialized_args.encode()).hexdigest()


async def handle_new_user(message: Message, state):
    info = await bot.get_me()
    await message.answer_sticker('CAACAgIAAxkBAAIsZGVY5wgzBq6lUUSgcSYTt99JnOBbAAIIAAPANk8Tb2wmC94am2kzBA')
    await message.answer(
        f'''Привет, {message.from_user.full_name}! \nДобро пожаловать в {info.username}!
Он поможет тебе вести отчет о твоих днях и делать выводы почему день был плохим или хорошим
Для начала нужно задать список дел. Какие у вас есть дела в течении дня? Например:''')
    await message.answer('встал в 6:30, лег в 11, зарядка утром, массаж, пп')
    await message.answer(
        'Вы можете воспользоваться предложенным списком или написать свой. Данные могут быть какие угодно',
        reply_markup=remove_markup)
    await state.set_state(ClientState.change_daily_jobs_1)


async def main():
    await database_start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
