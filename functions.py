"""Вспомогательные функции бота-дневника."""
import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import aiohttp
import certifi
import pandas as pd
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    bot, scheduler, ClientState, TARGET_TZ, LAT, LNG,
    NEGATIVE_RESPONSES, WEEKDAY_TRANSLATE, remove_markup,
    should_task_run_today
)
from sqlite import (
    create_profile, edit_database, add_daily_log, get_last_logs, get_all_logs,
    get_tasks_pool, get_one_time_tasks, get_user_data, get_full_user_state,
    parse_profile, ParsedProfile
)

ssl_ctx = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger(__name__)


def _is_scheduled_task(task_name: str) -> bool:
    """Проверяет, является ли задача scheduled (добавленной из планировщика).
    
    Scheduled задачи содержат слова "каждый/каждую/каждое" с днём недели.
    Например: "брусья каждый вторник" или "подтягивания | брусья каждый четверг"
    """
    if not task_name:
        return False
    task_lower = task_name.lower()
    # Проверяем наличие паттерна "каждый/каждую/каждое + день недели"
    weekdays = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье']
    for prefix in ['каждый ', 'каждую ', 'каждое ']:
        if prefix in task_lower:
            # Проверяем что после "каждый" идёт день недели
            for day in weekdays:
                if f'{prefix}{day}' in task_lower:
                    return True
    return False




async def add_day_to_excel(
    date: datetime,
    activities: List[str],
    sleep_quality: Union[int, float, str],
    personal_rate: Union[int, float],
    my_steps: Union[int, float, str],
    tasks_pool: List[str],
    user_message: str,
    message: Message,
    excel_chosen_tasks: Optional[List[str]] = None,
    personal_records: Optional[Dict[str, Any]] = None,
    today: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
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


# day_to_prefix удалён - используется из config.py



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
    scheduler_arguments = data.get('scheduler_arguments', {})
    if not scheduler_arguments:
        return
    
    # Кэшируем существующие job IDs для быстрого поиска
    existing_job_ids = {job.id for job in scheduler.get_jobs()}
    current_date = datetime.now(TARGET_TZ)
    expired_keys = []
    
    # Один вызов update_data в начале
    await state.update_data(user_id=message.from_user.id)
    
    for key, values in scheduler_arguments.items():
        values_copy = values.copy()
        values_copy['args'] = (state, key)
        
        # Обработка дат
        if 'date' in values_copy:
            try:
                dt_val = datetime.fromisoformat(values['date']) if isinstance(values['date'], str) else values['date']
            except Exception:
                dt_val = datetime.strptime(values['date'], '%Y-%m-%d')
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=TARGET_TZ)
            values_copy['date'] = dt_val
        elif 'run_date' in values_copy:
            try:
                dt_val = datetime.fromisoformat(values['run_date']) if isinstance(values['run_date'], str) else values['run_date']
            except Exception:
                dt_val = datetime.strptime(values['run_date'], '%Y-%m-%d %H:%M')
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=TARGET_TZ)
            values_copy['run_date'] = dt_val
            
            if current_date > (dt_val + timedelta(minutes=1)):
                expired_keys.append(key)
                continue
        
        unique_id = generate_unique_id_from_args(values_copy)
        if unique_id not in existing_job_ids:
            values_copy['id'] = unique_id
            if 'day_of_week' in values_copy and isinstance(values_copy['day_of_week'], list):
                values_copy['day_of_week'] = values_copy['day_of_week'][0]
            scheduler.add_job(executing_scheduler_job, **values_copy)
    
    # Удаляем просроченные задачи
    if expired_keys:
        for key in expired_keys:
            del scheduler_arguments[key]
        await state.update_data(scheduler_arguments=scheduler_arguments)
        await edit_database(scheduler_arguments=scheduler_arguments, user_id=message.from_user.id)


