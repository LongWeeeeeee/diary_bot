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
from sqlite import create_profile
import pandas as pd
from aiogram import types
import hashlib
from datetime import timedelta, datetime
from sqlite import edit_database
import pytz

os.environ['TZ'] = 'Etc/UTC'


scheduler = AsyncIOScheduler()
scheduler.configure(timezone='Europe/Moscow')
TARGET_TZ = pytz.timezone('Europe/Moscow')
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
    market = State()
    new_market_product = State()
    new_market_product_2 = State()
    backpack = State()


bot = Bot(token=keys.Token)
dp = Dispatcher()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
already_started = False
remove_markup = types.ReplyKeyboardRemove()
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
    except:
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
    answer = await counter_max_days(data=data, daily_scores=daily_tasks, message=message,
                                              activities=activities, personal_records=personal_records)
    if answer is not None:
        personal_records, daily_scores = answer
        return personal_records, daily_scores


def counter_negative(column, current_word, count=0):
    for words in column.iloc[::-1]:
        try:
            split_words = words.split(', ')
            for word in split_words:
                if word == current_word:
                    return count
        except: pass
        count += 1
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


async def scheduler_in(data, state):
    if 'scheduler_arguments' in data:
        # загрузка в scheduler заданий из database
        for key in list(data['scheduler_arguments'].keys()):
            values = data['scheduler_arguments'][key]
            values_copy = values.copy()
            values_copy['args'] = (state, key)
            if 'date' in values_copy:
                values_copy['date'] = datetime.strptime(values['date'], '%Y-%m-%d')
            elif 'run_date' in values_copy:
                values_copy['run_date'] = datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                current_date = datetime.now()
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


def keyboard_builder(inp, chosen, grid=1, price_tag=True, add_dell=True, checks=False, last_button="🚀Отправить 🚀", add_money=False):

    date_builder = InlineKeyboardBuilder()
    for index, job in enumerate(inp):
        if checks:
            date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        elif price_tag == False:
            if job in chosen:
                date_builder.button(text=f"{job} ✅️", callback_data=f"{index}")
            else:
                date_builder.button(text=f"{job} ✔️", callback_data=f"{index}")
        else:
            product_name = job
            price = inp[job]
            if type(price) == dict:
                for date in price:
                    if price[date]['used'] is False:
                        price = int(price[date]['price'])
                        date_builder.button(text=f"{price}💰 {product_name} ✔️", callback_data=f"{index}")
            else:
                if str(index) in chosen:
                    date_builder.button(text=f"{int(price)}💰 {product_name} ✅️", callback_data=f"{index}")
                else:
                    date_builder.button(text=f"{int(price)}💰 {product_name} ✔️", callback_data=f"{index}")

    date_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()
    if add_money:
        d_new_builder.button(text="Начислить 💰", callback_data="Начислить")
    if add_dell:
        d_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        if inp:
            d_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
            d_new_builder.adjust(1, 2)
    if last_button:
        callback = re.sub(r'[\U0001F000-\U0001FAFF\s]+', '', last_button)
        d_new_builder.button(text=last_button, callback_data=callback)
        if add_money:
            d_new_builder.adjust(1, 2, 1)
        else:
            d_new_builder.adjust(2, 1)
    date_builder.attach(d_new_builder)
    return date_builder.as_markup()




