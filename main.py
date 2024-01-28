import asyncio
import datetime
import json
import locale
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram import types, F
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys
from functions import generate_keyboard, diary_out, add_day_to_excel, normalized, day_to_prefix
from sqlite import database_start, create_profile, edit_database

bot = Bot(token=keys.Token)
dp = Dispatcher()

already_started = False
start = True
locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
remove_markup = types.ReplyKeyboardRemove()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
negative_responses = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}
translate = {'понедельник': 'mon', 'вторник': 'tue', 'среда': 'wed', 'четверг': 'thu', 'пятница': 'fri',
             'субботу': 'sat',
             'воскресенье': 'sun'}


class ClientState(StatesGroup):
    greet = State()
    new_daily_scores = State()
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


@dp.message(Command(commands=["start"]))
@dp.message(F.text == "Заполнить дневник")
async def start(message: Message, state: FSMContext) -> None:
    answer = await create_profile(user_id=message.from_user.id)
    if answer is not None:
        data = {}
        if answer[1] != '':
            daily_scores = json.loads(answer[1])
            data['daily_scores'] = daily_scores
        if answer[2] != '[]':
            one_time_jobs = json.loads(answer[2])
            data['one_time_jobs'] = one_time_jobs
        if answer[3] not in ['"{}"', {}, '{}']:
            scheduler_arguments = json.loads(answer[3])
            data['scheduler_arguments'] = scheduler_arguments
        data['chosen_tasks'] = []
        await state.update_data(**data)
        await existing_user(message, state)
    else:
        await handle_new_user(message, state)
    await start_scheduler(message, state)


@dp.message(F.text == 'Вывести дневник')
async def diary_output(message: Message, state: FSMContext) -> None:
    await diary_out(message)
    await state.set_state(ClientState.greet)


@dp.message(F.text == 'Настройки')
async def settings(message: Message, state: FSMContext) -> None:
    keyboard = generate_keyboard(['Ежедневные дела', 'Разовые дела', 'В определенную дату'],
                                 last_button="Заполнить дневник")
    await message.answer(text='Здесь вы можете изменить свои настройки', reply_markup=keyboard)
    await state.set_state(ClientState.settings)


@dp.message(F.text == 'В определенную дату', ClientState.settings)
async def date_jobs_keyboard(message: Message, state: FSMContext) -> None:
    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
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
        await message.answer('Для удаления выберите интересующие вас дела и нажмите "Удалить"\n"Добавить" - если хотите добавить новую задачу', reply_markup=remove_markup)
        await state.set_state(ClientState.date_jobs)
    else:
        await message.answer('Введите запланированную задачу', reply_markup=remove_markup)
        await state.set_state(ClientState.date_jobs_1)


@dp.callback_query(ClientState.date_jobs)
async def date_jobs_keyboard_callback(call: types.CallbackQuery, state: FSMContext):
    data = call.data
    try:
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

        await call.answer()
    except:
        if data == 'Удалить':
            user_states_data = await state.get_data()
            chosen_tasks = user_states_data['chosen_tasks']
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
                await state.update_data(scheduler_arguments=None)
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
            await call.message.answer('Введите новое дело', reply_markup=remove_markup)
            await state.update_data(call=call)
            await state.set_state(ClientState.date_jobs_1)


@dp.message(ClientState.date_jobs_1)
async def date_jobs_job(message: Message, state: FSMContext) -> None:
    await state.update_data(new_date_jobs=message.text)
    keyboard = generate_keyboard(['В день недели', 'Число месяца', 'Каждый год', 'Разово'])
    await message.answer('Выберите как и когда вы бы желали чтобы вам напомнили об этом деле', reply_markup=keyboard)
    await state.set_state(ClientState.date_jobs_2)