def keyboard_builder(
    tasks_list: Optional[List[str]] = None,
    tasks_dict: Optional[Dict[str, str]] = None,
    chosen: Optional[List[str]] = None,
    add_save: Optional[bool] = None,
    grid: int = 1,
    price_tag: bool = False,
    add_dell: bool = False,
    checks: bool = False,
    last_button: Optional[str] = None,
    add_money: bool = False
) -> types.InlineKeyboardMarkup:
    data_builder = InlineKeyboardBuilder()
    tasks_pool_builder = InlineKeyboardBuilder()
    chosen_set = set(chosen) if chosen else set()
    
    if tasks_list is not None:
        for index, task in enumerate(tasks_list):
            if chosen is not None:
                mark = "\u2705\ufe0f" if task in chosen_set else "\u2714\ufe0f"
                data_builder.button(text=f"{task} {mark}", callback_data=str(index))
            else:
                tasks_pool_builder.button(text=task, callback_data=str(index))
    
    if tasks_dict is not None:
        sorted_tasks = sorted(tasks_dict.items(), key=lambda item: parse_time_key(item[0]))
        for time, task in sorted_tasks:
            if checks:
                data_builder.button(text=f"{time} {task} \u2714\ufe0f", callback_data=time)
            elif not price_tag:
                mark = "\u2705\ufe0f" if time in chosen_set else "\u2714\ufe0f"
                data_builder.button(text=f"{time} {task} {mark}", callback_data=time)

    data_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()
    
    if add_money:
        d_new_builder.button(text="\U0001F4B0 Начислить", callback_data="Начислить")
    if add_dell:
        if add_save:
            d_new_builder.button(text="\U0001F4BE Сохранить \U0001F4BE", callback_data="Сохранить")
        d_new_builder.button(text="\U0001F4BC Добавить \U0001F4BC", callback_data="Добавить")
        d_new_builder.button(text="\u274c Удалить \u274c", callback_data="Удалить")
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
        return data_builder.as_markup()
    else:
        tasks_pool_builder.attach(d_new_builder)
        return tasks_pool_builder.as_markup()



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


async def handle_new_user(message: Message, state: FSMContext) -> None:
    info = await bot.get_me()
    try:
        await message.answer_sticker('CAACAgIAAxkBAAIsZGVY5wgzBq6lUUSgcSYTt99JnOBbAAIIAAPANk8Tb2wmC94am2kzBA')
    except Exception as e:
        logger.warning(f"Could not send sticker: {e}")
    await message.answer(
        f'''Привет, {message.from_user.full_name}! \nДобро пожаловать в {info.username}!
Он поможет тебе вести отчет о твоих днях и делать выводы почему день был плохим или хорошим
Для начала нужно задать список дел через запятую. Какие у вас есть дела в течении дня? Например:''')
    await message.answer('подьем, отбой, зарядка, массаж головы и ступ, подтягивания, завтрак, обед, ужин, прогулка, расстяжка')
    await message.answer(
        'Вы можете воспользоваться предложенным списком или написать свой. Данные могут быть какие угодно',
        reply_markup=remove_markup)
    await state.set_state(ClientState.add_tasks_pool)


