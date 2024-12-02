import re
import json
import logging
import os


from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys
from sqlite import create_profile, edit_database
import pandas as pd
from aiogram import types
import hashlib
import datetime
from datetime import timedelta


scheduler = AsyncIOScheduler()
scheduler.configure(timezone='Europe/Moscow')
class ClientState(StatesGroup):
    greet = State()
    start = State()
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
    collected_data = State()
    notification_proceed = State()
    notification_proceed_1 = State()
    notification_set_date = State()


bot = Bot(token=keys.Token)
dp = Dispatcher()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
already_started = False
# locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
remove_markup = types.ReplyKeyboardRemove()
# scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
# scheduler.start()
negative_responses = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}
translate = {'понедельник': 'mon', 'вторник': 'tue', 'среду': 'wed', 'четверг': 'thu', 'пятницу': 'fri',
             'субботу': 'sat',
             'воскресенье': 'sun'}


async def add_day_to_excel(date, activities: list, sleep_quality: int, personal_rate: float,
                           my_steps: int,
                           daily_tasks: list,
                           user_message: str, message, excel_chosen_tasks=None, personal_records=None):
    path = str(message.from_user.id) + '_Diary.xlsx'
    try:
        data = pd.read_excel(path)
    except FileNotFoundError:
        data = pd.DataFrame(columns=['Дата', 'Дела за день', 'Шаги', 'Sleep quality', 'О дне', 'My rate'])

    last_row = data.index.max() + 1
    yesterday = date - timedelta(days=1)
    data.loc[last_row, 'Дата'] = yesterday.strftime("%d.%m.%Y")
    data.loc[last_row, 'Дела за день'] = ", ".join(activities)
    data.loc[last_row, 'Шаги'] = my_steps
    data.loc[last_row, 'Sleep quality'] = sleep_quality
    if excel_chosen_tasks:
        user_message = f"Выполнил разовые дела: {', '.join(excel_chosen_tasks)}, {user_message}"
    data.loc[last_row, 'О дне'] = user_message
    data.loc[last_row, 'My rate'] = personal_rate
    writer = pd.ExcelWriter(path, engine='xlsxwriter')
    data.to_excel(writer, index=False, sheet_name='Лист1')

    workbook = writer.book
    worksheet = writer.sheets['Лист1']
    cell_format = workbook.add_format({'text_wrap': True})
    cell_format_middle = workbook.add_format({
        'text_wrap': True,
        'align': 'center'
    })
    for row, size in zip(['B', 'E'], [60, 122]):
        worksheet.set_column(f'{row}:{row}', size, cell_format)  # Установить ширину столбца A равной 20
    for row in ['A', 'C', 'D', 'E']:
        # Устанавливаем ширину столбцов
        worksheet.set_column(f'{row}:{row}', 10, cell_format_middle)  # Установить ширину столбца A равной 20

    writer._save()
    if not len(activities):
        await message.answer('Поздравляю! дневник заполнен')
    else:
        personal_records = await counter_max_days(data=data, daily_scores=daily_tasks, message=message,
                                                  activities=activities, personal_records=personal_records)
        return personal_records


def counter_negative(column, current_word, count=0):
    for words in column.iloc[::-1]:
        if not isinstance(words, float):
            split_words = words.split(', ')
            for word in split_words:
                if word == current_word:
                    return count
            count += 1
        else: 
            return count
    return count


def day_to_prefix(day: str) -> str:
    day_to_prefix_dict = {
        'воскресенье': 'каждое',
        'субботу': 'каждую',
        'пятницу': 'каждую',
        'четверг': 'каждый',
        'среду': 'каждую',
        'вторник': 'каждый',
        'понедельник': 'каждый'
    }
    return day_to_prefix_dict[day]


def counter_positive(current_word, column, count=0):
    for words in column.iloc[::-1]:
        if not isinstance(words, float):
            split_words = words.split(', ')
            if current_word in split_words:
                count += 1
            else:
                return count
        else:
            return count
    return count


# async def scheduler_in(data, state):
#     if 'scheduler_arguments' in data:
#         # загрузка в scheduler заданий из database
#         for key in list(data['scheduler_arguments'].keys()):
#             values = data['scheduler_arguments'][key]
#             values_copy = values.copy()
#             values_copy['args'] = (state, key)
#             if 'date' in values_copy:
#                 values_copy['date'] = datetime.datetime.strptime(values['date'], '%Y-%m-%d')
#             elif 'run_date' in values_copy:
#                 values_copy['run_date'] = datetime.datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
#                 current_date = datetime.datetime.now()
#                 if current_date > (values_copy['run_date'] + timedelta(minutes=1)):
#                     del data['scheduler_arguments'][key]
#                     continue
#             unique_id = generate_unique_id_from_args(values_copy)
#             if not any(job.id == unique_id for job in scheduler.get_jobs()):
#                 values_copy['id'] = unique_id
#                 scheduler.add_job(executing_scheduler_job, **values_copy)
#
#         if len(data['scheduler_arguments']) == 0:
#             del data['scheduler_arguments']
#             await state.set_data(data)
#             await edit_database(scheduler_arguments={})