@dp.message(ClientState.date_jobs_2)
async def date_jobs_job_2(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    if user_message == 'в день недели':
        keyboard = generate_keyboard(
            ['понедельник', 'вторник', 'среду', 'четверг', 'пятница', 'субботу', 'воскресенье'])
        await message.answer(
            'В какой день недели?', reply_markup=keyboard)
        await state.set_state(ClientState.date_jobs_week)
    elif user_message == 'число месяца':
        await message.answer(
            'Какого числа месяца вам нужно напомнить об этом деле?', reply_markup=remove_markup)
        await state.set_state(ClientState.date_jobs_month)
    elif user_message == 'каждый год':
        await message.answer(
            'Введите дату когда вам о нем напомнить в формате день-месяц, например:', reply_markup=remove_markup)
        next_day = datetime.date.today()
        await message.answer(next_day.strftime("%d-%m"))
        await state.set_state(ClientState.date_jobs_year)
    elif user_message == 'разово':
        await message.answer(
            'Введите дату когда вам о нем напомнить в формате год-месяц-день, например:', reply_markup=remove_markup)
        await message.answer(str(datetime.date.today()))
        await state.set_state(ClientState.date_jobs_once)


async def scheduler_list(message, state, out_message, user_states_data, **kwargs):
    # загрузка аргументов в database
    await message.answer(out_message)
    await settings(message, state)
    try:
        scheduler_arguments = user_states_data['scheduler_arguments']
        scheduler_arguments[out_message] = {**kwargs}
    except KeyError:
        scheduler_arguments = {out_message: {**kwargs}}
    await edit_database(scheduler_arguments=scheduler_arguments)
    await state.update_data(scheduler_arguments=scheduler_arguments)


@dp.message(ClientState.date_jobs_week)
async def date_jobs_week(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    day_of_week = translate[user_message]
    # now = datetime.datetime.now()
    # hours = now.hour
    # minutes = (now + timedelta(minutes=2)).minute
    hours = 7
    minutes = 50
    out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron", hour=hours, minute=minutes,
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
    hours = 7
    minutes = 50
    await scheduler_list(message, state, out_message, user_states_data, day=day_of_month, hour=hours, minute=minutes,
                         trigger="cron", args=new_date_jobs)
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
    hours = 7
    minutes = 50
    out_message = f'Я напомню вам : "{new_date_jobs}" каждое {date.day} {date.strftime("%B")}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron", day=date.day, month=date.month,
                         hour=hours, minute=minutes,
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
    #
    # # Объединение заданной даты с текущим временем
    # date = datetime.datetime.combine(date, current_time)

    # Добавление 2 минут
    date += datetime.timedelta(hours=7, minutes=50)

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
        await state.update_data(scheduler_arguments=None)
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


@dp.message(F.text == 'Разовые дела', ClientState.settings)
async def change_one_time_jobs(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        await message.answer(
            'Введите новый список разовых дел через запятую. Ваш предыдущий список:',
            reply_markup=remove_markup)
        await message.answer(', '.join(one_time_jobs))
    except KeyError:
        await message.answer('Введите новый список разовых дел через запятую', reply_markup=remove_markup)
    await state.set_state(ClientState.one_time_jobs_2)


@dp.message(ClientState.one_time_jobs_2)
async def change_one_time_jobs_2(message: Message, state: FSMContext) -> None:
    one_time_jobs_str = normalized(message.text)
    for i in one_time_jobs_str.split(', '):
        num = len(i) - 44
        if num > 0:
            await message.answer(f'"{i}" Должно быть короче на {num} cимвола\nПопробуйте использовать эмодзи 🎸🕺🍫 или разбейте на 2')
            return
    await edit_database(one_time_jobs=one_time_jobs_str.split(', '))
    await message.answer(f'Отлично, теперь ваш список разовых дел выглядит так:')
    await message.answer(one_time_jobs_str)
    await state.update_data(one_time_jobs=one_time_jobs_str.split(', '))
    await settings(message, state)


@dp.message(F.text == 'Ежедневные дела', ClientState.settings)
async def daily_jobs(message: Message, state: FSMContext) -> None:
    await message.answer('Введите новый список ежедневных. Ваш предыдущий список: ',
                         reply_markup=remove_markup)
    user_states_data = await state.get_data()
    daily_scores = user_states_data['daily_scores']
    await message.answer(', '.join(daily_scores))
    await state.set_state(ClientState.new_daily_scores)


@dp.message(F.text == 'Скачать дневник')
async def download_diary(message: Message) -> None:
    try:
        await message.answer_document(
            document=FSInputFile(f'{message.from_user.id}_Diary.xlsx'),
            disable_content_type_detection=True,
        )
    except FileNotFoundError:
        await message.answer('Сначала заполните данные')


@dp.message(ClientState.new_daily_scores)
async def change_daily_jobs(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    str_data = user_message.split(', ')
    for i in str_data:
        num = len(i) - 22
        if num > 0:
            await message.answer(f'"{i}" Должно быть короче на {num} cимвола\n Попробуйте использовать эмодзи 🎸🕺🍫')
            return
    daily_scores = [one_jobs for one_jobs in str_data]
    await state.update_data(daily_scores=daily_scores)
    await edit_database(daily_scores=daily_scores)
    await message.answer('Отлично, ваш список ежедневных дел обновлен!')
    await state.set_state(ClientState.settings)
    await settings(message, state)


@dp.callback_query(ClientState.greet)
async def process_daily_jobs(call: types.CallbackQuery, state: FSMContext):
    data = call.data
    if data == 'Отправить':
        # await bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        user_states_data = await state.get_data()
        chosen_tasks = user_states_data['chosen_tasks']
        await state.update_data(activities=chosen_tasks)
        try:
            one_time_jobs = user_states_data['one_time_jobs']
            one_time_builder = InlineKeyboardBuilder()
            for index, job in enumerate(one_time_jobs):
                one_time_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
            one_time_builder.adjust(1, 1)
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
            one_time_builder.attach(new_ot_builder)
            await call.message.answer('Отметьте разовые дела', reply_markup=one_time_builder.as_markup())
            await state.update_data(chosen_tasks=[])
            await state.set_state(ClientState.one_time_jobs_proceed)
        except KeyError:
            await call.message.answer("Сколько сделал шагов?")
            await state.set_state(ClientState.steps)

    else:
        user_states_data = await state.get_data()
        daily_scores = user_states_data['daily_scores']
        chosen_tasks = user_states_data['chosen_tasks']
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
        new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        builder.attach(new_builder)

        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=builder.as_markup()
        )

        await call.answer()


@dp.callback_query(ClientState.one_time_jobs_proceed)
async def process_one_time(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data
    if data == 'Отправить':
        user_states_data = await state.get_data()
        one_time_jobs = user_states_data['one_time_jobs']
        chosen_tasks = user_states_data['chosen_tasks']
        # await bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        for iter in chosen_tasks:
            one_time_jobs.remove(iter)
        if len(one_time_jobs) == 0:
            await state.update_data(one_time_jobs=None)
        else:
            await state.update_data(one_time_jobs=one_time_jobs)
        await edit_database(one_time_jobs=one_time_jobs)

        await state.update_data(chosen_tasks=[])

        await call.message.answer("Сколько сделал шагов?")
        await state.set_state(ClientState.steps)
    else:
        user_states_data = await state.get_data()
        one_time_jobs = user_states_data['one_time_jobs']
        chosen_tasks = user_states_data['chosen_tasks']
        data = int(data)
        if one_time_jobs[data] in chosen_tasks:
            chosen_tasks.remove(one_time_jobs[data])
        else:
            chosen_tasks.append(one_time_jobs[data])
        await state.update_data(chosen_tasks=chosen_tasks)
    a_builder = InlineKeyboardBuilder()
    for index, job in enumerate(one_time_jobs):
        if job in chosen_tasks:
            a_builder.button(text=f"{job} ✅️️", callback_data=f"{index}")
        else:
            a_builder.button(text=f"{job} ✔️", callback_data=f"{index}")

    a_builder.adjust(1, 1)
    a_new_builder = InlineKeyboardBuilder()
    a_new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
    a_builder.attach(a_new_builder)

    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=a_builder.as_markup()
    )
    await call.answer()


@dp.message(ClientState.steps)
async def process_steps(message: Message, state: FSMContext) -> None:
    await state.update_data(mysteps=message.text)
    await message.answer('Сколько всего спал?')
    await state.set_state(ClientState.total_sleep)


@dp.message(ClientState.total_sleep)
async def process_total_sleep(message: Message, state: FSMContext) -> None:
    if message.text not in negative_responses:
        try:
            user_message = float(message.text.replace(',', '.'))
            await state.update_data(total_sleep=user_message)
            await message.answer('Сколько из них глубокий сон?')
            await state.set_state(ClientState.deep_sleep)
        except ValueError:
            await message.answer(f'"{message.text}" должно быть числом')
    else:
        await state.update_data(total_sleep=0.0)
        await state.update_data(deep_sleep=0.0)
        await message.answer(
            'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
        await state.set_state(ClientState.about_day)


@dp.message(ClientState.deep_sleep)
async def process_deep_sleep(message: Message, state: FSMContext) -> None:
    try:
        user_message = float(message.text.replace(',', '.'))
        await state.update_data(deep_sleep=user_message)
        await message.answer(
            'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
        await state.set_state(ClientState.about_day)
    except ValueError:
        await message.answer(f'"{message.text}" должно быть числом')


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


async def start_scheduler(message, state):
    global already_started
    if already_started:
        return
    scheduler.add_job(start, 'cron', hour=8, minute=00, args=(message, state))
    scheduler.start()
    already_started = True


@dp.message(ClientState.personal_rate)
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    personal_rate = int(message.text)
    if personal_rate <= 10 and personal_rate >= 0:
        user_states_data = await state.get_data()
        daily_scores = user_states_data['daily_scores']
        date = datetime.datetime.now()
        activities = user_states_data['activities']
        user_message = user_states_data['user_message']
        total_sleep = float(user_states_data['total_sleep'])
        deep_sleep = float(user_states_data['deep_sleep'])
        my_steps = int(user_states_data['mysteps'])
        user_id = message.from_user.id
        await add_day_to_excel(date, activities, total_sleep, deep_sleep, personal_rate, my_steps, user_id,
                               daily_scores,
                               user_message, message)
        await state.set_state(ClientState.greet)
    else:
        await message.answer(f'"{message.text}" должен быть числом от 0 до 10')


async def existing_user(message, state):
    user_data = await state.get_data()
    tasks = []
    if 'daily_scores' in user_data:
        daily_scores = user_data['daily_scores']
        builder = InlineKeyboardBuilder()
        for index, job in enumerate(daily_scores):
            # word = ''
            # for i in job.split(' '):
            #     if len(word) > 12:
            #         word += '/n'
            #         word += i
            #     else:
            #         word += i
            builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        builder.adjust(2, 2)
        new_builder = InlineKeyboardBuilder()
        new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        builder.attach(new_builder)
        await message.answer(
            'Отметьте вчерашние дела', reply_markup=builder.as_markup())
        keyboard = generate_keyboard(['Вывести дневник', 'Настройки', 'Заполнить дневник'])
        await message.answer(
            'После этого нажмите кнопку "Отправить"', reply_markup=keyboard)
    if 'scheduler_arguments' in user_data:
        # загрузка в scheduler заданий из database
        for key in list(user_data['scheduler_arguments'].keys()):
            values = user_data['scheduler_arguments'][key]
            values_copy = values.copy()
            values_copy['args'] = (state, key)

            if 'date' in values_copy:
                values_copy['date'] = datetime.datetime.strptime(values['date'], '%Y-%m-%d')
                scheduler.add_job(executing_scheduler_job, **values_copy)

            elif 'run_date' in values_copy:
                values_copy['run_date'] = datetime.datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                current_date = datetime.datetime.now()

                if current_date > (values_copy['run_date'] + timedelta(minutes=1)):
                    del user_data['scheduler_arguments'][key]
                else:
                    scheduler.add_job(executing_scheduler_job, **values_copy)
            else:
                scheduler.add_job(executing_scheduler_job, **values_copy)

        if len(user_data['scheduler_arguments']) == 0:
            del user_data['scheduler_arguments']
            await state.set_data(user_data)
            await edit_database(scheduler_arguments={})
    await state.set_state(ClientState.greet)


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
    await state.set_state(ClientState.new_daily_scores)


async def main():
    await database_start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
