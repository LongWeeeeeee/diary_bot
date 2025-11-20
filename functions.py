import re
import json
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys
from sqlite import (create_profile, edit_database, add_daily_log, get_last_logs, get_all_logs,
                    get_tasks_pool, get_one_time_tasks)
import pandas as pd
from aiogram import types
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
from typing import Optional
import ssl, certifi, aiohttp, asyncio

ssl_ctx = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger(__name__)
os.environ['TZ'] = 'Etc/UTC'


scheduler = AsyncIOScheduler()
scheduler.configure(timezone='Europe/Moscow')
TARGET_TZ = ZoneInfo("Europe/Moscow")

redis_storage = RedisStorage.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

class ClientState(StatesGroup):
    greet = State()
    start = State()
    personal_rate_1 = State()
    one_time_tasks_3=State()
    change_tasks_pool_1 = State()
    steps = State()
    total_sleep = State()
    deep_sleep = State()
    about_day = State()
    change_today_tasks = State()
    change_today_tasks_1 = State()
    add_tasks_pool = State()
    edit_tasks_pool = State()
    personal_rate = State()
    settings = State()
    download = State()
    one_time_tasks_2 = State()
    one_time_tasks_proceed = State()
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
    new_today_tasks = State()


bot = Bot(token=keys.Token)
dp = Dispatcher(storage=redis_storage)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
already_started = False
remove_markup = types.ReplyKeyboardRemove()
negative_responses = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}
translate = {'понедельник': 'mon', 'вторник': 'tue', 'среду': 'wed', 'четверг': 'thu', 'пятницу': 'fri',
             'субботу': 'sat',
             'воскресенье': 'sun'}


async def add_day_to_excel(date, activities: list, sleep_quality: int, personal_rate: float,
                           my_steps: int,
                           tasks_pool: list,
                           user_message: str, message, excel_chosen_tasks=None, personal_records=None, today=None):
    user_id = message.from_user.id
    path = f"{user_id}_Diary.xlsx"

    log_datetime = date if today else date - timedelta(days=1)
    log_date_iso = log_datetime.strftime("%Y-%m-%d")

    activities_text = ", ".join(activities)
    about_day_text = user_message
    if excel_chosen_tasks:
        about_day_text = f"Выполнил разовые дела: {', '.join(excel_chosen_tasks)}, {user_message}"

    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    steps_value = _to_float(my_steps)
    sleep_value = _to_float(sleep_quality)

    await add_daily_log(
        user_id=user_id,
        date=log_date_iso,
        activities=activities_text,
        steps=steps_value,
        sleep_quality=sleep_value,
        about_day=about_day_text,
        personal_rate=personal_rate
    )

    logs = await get_all_logs(user_id)

    def _write_excel():
        records = []
        for log_date, log_activities, log_steps, log_sleep, log_about, log_rate in logs:
            try:
                formatted_date = datetime.strptime(log_date, "%Y-%m-%d").strftime("%d.%m.%Y")
            except (TypeError, ValueError):
                formatted_date = log_date
            records.append({
                'Дата': formatted_date,
                'Дела за день': log_activities or '-',
                'Шаги': '-' if log_steps is None else log_steps,
                'Sleep quality': '-' if log_sleep is None else log_sleep,
                'О дне': log_about or '-',
                'My rate': '-' if log_rate is None else log_rate
            })
        df = pd.DataFrame(records, columns=['Дата', 'Дела за день', 'Шаги', 'Sleep quality', 'О дне', 'My rate'])
        with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Лист1')

            workbook = writer.book
            worksheet = writer.sheets['Лист1']
            cell_format = workbook.add_format({'text_wrap': True})
            cell_format_middle = workbook.add_format({
                'text_wrap': True,
                'align': 'center'
            })
            for row, size in zip(['B', 'E'], [60, 122]):
                worksheet.set_column(f'{row}:{row}', size, cell_format)
            for row in ['A', 'C', 'D', 'E']:
                worksheet.set_column(f'{row}:{row}', 10, cell_format_middle)

    try:
        await asyncio.to_thread(_write_excel)
    except Exception as e:
        logger.error(f"Error saving diary file {path}: {e}")
        await message.answer("Ошибка при сохранении файла дневника.")
        return None

    activity_history = [log[1] for log in logs if log[1]]
    answer = await counter_max_days(activity_history=activity_history, tasks_pool=tasks_pool, message=message,
                                    activities=activities, personal_records=personal_records)
    if answer is not None:
        personal_records = answer
        return personal_records