def keyboard_builder(inp: list, grid=1, chosen=None, add_dell=True, add_sent=True):
    date_builder = InlineKeyboardBuilder()
    for index, job in enumerate(inp):
        if chosen is not None:
            if job in chosen:
                date_builder.button(text=f"{job} ✅️", callback_data=f"{index}")
            else:
                date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        else:
            date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
    date_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()
    if add_dell:
        d_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
        d_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        d_new_builder.adjust(2)
    if add_sent:
        d_new_builder.button(text="🚀Отправить 🚀", callback_data="Отправить")
        d_new_builder.adjust(2, 1)
    date_builder.attach(d_new_builder)
    return date_builder.as_markup()


# async def start_scheduler(message, state):
#     global already_started
#     if already_started:
#         return
#     scheduler.add_job(start, 'cron', hour=8, minute=00, args=(message, state))
#     scheduler.start()
#     already_started = True


def generate_unique_id_from_args(args_dict):
    # Сериалзуем аргументы в строку в формате JSON
    copy_args_dict = args_dict.copy()
    copy_args_dict['args'] = args_dict['args'][1]
    serialized_args = json.dumps(copy_args_dict, sort_keys=True)
    # Используем хэш-функцию для генерации уникального идентификатора
    return hashlib.sha256(serialized_args.encode()).hexdigest()


async def handle_new_user(message: Message, state: FSMContext):
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


async def daily_jobs(message, state: FSMContext):
    user_data = await state.get_data()
    if 'daily_tasks' in user_data:
        daily_tasks = user_data['daily_tasks']
        daily_chosen_tasks = user_data['daily_chosen_tasks']
        keyboard = keyboard_builder(inp=daily_tasks, grid=2, chosen=daily_chosen_tasks)
        await message.answer(
            'Отметьте вчерашние дела после этого нажмите кнопку "Отправить"', reply_markup=keyboard)
        await state.set_state(ClientState.greet)
    else:
        await handle_new_user(message, state)


# async def rebuild_keyboard(state: FSMContext, tasks_type):
#     user_states_data = await state.get_data()
#     chosen_tasks = user_states_data[tasks_type]
#     call = user_states_data['call']
#     scheduler_arguments = user_states_data['scheduler_arguments']
#     for itr in chosen_tasks:
#         del scheduler_arguments[itr]
#     if len(scheduler_arguments) == 0:
#         del user_states_data['scheduler_arguments']
#         await state.set_data(user_states_data)
#     else:
#         scheduler_arguments_inp = [key.split('Я напомню вам : ')[1].replace('"', '') for key in
#                                    user_states_data['scheduler_arguments']]
#         keyboard = keyboard_builder(inp=scheduler_arguments_inp, add_sent=False)
#         await bot.edit_message_reply_markup(
#             chat_id=call.message.chat.id,
#             message_id=call.message.message_id,
#             reply_markup=keyboard
#         )


# async def scheduler_list(message, state, out_message, user_states_data, **kwargs):
#     # загрузка аргументов в database
#     await message.answer(out_message)
#     try:
#         scheduler_arguments = user_states_data['scheduler_arguments']
#         scheduler_arguments[out_message] = {**kwargs}
#     except KeyError:
#         scheduler_arguments = {out_message: {**kwargs}}
#     await edit_database(scheduler_arguments=scheduler_arguments)
#     await state.update_data(scheduler_arguments=scheduler_arguments)
#     await start(message, state)
async def fill_diary(message: Message, state: FSMContext) -> None:
    user_data = await state.get_data()
    if user_data:
        if 'daily_tasks' in user_data:
            await daily_jobs(message, state)
        else:
            await handle_new_user(message, state)
    else:
        await start(message, state)