async def tasks_pool_function(message, state: FSMContext):
    """Показывает расписание на сегодня для заполнения дневника."""
    user_data = await state.get_data()
    user_id_str = str(message.from_user.id)

    # Собираем все данные для одного update_data в конце
    state_updates = {}
    
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    today_str = now.strftime("%Y-%m-%d")
    
    # Проверяем, изменился ли день с последнего открытия расписания
    last_tasks_date = user_data.get('today_tasks_date', None)
    is_new_day = last_tasks_date != today_str
    
    # Получаем данные из state или БД
    tasks_pool = user_data.get('tasks_pool', [])
    one_time_tasks = user_data.get('one_time_tasks', [])
    daily_tasks = user_data.get('daily_tasks', {})
    daily_tasks_not_time = user_data.get('daily_tasks_not_time', [])
    scheduler_arguments = user_data.get('scheduler_arguments', {})
    
    # Загружаем из БД только если данных нет в state
    need_db_load = not tasks_pool or is_new_day
    if need_db_load:
        user_db_data = await get_user_data(user_id_str, include_today=not is_new_day)
        if user_db_data:
            p = parse_profile(user_db_data['profile'])
            if not tasks_pool:
                tasks_pool = user_db_data['tasks_pool'] or (list(set(p.tasks_pool)) if p else [])
                state_updates['tasks_pool'] = tasks_pool
            
            one_time_tasks = user_db_data['one_time_tasks']
            state_updates['one_time_tasks'] = one_time_tasks
            
            if p:
                daily_tasks = p.daily_tasks
                daily_tasks_not_time = p.daily_tasks_not_time
                if not scheduler_arguments:
                    scheduler_arguments = p.scheduler_arguments
                    state_updates['scheduler_arguments'] = scheduler_arguments
    
    if not tasks_pool:
        await message.answer('Ваш список дел пуст! Добавьте ваши общие дела через запятую.')
        await state.set_state(ClientState.add_tasks_pool)
        return
    
    # Множество допустимых задач (tasks_pool + one_time_tasks + scheduled)
    valid_tasks = set(tasks_pool) | set(one_time_tasks)
    
    # Фильтруем разовые дела из daily_tasks и проверяем валидность
    daily_tasks = {k: v for k, v in daily_tasks.items() 
                   if v not in one_time_tasks and (v in valid_tasks or _is_scheduled_task(v) or v == 'закат ☀️')}
    daily_tasks_not_time = [t for t in daily_tasks_not_time 
                            if not _is_scheduled_task(t) and t not in one_time_tasks and t in valid_tasks]
    state_updates['daily_tasks'] = daily_tasks
    state_updates['daily_tasks_not_time'] = daily_tasks_not_time
    
    today_tasks = user_data.get('today_tasks', {})
    today_tasks_not_time = user_data.get('today_tasks_not_time', [])
    
    # Если новый день - сбрасываем today_tasks до daily_tasks
    if is_new_day:
        logger.info(f"New day detected ({last_tasks_date} -> {today_str}), resetting today_tasks")
        today_tasks = {k: v for k, v in daily_tasks.items() if not _is_scheduled_task(v)}
        today_tasks_not_time = [t for t in daily_tasks_not_time if not _is_scheduled_task(t)]
        state_updates['today_tasks_date'] = today_str
        state_updates['today_tasks_chosen'] = []
        state_updates['today_tasks_not_time_chosen'] = []
    else:
        # Восстанавливаем расписание из daily_*, если оно отсутствует
        if not today_tasks:
            today_tasks = daily_tasks.copy()
        else:
            for time_key, task in daily_tasks.items():
                today_tasks.setdefault(time_key, task)
        
        if not today_tasks_not_time:
            today_tasks_not_time = daily_tasks_not_time.copy()
        else:
            for task in daily_tasks_not_time:
                if task not in today_tasks_not_time:
                    today_tasks_not_time.append(task)
    
    # Фильтруем невалидные и старые scheduled задачи
    today_tasks = {k: v for k, v in today_tasks.items() 
                   if not _is_scheduled_task(v) and (v in valid_tasks or v == 'закат ☀️')}
    today_tasks_not_time = [t for t in today_tasks_not_time 
                           if not _is_scheduled_task(t) and t in valid_tasks]
    
    today_tasks_chosen = user_data.get('today_tasks_chosen', [])
    today_tasks_not_time_chosen = user_data.get('today_tasks_not_time_chosen', [])

    # Добавляем scheduled задачи на сегодня
    for key, values in scheduler_arguments.items():
        if should_task_run_today(values, now):
            try:
                task_text = normalize_preserve_case(key.split(' : ')[1]).replace('"', '').replace(' - ', '-')
                tmp = task_text.split('-')
                if len(tmp) >= 2:
                    time_part = tmp[1].split(' ')[0]
                    if ':' in time_part and len(time_part) == 5:
                        job_timing = time_part
                        task_name = tmp[0].strip()
                        suffix_parts = tmp[1].split(' ')[1:]
                        task_display = f"{task_name} {' '.join(suffix_parts)}".strip()
                        if job_timing not in today_tasks:
                            today_tasks[job_timing] = task_display
                    else:
                        if task_text not in today_tasks_not_time:
                            today_tasks_not_time.append(task_text)
                else:
                    if task_text not in today_tasks_not_time:
                        today_tasks_not_time.append(task_text)
            except (IndexError, AttributeError):
                pass

    # Обновляем закат (асинхронно, не блокируя)
    sunrise = user_data.get('sunrise', None)
    if sunrise != today_str:
        old_sunset_keys = [k for k, v in today_tasks.items() if v == 'закат ☀️']
        for key in old_sunset_keys:
            del today_tasks[key]
        
        try:
            sunset_time = await get_sunset_minus_30_safe()
            if sunset_time:
                today_tasks[sunset_time.strftime("%H:%M")] = 'закат ☀️'
                state_updates['sunrise'] = today_str
        except Exception as e:
            logger.error(f"Error getting sunset time: {e}")
    
    # Один вызов update_data
    state_updates['today_tasks'] = today_tasks
    state_updates['today_tasks_not_time'] = today_tasks_not_time
    await state.update_data(**state_updates)
    
    # Build keyboard
    keyboard = keyboard_builder(
        tasks_dict=today_tasks,
        tasks_list=today_tasks_not_time,
        grid=1,
        chosen=today_tasks_chosen + today_tasks_not_time_chosen,
        add_dell=True,
        last_button="🚀Отправить 🚀",
        add_save=True,
    )
    
    msg = 'Отметьте выполненные дела\nДля формирования расписания нажмите "Добавить"' if today_tasks else 'Ваш список дел пуст! Добавьте их нажав на кнопку "Добавить'
    await message.answer(msg, reply_markup=keyboard)
    await state.set_state(ClientState.greet)