def counter_negative(column, current_word):
    count = 0
    for words in reversed(column):
        if not isinstance(words, str):
            count += 1
            continue
        try:
            split_words = words.split(', ')
            for word in split_words:
                if word == current_word:
                    return count
        except (AttributeError, TypeError):
            pass
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



def parse_time_key(key: str) -> int:
    """
    Преобразует строку вида "H", "HH", "H:MM" или "HH:MM" в число минут с начала суток.
    """
    if ':' in key:
        hours, minutes = map(int, key.split(':'))
    else:
        hours, minutes = int(key), 0
    return hours * 60 + minutes

def counter_positive(current_word, column):
    count = 0
    for words in reversed(column):
        if not isinstance(words, str):
            return count
        split_words = words.split(', ')
        if current_word in split_words:
            count += 1
        else:
            return count
    return count


async def scheduler_in(data, state, message):
    if 'scheduler_arguments' in data:
        # загрузка в scheduler заданий из database
        for key in list(data['scheduler_arguments'].keys()):
            values = data['scheduler_arguments'][key]
            await state.update_data(user_id=message.from_user.id,)
            values_copy = values.copy()
            values_copy['args'] = (state, key)
            if 'date' in values_copy:
                try:
                    values_copy['date'] = datetime.fromisoformat(values['date'])
                except Exception:
                    values_copy['date'] = datetime.strptime(values['date'], '%Y-%m-%d')
                # Ensure timezone is set for date field
                if values_copy['date'].tzinfo is None:
                    values_copy['date'] = values_copy['date'].replace(tzinfo=TARGET_TZ)
            elif 'run_date' in values_copy:
                try:
                    values_copy['run_date'] = datetime.fromisoformat(values['run_date'])
                except Exception:
                    values_copy['run_date'] = datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                if values_copy['run_date'].tzinfo is None:
                    values_copy['run_date'] = values_copy['run_date'].replace(tzinfo=TARGET_TZ)
                current_date = datetime.now(TARGET_TZ)
                if current_date > (values_copy['run_date'] + timedelta(minutes=1)):
                    del data['scheduler_arguments'][key]
                    continue
            unique_id = generate_unique_id_from_args(values_copy)
            if not any(job.id == unique_id for job in scheduler.get_jobs()):
                values_copy['id'] = unique_id
                if 'day_of_week' in values_copy and isinstance(values_copy['day_of_week'], list):
                    values_copy['day_of_week'] = values_copy['day_of_week'][0]
                scheduler.add_job(executing_scheduler_job, **values_copy)
                
                # Check if task should run today and execute immediately
                now = datetime.now(TARGET_TZ)
                should_execute_today = False
                
                # Check weekly tasks (day_of_week)
                if 'day_of_week' in values_copy:
                    today_dow = now.strftime('%a').lower()[:3]  # 'mon'..'sun'
                    if values_copy['day_of_week'] == today_dow:
                        should_execute_today = True
                
                # Check monthly tasks (day of month)
                elif 'day' in values_copy and 'month' not in values_copy:
                    if int(values_copy['day']) == now.day:
                        should_execute_today = True
                
                # Check yearly tasks (specific day and month)
                elif 'day' in values_copy and 'month' in values_copy:
                    if int(values_copy['day']) == now.day and int(values_copy['month']) == now.month:
                        should_execute_today = True
                
                # Check one-time tasks scheduled for today
                elif 'run_date' in values_copy:
                    run_date = values_copy['run_date']
                    if isinstance(run_date, datetime):
                        if run_date.date() == now.date():
                            should_execute_today = True
                
                elif 'date' in values_copy:
                    task_date = values_copy['date']
                    if isinstance(task_date, datetime):
                        if task_date.date() == now.date():
                            should_execute_today = True
                
                if should_execute_today:
                    await executing_scheduler_job(state, key)

        if len(data['scheduler_arguments']) == 0:
            del data['scheduler_arguments']
            await state.set_data(data)
            await edit_database(scheduler_arguments={}, user_id=message.from_user.id)