async def start(message: Message, state: FSMContext, flag=True) -> None:
    data = {}
    await state.set_state(ClientState.start)
    answer = await create_profile(user_id=message.from_user.id)
    if answer is not None:

        daily_tasks, one_time_jobs, scheduler_arguments, personal_records,\
            previous_diary, chosen_collected_data, notifications_data = json.loads(answer[1]), json.loads(answer[2]),\
            json.loads(answer[3]), json.loads(answer[4]), answer[5], json.loads(answer[6]), json.loads(answer[7])
        data['daily_tasks'] = daily_tasks
        data['one_time_jobs'] = one_time_jobs
        data['scheduler_arguments'] = scheduler_arguments
        if personal_records is not None and len(personal_records) != 0:
            data['personal_records'] = personal_records
        data['previous_diary'] = previous_diary
        data['notifications_data'] = notifications_data
        if notifications_data.get('chosen_notifications', []):
            hours = notifications_data['hours']
            minutes = notifications_data['minutes']
            if notifications_data.setdefault('chosen_notifications', []) == ['Включено']:
                job_id = scheduler.add_job(
                    fill_diary,
                    trigger='cron',
                    hour=hours,
                    minute=minutes,
                    args=(message, state))
                data['notifications_data']['job_id'] = job_id.id
        data['chosen_collected_data'] = chosen_collected_data
        user_data = await state.get_data()
        if 'daily_chosen_tasks' not in user_data:
            await state.update_data(daily_chosen_tasks=[], one_time_chosen_tasks=[], excel_chosen_tasks=[])
        await state.update_data(**data)
        if not daily_tasks:
            await handle_new_user(message, state)
            return
        path = str(message.from_user.id) + '_Diary.xlsx'
        if os.path.exists(path):
            keyboard = generate_keyboard(['Вывести Дневник', 'Настройки', 'Скачать Дневник'],
                                         first_button='Заполнить Дневник')
        else:
            keyboard = generate_keyboard(['Настройки', 'Заполнить Дневник'])
        await message.answer('Главное меню', reply_markup=keyboard)
        # загрузка данных в scheduler из scheduler_arguments from database
        # await scheduler_in(data, state)
    else:
        await handle_new_user(message, state)


# async def executing_scheduler_job(state, out_message):
#     # функция, которая срабатывает, когда срабатывает scheduler
#     user_states_data = await state.get_data()
#     scheduler_arguments = user_states_data['scheduler_arguments']
#     if scheduler_arguments[out_message]['trigger'] == 'date':
#         del scheduler_arguments[out_message]
#         await edit_database(scheduler_arguments=scheduler_arguments)
#     # Я напомню вам : "тес" 14 января 2024
#     job = normalized(out_message.split(': ')[1]).replace('"', '')
#     try:
#         one_time_jobs = user_states_data['one_time_jobs']
#         one_time_jobs.append(job)
#         await state.update_data(one_time_jobs=one_time_jobs)
#         await edit_database(one_time_jobs=one_time_jobs)
#     except KeyError:
#         await state.update_data(one_time_jobs=job)
#         await edit_database(one_time_jobs=job)


async def counter_max_days(data, daily_scores, message, activities, personal_records, output=''):
    column = data['Дела за день']
    if column.any():
        negative_dict = {current_word: counter_negative(current_word=current_word, column=column) for current_word in
                         daily_scores}
        positive_dict = {current_word: counter_positive(current_word=current_word, column=column) for current_word in
                         activities}
        negative_output = '\n'.join(
            ['{} : {}'.format(key, value) for key, value in negative_dict.items() if value not in [0, 1]])
        positive_output = []
        if personal_records is None:
            personal_records = {}
        for key, value in positive_dict.items():
            if key in personal_records:
                if personal_records[key] < value:
                    personal_records[key] = value
            else:
                personal_records[key] = value
            if value not in [0, 1]:
                positive_output.append(f'{key} : {value}')
        positive_output = '\n'.join(positive_output)
        if positive_output:
            output += f'Поздравляю! Вы соблюдаете эти дела уже столько дней:\n{positive_output}'
        if negative_output:
            if output != '':
                output += '\n\n'
            output += f'Вы не делали эти дела уже столько дней:\n{negative_output}\n\n' \
                      f'Может стоит дать им еще один шанс?'
        if output:
            sent_message = await message.answer(output)
            await message.bot.pin_chat_message(message.chat.id, sent_message.message_id)
            return personal_records
    else:
        await message.answer('Поздравляю! дневник заполнен')


def generate_keyboard(buttons: list, last_button=None, first_button=None):
    if last_button is not None:
        kb = [[types.KeyboardButton(text=button) for button in buttons], [types.KeyboardButton(text=last_button)]]
    elif first_button is not None:
        kb = [[types.KeyboardButton(text=first_button)], [types.KeyboardButton(text=button) for button in buttons]]
    else:
        kb = [[types.KeyboardButton(text=button) for button in buttons]]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
    )
    return keyboard


def normalized(text):
    return re.sub(r',(?=\S)', ', ', text).lower().replace('ё', 'е')


async def diary_out(message):
    # Чтение данных из файла Excel
    data = pd.read_excel(f'{message.from_user.id}_Diary.xlsx')

    # Отправка заголовка таблицы
    await message.answer(
        "{} | {} | {} | {} | {} | {} | {} | {}".format("Дата", "Дела за день", "Шаги", "Total sleep", "Deep sleep",
                                                       "О дне", "My rate", "Total"))

    # Получение последних 7 строк данных
    last_entries = data.tail(7)

    # Перебор и отправка последних 7 строк
    for index, row in last_entries.iterrows():
        message_sheet = "{} | {} | {} | {} | {} | {} | {}".format(row["Дата"], row["Дела за день"], row["Шаги"],
                            row["Total sleep"], row['Deep sleep'], row['О дне'], row['My rate'])

        # Разделение длинного сообщения на части
        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)