async def scheduler_list(
    message_or_call: Union[Message, types.CallbackQuery],
    state: FSMContext,
    out_message: str,
    userdata: Dict[str, Any],
    **kwargs
) -> None:
    """Добавляет задачу в планировщик."""
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
    # Задачи добавятся в расписание при открытии "Заполнить дневник"



async def start(state: FSMContext, message: Message) -> None:
    user_data = await state.get_data()
    user_id_str = str(message.from_user.id)
    
    # Получаем все данные пользователя одним оптимизированным запросом
    db_data = await get_full_user_state(user_id_str)
    
    if db_data['profile'] is None:
        # Создаём профиль если не существует
        answer = await create_profile(user_id=message.from_user.id)
        if answer is None:
            await handle_new_user(message, state)
            return
        db_data = await get_full_user_state(user_id_str)
    
    if db_data['profile'] is not None:
        # Парсим профиль один раз
        p = parse_profile(db_data['profile'])
        if not p:
            await handle_new_user(message, state)
            return
        
        # Используем данные из отдельных таблиц или из профиля
        tasks_pool = db_data['tasks_pool'] or list(set(p.tasks_pool))
        one_time_tasks = db_data['one_time_tasks'] or p.one_time_tasks
        daily_tasks = db_data['daily_tasks'] or p.daily_tasks
        daily_tasks_not_time = db_data['daily_tasks_not_time'] or p.daily_tasks_not_time
        
        # Фильтруем разовые дела из daily_tasks
        daily_tasks = {k: v for k, v in daily_tasks.items() if v not in one_time_tasks}
        daily_tasks_not_time = [t for t in daily_tasks_not_time if t not in one_time_tasks]
        
        # Собираем все обновления state в один словарь
        state_updates = {
            'tasks_pool': list(set(tasks_pool)),
            'one_time_tasks': one_time_tasks,
            'daily_tasks': daily_tasks,
            'daily_tasks_not_time': daily_tasks_not_time,
            'scheduler_arguments': p.scheduler_arguments,
            'previous_diary': p.previous_diary,
            'notifications_data': p.notifications_data,
            'chosen_collected_data': p.chosen_collected_data,
        }
        
        if p.personal_records:
            state_updates['personal_records'] = p.personal_records
        
        # Настройка уведомлений
        if (p.notifications_data.get('chosen_notifications') == ['Включено'] 
            and 'hours' in p.notifications_data 
            and 'minutes' in p.notifications_data):
            existing_job_id = user_data.get('job_id')
            job_exists = existing_job_id and any(
                job.id == existing_job_id for job in scheduler.get_jobs()
            )
            if not job_exists:
                job_id = scheduler.add_job(
                    tasks_pool_function,
                    trigger='cron',
                    hour=p.notifications_data['hours'],
                    minute=p.notifications_data['minutes'],
                    args=(message, state))
                state_updates['job_id'] = job_id.id

        # Один вызов update_data
        await state.update_data(**state_updates)

        user_id = p.user_id
        path = f"{user_id}_Diary.xlsx"
        if os.path.exists(path):
            keyboard = generate_keyboard(
                ['Вывести Дневник', 'Настройки'],
                first_button='Заполнить Дневник')
        else:
            keyboard = generate_keyboard(['Заполнить Дневник'], last_button='Настройки')
        
        out_message = ''
        if p.personal_records:
            # Фильтруем рекорды — показываем только актуальные дела из tasks_pool
            filtered_records = {k: v for k, v in p.personal_records.items() if k in tasks_pool}
            if filtered_records != p.personal_records:
                await state.update_data(personal_records=filtered_records)
                await edit_database(personal_records=filtered_records, user_id=message.from_user.id)
            if filtered_records:
                record_message = "\n".join(f'{k} : {v}' for k, v in filtered_records.items())
                out_message += f'\n\nВаши рекорды:\n{record_message}'
                await message.answer(out_message, reply_markup=keyboard)
            else:
                await message.answer('Главное меню', reply_markup=keyboard)
        else:
            await message.answer('Главное меню', reply_markup=keyboard)

        await scheduler_in(state_updates, state, message=message)
    else:
        await handle_new_user(message, state)