def keyboard_builder(tasks_list=None, tasks_dict=None, chosen=None, add_save=None, grid=1, price_tag=False, add_dell=False, checks=False, last_button=None, add_money=False):
    data_builder = InlineKeyboardBuilder()
    tasks_pool_builder = InlineKeyboardBuilder()
    if tasks_list is not None:
        for index, task in enumerate(tasks_list):
            if chosen is not None:
                if task in chosen:
                    data_builder.button(text=f"{task} ✅️", callback_data=f"{index}")
                else:
                    data_builder.button(text=f"{task} ✔️", callback_data=f"{index}")
            else:
                tasks_pool_builder.button(text=f"{task}", callback_data=f"{index}")
    if tasks_dict is not None:
        today_tasks = dict(sorted(
            tasks_dict.items(),
            key=lambda item: parse_time_key(item[0])
        ))
        for time, task in today_tasks.items():
            if checks:
                data_builder.button(text=f"{time} {task} ✔️", callback_data=f"{time}")
            elif not price_tag:
                if time in chosen:
                    data_builder.button(text=f"{time} {task} ✅️", callback_data=f"{time}")
                else:
                    data_builder.button(text=f"{time} {task} ✔️", callback_data=f"{time}")

        # else:
        #     product_name = job
        #     price = inp[job]
        #     if type(price) == dict:
        #         for date in price:
        #             if price[date]['used'] is False:
        #                 price = int(price[date]['price'])
        #                 data_builder.button(text=f"{price}💰 {product_name} ✔️", callback_data=f"{index}")
        #     else:
        #         if str(index) in chosen:
        #             data_builder.button(text=f"{int(price)}💰 {product_name} ✅️", callback_data=f"{index}")
        #         else:
        #             data_builder.button(text=f"{int(price)}💰 {product_name} ✔️", callback_data=f"{index}")
    data_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()
    if add_money:
        d_new_builder.button(text="Начислить 💰", callback_data="Начислить")
    if add_dell:
        if add_save:
            d_new_builder.button(text="💾Сохранить 💾", callback_data="Сохранить")
        d_new_builder.button(text="💼Добавить 💼", callback_data="Добавить")
        d_new_builder.button(text="❌Удалить❌", callback_data="Удалить")
    if last_button:
        callback = re.sub(r'[\U0001F000-\U0001FAFF\s]+', '', last_button)
        d_new_builder.button(text=last_button, callback_data=callback)
        if add_money or add_save:
            d_new_builder.adjust(1, 2, 1)
        else:
            d_new_builder.adjust(1, 2)
    tasks_pool_builder.adjust(1, 1)
    if chosen is not None:
        data_builder.attach(d_new_builder)
        # data_builder.attach(tasks_pool_builder)
        return_builder = data_builder
    else:
        return_builder = tasks_pool_builder.attach(d_new_builder)
    return return_builder.as_markup()




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
Для начала нужно задать список дел через запятую. Какие у вас есть дела в течении дня? Например:''')
    await message.answer('подьем, отбой, зарядка, массаж головы и ступ, подтягивания, завтрак, обед, ужин, прогулка, расстяжка')
    await message.answer(
        'Вы можете воспользоваться предложенным списком или написать свой. Данные могут быть какие угодно',
        reply_markup=remove_markup)
    await state.set_state(ClientState.add_tasks_pool)


@dp.message(lambda message: message.text and message.text.lower() == 'заполнить дневник')
async def tasks_pool_function(message, state: FSMContext):
    user_data = await state.get_data()
    profile_row = None

    async def ensure_profile():
        nonlocal profile_row
        if profile_row is None:
            profile_row = await create_profile(user_id=message.from_user.id)
        return profile_row

    tasks_pool = user_data.get('tasks_pool', [])
    if not tasks_pool:
        profile = await ensure_profile()
        if profile:
            tasks_pool_json = json.loads(profile[1])
            tasks_pool_db = await get_tasks_pool(str(message.from_user.id))
            tasks_pool = tasks_pool_db or list(set(tasks_pool_json))
            await state.update_data(tasks_pool=tasks_pool)
    if not tasks_pool:
        await message.answer('Ваш список дел пуст! Добавьте ваши общие дела через запятую.')
        await state.set_state(ClientState.add_tasks_pool)
        return
    sunrise = user_data.get('sunrise', None)
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    today_tasks = user_data.get('today_tasks', {})
    daily_tasks = user_data.get('daily_tasks', {})
    if not daily_tasks:
        profile = await ensure_profile()
        if profile:
            daily_tasks = json.loads(profile[8])
            await state.update_data(daily_tasks=daily_tasks)
    today_tasks_chosen = user_data.get('today_tasks_chosen', [])
    today_tasks_not_time_chosen = user_data.get('today_tasks_not_time_chosen', [])

    # всегда восстанавливаем расписание из daily_*, если оно отсутствует (или очищено после дневника)
    if not today_tasks:
        today_tasks = daily_tasks.copy()
    else:
        for time_key, task in daily_tasks.items():
            today_tasks.setdefault(time_key, task)

    daily_tasks_not_time = user_data.get('daily_tasks_not_time', [])
    if not daily_tasks_not_time:
        profile = await ensure_profile()
        if profile:
            daily_tasks_not_time = json.loads(profile[9])
            await state.update_data(daily_tasks_not_time=daily_tasks_not_time)
    if not today_tasks_not_time:
        today_tasks_not_time = daily_tasks_not_time.copy()
    else:
        for task in daily_tasks_not_time:
            if task not in today_tasks_not_time:
                today_tasks_not_time.append(task)

    need_sunset = 'закат ☀️' not in today_tasks.values()
    sunrise_outdated = sunrise != now.strftime("%Y-%m-%d")
    if need_sunset or sunrise_outdated:
        try:
            sunrise = await get_sunset_minus_30()
            if sunrise:
                today_tasks[sunrise.strftime("%H:%M")] = 'закат ☀️'
                sunrise_value = sunrise.strftime("%Y-%m-%d")
            else:
                logger.warning("Could not fetch sunset time, skipping sunset task")
                sunrise_value = sunrise
        except Exception as e:
            logger.error(f"Error getting sunset time: {e}")
            sunrise_value = sunrise
        await state.update_data(today_tasks=today_tasks, today_tasks_not_time=today_tasks_not_time, sunrise=sunrise_value)
    else:
        await state.update_data(today_tasks=today_tasks, today_tasks_not_time=today_tasks_not_time)
    # Build the keyboard with the scheduled tasks and the available pool
    keyboard = keyboard_builder(
        tasks_dict=today_tasks,
        tasks_list=today_tasks_not_time,
        grid=1,
        chosen=today_tasks_chosen+today_tasks_not_time_chosen,
        add_dell=True,
        last_button="🚀Отправить 🚀",
        add_save=True,
    )
    if today_tasks:
        await message.answer(
            'Отметьте выполненные дела. Нижний список - дела, которые можно добавить в расписание на сегодня.',
            reply_markup=keyboard
        )
    else:
        await message.answer(
            'Ваш список дел пуст! Добавьте их нажав на кнопку "Добавить',
            reply_markup=keyboard
        )
    await state.set_state(ClientState.greet)

async def scheduler_list(message_or_call, state, out_message, userdata, **kwargs):
    # определить корректный actor_id
    if isinstance(message_or_call, types.CallbackQuery):
        actor_id = message_or_call.from_user.id
    else:
        actor_id = message_or_call.from_user.id

    data = await state.get_data()
    scheduler_arguments = data.get('scheduler_arguments', {})
    scheduler_arguments[out_message] = kwargs

    # использовать actor_id, а не message_obj.from_user.id
    await edit_database(scheduler_arguments=scheduler_arguments, user_id=actor_id)
    await state.update_data(scheduler_arguments=scheduler_arguments, user_id=actor_id)

    # 2) Немедленно добавляем job в APScheduler (без ожидания рестарта)
    values_copy = scheduler_arguments[out_message].copy()
    values_copy['args'] = (state, out_message)

    # Normalize date/run_date inputs
    if 'date' in values_copy and isinstance(values_copy['date'], str):
        try:
            values_copy['date'] = datetime.fromisoformat(values_copy['date'])
        except Exception:
            try:
                values_copy['date'] = datetime.strptime(values_copy['date'], '%Y-%m-%d')
            except Exception:
                pass
        # Ensure timezone is set for date field
        if isinstance(values_copy['date'], datetime) and values_copy['date'].tzinfo is None:
            values_copy['date'] = values_copy['date'].replace(tzinfo=TARGET_TZ)
    if 'run_date' in values_copy:
        rd = values_copy['run_date']
        if isinstance(rd, str):
            try:
                values_copy['run_date'] = datetime.fromisoformat(rd)
            except Exception:
                try:
                    values_copy['run_date'] = datetime.strptime(rd, '%Y-%m-%d %H:%M')
                except Exception:
                    pass
        if isinstance(values_copy['run_date'], datetime) and values_copy['run_date'].tzinfo is None:
            values_copy['run_date'] = values_copy['run_date'].replace(tzinfo=TARGET_TZ)

    if 'day_of_week' in values_copy and isinstance(values_copy['day_of_week'], list):
        values_copy['day_of_week'] = values_copy['day_of_week'][0]

    unique_id = generate_unique_id_from_args(values_copy)
    if not any(job.id == unique_id for job in scheduler.get_jobs()):
        values_copy['id'] = unique_id
        scheduler.add_job(executing_scheduler_job, **values_copy)

    # 3) Check if task should run today and execute immediately
    now = datetime.now(TARGET_TZ)
    should_execute_today = False
    
    # Check weekly tasks (day_of_week)
    if 'day_of_week' in values_copy:
        today_dow = now.strftime('%a').lower()[:3]  # 'mon'..'sun'
        if values_copy['day_of_week'] == today_dow:
            should_execute_today = True
    
    # Check monthly tasks (day of month)
    elif 'day' in values_copy and 'month' not in values_copy:
        if int(values_copy['day']) == now.day:
            should_execute_today = True
    
    # Check yearly tasks (specific day and month)
    elif 'day' in values_copy and 'month' in values_copy:
        if int(values_copy['day']) == now.day and int(values_copy['month']) == now.month:
            should_execute_today = True
    
    # Check one-time tasks scheduled for today
    elif 'run_date' in values_copy:
        run_date = values_copy['run_date']
        if isinstance(run_date, datetime):
            if run_date.date() == now.date():
                should_execute_today = True
    
    elif 'date' in values_copy:
        task_date = values_copy['date']
        if isinstance(task_date, datetime):
            if task_date.date() == now.date():
                should_execute_today = True
    
    if should_execute_today:
        await executing_scheduler_job(state, out_message)



async def start(state, message) -> None:
    user_data = await state.get_data()
    data = user_data.copy()
    answer = await create_profile(user_id=message.from_user.id)
    if answer is not None:
        (user_id, tasks_pool_json, one_time_tasks_json, scheduler_arguments, personal_records,
            previous_diary, chosen_collected_data, notifications_data,
         daily_tasks, daily_tasks_not_time) = (json.loads(answer[0]), json.loads(answer[1]), json.loads(answer[2]), \
            json.loads(answer[3]), json.loads(answer[4]), answer[5], json.loads(answer[6]), json.loads(
            answer[7]), json.loads(answer[8]), json.loads(answer[9]))  # Note: today_tasks is not used from db, daily_tasks is the source of truth

        user_id_str = str(user_id)
        tasks_pool = await get_tasks_pool(user_id_str)
        if not tasks_pool:
            tasks_pool = list(set(tasks_pool_json))
        one_time_tasks = await get_one_time_tasks(user_id_str)
        if not one_time_tasks:
            one_time_tasks = one_time_tasks_json
        data['daily_tasks_not_time'] = daily_tasks_not_time
        data['tasks_pool'] = list(set(tasks_pool))
        data['daily_tasks'] = daily_tasks
        data['one_time_tasks'] = one_time_tasks
        data['scheduler_arguments'] = scheduler_arguments
        if personal_records:
            data['personal_records'] = personal_records
        data['previous_diary'] = previous_diary
        data['notifications_data'] = notifications_data
        if notifications_data.get('chosen_notifications') == ['Включено'] and not user_data.get('job_id'):
            hours = notifications_data['hours']
            minutes = notifications_data['minutes']
            job_id = scheduler.add_job(
                tasks_pool_function,
                trigger='cron',
                hour=hours,
                minute=minutes,
                args=(message, state))
            data['job_id'] = job_id.id

        await state.update_data(**data)

        path = f"{user_id}_Diary.xlsx"
        if os.path.exists(path):
            keyboard = generate_keyboard(
                ['Вывести Дневник', 'Настройки'],
                first_button='Заполнить Дневник')
        else:
            keyboard = generate_keyboard(['Заполнить Дневник'], last_button='Настройки')
        out_message = ''
        if personal_records:
            record_message = "\n".join(f'{k} : {v}' for k, v in personal_records.items())
            out_message += f'\n\nВаши рекорды:\n{record_message}'
            await message.answer(out_message, reply_markup=keyboard)
        else:
            await message.answer('Главное меню', reply_markup=keyboard)

        await scheduler_in(data, state, message=message)
    else:
        await handle_new_user(message, state)


async def executing_scheduler_job(state, out_message):
    # функция, которая срабатывает, когда срабатывает scheduler
    
    # Безопасно получаем название задачи из сообщения
    try:
        text_normalized = normalized(out_message.split(' : ')[1]).replace('"', '').replace(' - ', '-')
    except (IndexError, AttributeError):
        print(f"Error parsing job text from: {out_message}")
        return

    tmp = text_normalized.split('-')
    
    # Получаем данные как можно позже, чтобы уменьшить вероятность гонки
    user_states_data = await state.get_data()
    user_id = user_states_data.get('user_id', None)

    if len(tmp) == 2:
        job, job_timing = text_normalized.split('-')[0], text_normalized.split('-')[1].split(' ')[0]
        # 1. Безопасно получаем список one_time_tasks из состояния
        # Если его нет, создаем пустой список
        today_tasks = user_states_data.get('today_tasks', {})

        # 2. Добавляем новую задачу в список, если её там ещё нет или она отличается
        if today_tasks.get(job_timing) != job:
            today_tasks[job_timing] = job
            # 3. Обновляем состояние
            await state.update_data(today_tasks=today_tasks)
    else:
        today_tasks_not_time = user_states_data.get('today_tasks_not_time', [])
        # Avoid duplicates - only add if not already present
        if text_normalized not in today_tasks_not_time:
            today_tasks_not_time.append(text_normalized)
            await state.update_data(today_tasks_not_time=today_tasks_not_time)
    
    # 4. Если это было разовое напоминание (trigger='date'), удаляем его из scheduler_arguments
    scheduler_arguments = user_states_data.get('scheduler_arguments', {})
    if out_message in scheduler_arguments and scheduler_arguments[out_message].get('trigger') == 'date':
        del scheduler_arguments[out_message]
        await state.update_data(scheduler_arguments=scheduler_arguments)
        if user_id:
            await edit_database(scheduler_arguments=scheduler_arguments, user_id=user_id)

    print(f"Successfully added scheduled task '{text_normalized}' to daily_tasks for user {user_id}")



async def counter_max_days(activity_history, tasks_pool, message, activities, personal_records, output=''):
    column = activity_history
    if column:
        negative_dict = {current_word: counter_negative(current_word=current_word, column=column) for current_word in
                         tasks_pool}
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
            # for name, value in negative_dict.items():
            #     if value:
            #         tasks_pool[name] = int(tasks_pool[name])*1.03
            if output != '':
                output += '\n\n'
            output += f'Вы не делали эти дела уже столько дней:\n{negative_output}\n\n' \
                      f'Может стоит дать им еще один шанс?'
        if output:
            send_message = await message.answer(output)
            await message.bot.pin_chat_message(message.chat.id, send_message.message_id)
            return personal_records
    else:
        await message.answer('Поздравляю! дневник заполнен')



def generate_keyboard(buttons: list, last_button=None, first_button=None):
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
    return re.sub(r',(?=\S)', ', ', text).strip().lower().replace('ё', 'е')


async def diary_out(message):
    logs = await get_last_logs(message.from_user.id)
    if not logs:
        await message.answer("Дневник еще не создан. Сначала заполните его!")
        return

    await message.answer(
        "{} | {} | {} | {} | {} | {} ".format("Дата", "Дела за день", "Шаги", "Sleep quality", "О дне", "My rate"))

    for log_date, activities, steps, sleep_quality, about_day, personal_rate in reversed(logs):
        try:
            formatted_date = datetime.strptime(log_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            formatted_date = log_date

        steps_value = '-' if steps is None else steps
        sleep_value = '-' if sleep_quality is None else sleep_quality
        personal_rate_value = '-' if personal_rate is None else personal_rate

        message_sheet = "{} | {} | {} | {} | {} | {}".format(
            formatted_date,
            activities or '-',
            steps_value,
            sleep_value,
            about_day or '-',
            personal_rate_value
        )

        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)




LAT = 55.72545
LNG = 52.41122
MSK = ZoneInfo("Europe/Moscow")
PRIMARY_API = "https://api.sunrise-sunset.org/json"
FALLBACK_API = "https://api.sunrisesunset.io/json"

# Try to use dateutil.parser if available for robust parsing
try:
    from dateutil import parser as _dateutil_parser
    _HAS_DATEUTIL = True
except Exception:
    _HAS_DATEUTIL = False

async def _fetch_with_retries(session: aiohttp.ClientSession, url: str, params: dict, tries: int = 3, backoff: float = 0.5):
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.get(url, params=params, timeout=timeout, ssl=ssl_ctx) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            last_exc = e
            logger.debug("Fetch attempt %d failed for %s: %s", attempt, url, e)
            if attempt < tries:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
    raise last_exc


def _try_parse_sunset_string(s: str, assume_msk_when_naive: bool = True, date_for_time: Optional[str] = None) -> Optional[datetime]:
    """
    Try to parse various sunset string formats into timezone-aware datetime.
    - If date_for_time provided and s is time-only, combine them.
    - If dateutil available use it; else try common formats.
    - If result is naive, assign MSK if assume_msk_when_naive else UTC.
    """
    if not s:
        return None
    s = s.strip()

    # 1) dateutil (best)
    if _HAS_DATEUTIL:
        try:
            dt = _dateutil_parser.parse(s)
            if dt.tzinfo is None:
                # naive: assume MSK (local) or UTC depending on caller
                tz = MSK if assume_msk_when_naive else ZoneInfo("UTC")
                dt = dt.replace(tzinfo=tz)
            return dt
        except Exception:
            pass

    # 2) try ISO-ish with Z or offset
    try:
        if s.endswith("Z"):
            s2 = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s2)
        if "T" in s:
            # maybe ISO without offset -> treat as naive
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=MSK if assume_msk_when_naive else ZoneInfo("UTC"))
                return dt
            except Exception:
                pass
    except Exception:
        pass

    # 3) try time-only formats -> combine with date_for_time (or today MSK)
    time_formats = ("%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M")
    for fmt in time_formats:
        try:
            t = datetime.strptime(s, fmt).time()
            if date_for_time is None:
                date_str = datetime.now(MSK).date().isoformat()
            else:
                date_str = date_for_time
            dt = datetime.fromisoformat(f"{date_str}T{t.isoformat()}")
            return dt.replace(tzinfo=MSK)
        except Exception:
            continue

    # could not parse
    return None


async def get_sunset_minus_30(
    lat: float = LAT,
    lng: float = LNG,
    date: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> datetime:
    """
    Async: Return timezone-aware datetime (Europe/Moscow) of sunset - 30 minutes.
    Raises RuntimeError only if all attempts fail.
    """
    own_session = False
    if session is None:
        session = aiohttp.ClientSession()
        own_session = True

    try:
        # 1) PRIMARY API (sunrise-sunset.org) - formatted=0 likely gives ISO in UTC or with offset
        try:
            params = {"lat": lat, "lng": lng, "formatted": 0}
            if date:
                params["date"] = date
            data = await _fetch_with_retries(session, PRIMARY_API, params)
            # data example: {"results": {"sunset": "2025-09-16T14:17:36+00:00"}, "status":"OK"}
            if data and data.get("status") == "OK":
                sunset_raw = data["results"].get("sunset")
                # primary often returns UTC ISO: treat naive ISO as UTC for primary
                dt = _try_parse_sunset_string(sunset_raw, assume_msk_when_naive=False, date_for_time=date)
                if dt:
                    dt = dt.astimezone(MSK)
                    return dt - timedelta(minutes=30)
        except Exception as e:
            logger.debug("Primary API failed: %s", e)

        # 2) FALLBACK API (sunrisesunset.io) - format may be time-only and likely local
        try:
            params = {"lat": lat, "lng": lng}
            if date:
                params["date"] = date
            data2 = await _fetch_with_retries(session, FALLBACK_API, params)
            if data2 and data2.get("status") == "OK":
                sunset_raw = None
                # try typical locations for the field
                if "results" in data2:
                    # sometimes nested
                    res = data2["results"]
                    sunset_raw = res.get("sunset") or res.get("sunset_time") or res.get("sunsetLocal")
                else:
                    sunset_raw = data2.get("sunset")
                dt = _try_parse_sunset_string(sunset_raw, assume_msk_when_naive=True, date_for_time=date)
                if dt:
                    # ensure MSK tz
                    dt = dt.astimezone(MSK)
                    return dt - timedelta(minutes=30)
        except Exception as e:
            logger.debug("Fallback API failed: %s", e)

        # 3) if reached here -> nothing parsed
        raise RuntimeError("Could not fetch/parse sunset time from available APIs.")
    finally:
        if own_session:
            await session.close()


# Safe wrapper that returns Optional[datetime] instead of raising
async def get_sunset_minus_30_safe(*args, **kwargs) -> Optional[datetime]:
    try:
        return await get_sunset_minus_30(*args, **kwargs)
    except Exception as e:
        logger.warning("get_sunset_minus_30_safe: failed to obtain sunset time: %s", e)
        return None