def generate_unique_id_from_args(args_dict):
    copy_args_dict = args_dict.copy()
    # Extract the second element from the 'args' tuple.
    copy_args_dict['args'] = args_dict['args'][1]

    # Define a converter for non-serializable types
    def datetime_converter(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    serialized_args = json.dumps(copy_args_dict, sort_keys=True, default=datetime_converter)
    # Generate a unique hash
    return hashlib.sha256(serialized_args.encode()).hexdigest()


async def handle_new_user(message: Message, state: FSMContext):
    info = await bot.get_me()
    await message.answer_sticker('CAACAgIAAxkBAAIsZGVY5wgzBq6lUUSgcSYTt99JnOBbAAIIAAPANk8Tb2wmC94am2kzBA')
    await message.answer(
        f'''Привет, {message.from_user.full_name}! \nДобро пожаловать в {info.username}!
Он поможет тебе вести отчет о твоих днях и делать выводы почему день был плохим или хорошим
Для начала нужно задать список дел и их стоимость. Какие у вас есть дела в течении дня? Например:''')
    await message.answer('встал в 6:30 : 5, лег в 11 : 5, зарядка утром : 10, массаж : 3, пп : 20')
    await message.answer(
        'Вы можете воспользоваться предложенным списком или написать свой. Данные могут быть какие угодно',
        reply_markup=remove_markup)
    await state.set_state(ClientState.change_daily_jobs_1)


@dp.message(lambda message: message.text and message.text.lower() == 'заполнить дневник')
async def daily_jobs(message, state: FSMContext):
    user_data = await state.get_data()
    daily_tasks = user_data.get('daily_tasks', [])
    daily_chosen_tasks = user_data['daily_chosen_tasks']

    # --- ИЗМЕНЕНИЕ НАЧАЛО ---
    # Инициализируем/сбрасываем список задач, за которые уже начислили в этой сессии

    if daily_tasks:
        keyboard = keyboard_builder(inp=daily_tasks, grid=2, chosen=daily_chosen_tasks, add_dell=True, add_money=True)
        await message.answer(
            'Отметьте вчерашние дела после этого нажмите кнопку "Отправить"', reply_markup=keyboard)
    else:
        keyboard = keyboard_builder(inp=daily_tasks, grid=2, chosen=daily_chosen_tasks, add_dell=True)
        await message.answer(
            'Добавьте ежедневные дела', reply_markup=keyboard)
    await state.set_state(ClientState.greet)


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



async def start(state, message=None, daily_tasks=None) -> None:
    data = {}
    user_data = await state.get_data()
    answer = await create_profile(user_id=message.from_user.id)
    if answer is not None:
        user_id, daily_tasks, one_time_jobs, scheduler_arguments, personal_records, \
            previous_diary, chosen_collected_data, notifications_data, balance, market = answer[0], json.loads(
            answer[1]), json.loads(answer[2]), \
            json.loads(answer[3]), json.loads(answer[4]), answer[5], json.loads(answer[6]), json.loads(
            answer[7]), json.loads(answer[8]), json.loads(answer[9])
        data['daily_tasks'] = daily_tasks
        data['one_time_jobs'] = one_time_jobs
        data['scheduler_arguments'] = scheduler_arguments
        data['balance'] = balance if balance else {'gold': 0, 'rank': 0}
        gold, rank = data['balance']['gold'], data['balance']['rank']
        if market:
            data['market'] = market
        else:
            data['market'] = {'purchase_history': {}, 'store': {}}
        if personal_records:
            data['personal_records'] = personal_records
        if 'job_id' in user_data:
            data['job_id'] = user_data['job_id']
        data['previous_diary'] = previous_diary
        data['message'] = message
        data['notifications_data'] = notifications_data
        data['session_accrued_tasks'] = user_data.get('session_accrued_tasks', [])
        if notifications_data.get('chosen_notifications') == ['Включено'] and not user_data.get('job_id'):
            hours = notifications_data['hours']
            minutes = notifications_data['minutes']
            job_id = scheduler.add_job(
                daily_jobs,
                trigger='cron',
                hour=hours,
                minute=minutes,
                args=(message, state))
            data['job_id'] = job_id.id

        data['chosen_collected_data'] = chosen_collected_data
        data['daily_chosen_tasks'] = user_data.get('daily_chosen_tasks', [])
        data.update({
            'one_time_chosen_tasks': [],
            'excel_chosen_tasks': [],
            'date_chosen_tasks': [],
            'date_jobs_week_chosen_tasks': [],
            'chosen_store': [],
            'backpack_chosen': [],
        })
        await state.update_data(**data)

        path = f"{user_id}_Diary.xlsx"
        if os.path.exists(path):
            keyboard = generate_keyboard(
            ['Вывести Дневник', 'Настройки', 'Потратить Золото'],
            first_button='Заполнить Дневник')
        else:
            keyboard = generate_keyboard(['Заполнить Дневник'], last_button='Настройки')

        out_message = f'Ваш баланс: {gold}💰'
        if personal_records:
            record_message = "\n".join(f'{k} : {v}' for k, v in personal_records.items())
            out_message += f'\n\nВаши рекорды:\n{record_message}'

        await message.answer(out_message, reply_markup=keyboard)
        # Меняем состояние, чтобы избежать повторного входа
        # загрузка данных в scheduler из scheduler_arguments from database
        await scheduler_in(data, state)
    elif answer is None or not daily_tasks:
        await handle_new_user(message, state)


async def executing_scheduler_job(state, out_message):
    # функция, которая срабатывает, когда срабатывает scheduler
    user_states_data = await state.get_data()
    scheduler_arguments = user_states_data['scheduler_arguments']
    if scheduler_arguments[out_message]['trigger'] == 'date':
        del scheduler_arguments[out_message]
        await edit_database(scheduler_arguments=scheduler_arguments)
    # Я напомню вам : "тес" 14 января 2024
    job = normalized(out_message.split(': ')[1]).replace('"', '')
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        one_time_jobs[job] = 300
        await state.update_data(one_time_jobs=one_time_jobs)
        await edit_database(one_time_jobs=one_time_jobs)
    except KeyError:
        await state.update_data(one_time_jobs=job)
        await edit_database(one_time_jobs=job)


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
            for name, value in negative_dict.items():
                if value:
                    daily_scores[name] = int(daily_scores[name])*1.03
            if output != '':
                output += '\n\n'
            output += f'Вы не делали эти дела уже столько дней:\n{negative_output}\n\n' \
                      f'Может стоит дать им еще один шанс?'
        if output:
            send_message = await message.answer(output)
            await message.bot.pin_chat_message(message.chat.id, send_message.message_id)
            return personal_records, daily_scores
    else:
        await message.answer('Поздравляю! дневник заполнен')


def generate_keyboard(buttons: list, last_button=None, first_button=None, chosen=None):
    #✅️✔️

    if last_button is not None:
        kb = [[types.KeyboardButton(text=f"{button}") for button in buttons], [types.KeyboardButton(text=last_button)]]
    elif first_button is not None:
        kb = [[types.KeyboardButton(text=first_button)], [types.KeyboardButton(text=f"{button}") for button in buttons]]
    else:
        kb = [[types.KeyboardButton(text=f"{button}") for button in buttons]]
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
        "{} | {} | {} | {} | {} | {} ".format("Дата", "Дела за день", "Шаги", "Sleep quality", "О дне", "My rate"))

    # Получение последних 7 строк данных
    last_entries = data.tail(7)

    # Перебор и отправка последних 7 строк
    for index, row in last_entries.iterrows():
        message_sheet = "{} | {} | {} | {} | {}".format(row["Дата"], row["Дела за день"], row["Шаги"],
                            row["Sleep quality"], row['О дне'], row['My rate'])

        # Разделение длинного сообщения на части
        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)