async def close_db_pool():
    """Закрывает пул соединений БД."""
    from sqlite import _pool
    if _pool:
        await _pool.close()


async def executing_scheduler_job(state: FSMContext, out_message: str) -> None:
    # функция, которая срабатывает, когда срабатывает scheduler
    
    # Безопасно получаем название задачи из сообщения
    try:
        text_normalized = normalize_preserve_case(out_message.split(' : ')[1]).replace('"', '').replace(' - ', '-')
    except (IndexError, AttributeError):
        logger.error(f"Error parsing job text from: {out_message}")
        return

    tmp = text_normalized.split('-')
    
    # Получаем данные как можно позже, чтобы уменьшить вероятность гонки
    user_states_data = await state.get_data()
    user_id = user_states_data.get('user_id', None)
    state_updates = {}

    if len(tmp) == 2:
        job, job_timing = text_normalized.split('-')[0], text_normalized.split('-')[1].split(' ')[0]
        today_tasks = user_states_data.get('today_tasks', {})
        if today_tasks.get(job_timing) != job:
            today_tasks[job_timing] = job
            state_updates['today_tasks'] = today_tasks
    else:
        today_tasks_not_time = user_states_data.get('today_tasks_not_time', [])
        if text_normalized not in today_tasks_not_time:
            today_tasks_not_time.append(text_normalized)
            state_updates['today_tasks_not_time'] = today_tasks_not_time
    
    # Если это было разовое напоминание (trigger='date'), удаляем его из scheduler_arguments
    scheduler_arguments = user_states_data.get('scheduler_arguments', {})
    if out_message in scheduler_arguments and scheduler_arguments[out_message].get('trigger') == 'date':
        del scheduler_arguments[out_message]
        state_updates['scheduler_arguments'] = scheduler_arguments
        if user_id:
            await edit_database(scheduler_arguments=scheduler_arguments, user_id=user_id)

    if state_updates:
        await state.update_data(**state_updates)
    logger.info(f"Successfully added scheduled task '{text_normalized}' to daily_tasks for user {user_id}")



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
            try:
                await message.bot.pin_chat_message(message.chat.id, send_message.message_id)
            except Exception as e:
                logger.debug(f"Could not pin message: {e}")
            return personal_records
    else:
        await message.answer('Поздравляю! дневник заполнен')



def generate_keyboard(
    buttons: List[str],
    last_button: Optional[str] = None,
    first_button: Optional[str] = None
) -> types.ReplyKeyboardMarkup:
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


def normalized(text: str) -> str:
    return re.sub(r',(?=\S)', ', ', text).strip().lower().replace('ё', 'е')


def normalize_preserve_case(text: str) -> str:
    """Нормализует текст, сохраняя регистр букв."""
    return re.sub(r',(?=\S)', ', ', text).strip().replace('ё', 'е')


async def diary_out(message: Message) -> None:
    logs = await get_last_logs(message.from_user.id)
    if not logs:
        await message.answer("Дневник еще не создан. Сначала заполните его!")
        return

    # Собираем все записи в один текст
    lines = ["📅 Последние записи дневника:\n"]
    
    for log_date, activities, steps, sleep_quality, about_day, personal_rate in reversed(logs):
        try:
            formatted_date = datetime.strptime(log_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            formatted_date = log_date

        steps_val = '-' if steps is None else int(steps) if steps == int(steps) else steps
        sleep_val = '-' if sleep_quality is None else sleep_quality
        rate_val = '-' if personal_rate is None else int(personal_rate)
        
        # Компактный формат
        entry = f"📆 {formatted_date} | ⭐ {rate_val}/10"
        if steps_val != '-':
            entry += f" | 👣 {steps_val}"
        if sleep_val != '-':
            entry += f" | 😴 {sleep_val}"
        lines.append(entry)
        
        if activities and activities != '-':
            lines.append(f"   ✅ {activities[:100]}{'...' if len(activities) > 100 else ''}")
        if about_day and about_day != '-':
            lines.append(f"   💭 {about_day[:150]}{'...' if len(about_day) > 150 else ''}")
        lines.append("")
    
    # Отправляем одним сообщением (или несколькими если > 4096)
    full_text = "\n".join(lines)
    for i in range(0, len(full_text), 4096):
        await message.answer(full_text[i:i + 4096])




MSK = ZoneInfo("Europe/Moscow")
PRIMARY_API = "https://api.sunrise-sunset.org/json"
FALLBACK_API = "https://api.sunrisesunset.io/json"

# Кэш для API заката: {date_str: (sunset_datetime, fetch_time)}
_sunset_cache: Dict[str, tuple] = {}
_CACHE_TTL_HOURS = 12

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
    Uses caching to avoid excessive API calls.
    Raises RuntimeError only if all attempts fail.
    """
    # Проверяем кэш
    cache_key = date or datetime.now(MSK).date().isoformat()
    if cache_key in _sunset_cache:
        cached_result, fetch_time = _sunset_cache[cache_key]
        if datetime.now(MSK) - fetch_time < timedelta(hours=_CACHE_TTL_HOURS):
            logger.debug(f"Using cached sunset for {cache_key}")
            return cached_result
    
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
                    result = dt - timedelta(minutes=30)
                    _sunset_cache[cache_key] = (result, datetime.now(MSK))
                    return result
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
                    result = dt - timedelta(minutes=30)
                    _sunset_cache[cache_key] = (result, datetime.now(MSK))
                    return result
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


