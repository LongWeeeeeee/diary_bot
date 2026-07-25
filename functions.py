"""Вспомогательные функции бота-дневника."""

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
from datetime import datetime, timedelta
from html import escape as html_escape
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
    LAT,
    LNG,
    NEGATIVE_RESPONSES,
    TARGET_TZ,
    WEEKDAY_TRANSLATE,
    ClientState,
    bot,
    remove_markup,
    scheduler,
    should_task_run_today,
    timed,
)
from analytics import MIN_RATED_DAYS, build_analysis, build_word_report
from sqlite import (
    ParsedProfile,
    add_daily_log,
    batch_update_tasks,
    create_profile,
    edit_database,
    get_all_logs,
    get_full_user_state,
    get_last_logs,
    get_one_time_tasks,
    get_tasks_pool,
    get_user_data,
    get_users_with_notifications,
    get_users_with_scheduler_jobs,
    parse_profile,
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
    weekdays = [
        "понедельник",
        "вторник",
        "среду",
        "четверг",
        "пятницу",
        "субботу",
        "воскресенье",
    ]
    for prefix in ["каждый ", "каждую ", "каждое "]:
        if prefix in task_lower:
            # Проверяем что после "каждый" идёт день недели
            for day in weekdays:
                if f"{prefix}{day}" in task_lower:
                    return True
    return False


def notification_job_id(user_id: Union[int, str]) -> str:
    """Возвращает стабильный job id для ежедневных уведомлений пользователя."""
    return f"notify:{int(user_id)}"


def _extract_notification_owner_id(message: Any) -> Optional[int]:
    """Извлекает user_id из Message / MessageProxy для cleanup старых jobs."""
    if message is None:
        return None

    from_user = getattr(message, "from_user", None)
    if from_user is not None:
        user_id = getattr(from_user, "id", None)
        if user_id is not None:
            return int(user_id)

    chat = getattr(message, "chat", None)
    if chat is not None:
        chat_id = getattr(chat, "id", None)
        if chat_id is not None:
            return int(chat_id)

    chat_id = getattr(message, "chat_id", None)
    if chat_id is not None:
        return int(chat_id)

    return None


def get_notification_job_ids(user_id: Union[int, str]) -> List[str]:
    """Собирает все jobs ежедневных уведомлений пользователя, включая legacy-дубли."""
    user_id = int(user_id)
    stable_job = notification_job_id(user_id)
    job_ids: List[str] = []

    for job in scheduler.get_jobs():
        if job.id == stable_job:
            job_ids.append(job.id)
            continue

        if getattr(job, "func", None) is not tasks_pool_function:
            continue

        args = getattr(job, "args", ()) or ()
        message = args[0] if args else None
        if _extract_notification_owner_id(message) == user_id:
            job_ids.append(job.id)

    return list(dict.fromkeys(job_ids))


def remove_notification_jobs(
    user_id: Union[int, str], legacy_job_id: Optional[str] = None
) -> List[str]:
    """Удаляет все найденные jobs ежедневных уведомлений пользователя."""
    job_ids = get_notification_job_ids(user_id)
    if legacy_job_id:
        job_ids.append(legacy_job_id)

    removed: List[str] = []
    for job_id in dict.fromkeys(job_ids):
        try:
            scheduler.remove_job(job_id=job_id)
            removed.append(job_id)
        except Exception:
            pass
    return removed


def ensure_notification_job(
    user_id: Union[int, str],
    hours: int,
    minutes: int,
    message: Any,
    state: FSMContext,
):
    """Создаёт ровно одну job ежедневных уведомлений для пользователя."""
    stable_job = notification_job_id(user_id)
    remove_notification_jobs(user_id)
    return scheduler.add_job(
        tasks_pool_function,
        trigger="cron",
        hour=hours,
        minute=minutes,
        args=(message, state),
        id=stable_job,
        replace_existing=True,
    )


def dedupe_preserve_order(items: List[str]) -> List[str]:
    """Убирает дубликаты, сохраняя порядок (set() его теряет и тасует между рестартами)."""
    return list(dict.fromkeys(items))


def _to_float(value) -> Optional[float]:
    """Приводит значение к float или None (для '-' и прочих пропусков)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diary_excel_path(user_id: Union[int, str]) -> str:
    """Путь к Excel-дневнику пользователя."""
    return f"{user_id}_Diary.xlsx"


def _write_diary_excel(path: str, logs: List[tuple]) -> None:
    """Пересобирает Excel-дневник из записей БД (блокирующая часть)."""
    records = []
    for log_date, log_activities, log_steps, log_sleep, log_about, log_rate in logs:
        try:
            formatted_date = datetime.strptime(log_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            formatted_date = log_date
        records.append(
            {
                "Дата": formatted_date,
                "Дела за день": log_activities or "-",
                "Шаги": "-" if log_steps is None else log_steps,
                "Sleep quality": "-" if log_sleep is None else log_sleep,
                "О дне": log_about or "-",
                "My rate": "-" if log_rate is None else log_rate,
            }
        )
    df = pd.DataFrame(
        records,
        columns=["Дата", "Дела за день", "Шаги", "Sleep quality", "О дне", "My rate"],
    )
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Лист1")

        workbook = writer.book
        worksheet = writer.sheets["Лист1"]
        cell_format = workbook.add_format({"text_wrap": True})
        cell_format_middle = workbook.add_format({"text_wrap": True, "align": "center"})
        for row, size in zip(["B", "E"], [60, 122]):
            worksheet.set_column(f"{row}:{row}", size, cell_format)
        for row in ["A", "C", "D", "E"]:
            worksheet.set_column(f"{row}:{row}", 10, cell_format_middle)


async def export_diary_excel(user_id: Union[int, str]) -> Optional[str]:
    """Пересобирает файл дневника из БД. НЕ создаёт запись за день.

    Нужен для «скачать дневник»: раньше файл собирался через add_day_to_excel,
    который писал в daily_logs пустую запись за сегодня и затирал настоящую.
    """
    logs = await get_all_logs(user_id)
    if not logs:
        return None
    path = diary_excel_path(user_id)
    try:
        await asyncio.to_thread(_write_diary_excel, path, logs)
    except Exception as e:
        logger.error(f"Error exporting diary file {path}: {e}")
        return None
    return path


@timed
async def add_day_to_excel(
    date: datetime,
    activities: List[str],
    sleep_quality: Union[int, float, str],
    personal_rate: Union[int, float],
    my_steps: Union[int, float, str],
    user_message: str,
    message: Message,
    excel_chosen_tasks: Optional[List[str]] = None,
    personal_records: Optional[Dict[str, Any]] = None,
    today: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    user_id = message.from_user.id
    path = diary_excel_path(user_id)

    log_datetime = date if today else date - timedelta(days=1)
    log_date_iso = log_datetime.strftime("%Y-%m-%d")

    activities_text = ", ".join(activities)
    about_day_text = user_message
    if excel_chosen_tasks:
        about_day_text = (
            f"Выполнил разовые дела: {', '.join(excel_chosen_tasks)}, {user_message}"
        )

    steps_value = _to_float(my_steps)
    sleep_value = _to_float(sleep_quality)
    rate_value = _to_float(personal_rate)

    await add_daily_log(
        user_id=user_id,
        date=log_date_iso,
        activities=activities_text,
        steps=steps_value,
        sleep_quality=sleep_value,
        about_day=about_day_text,
        personal_rate=rate_value,
    )

    logs = await get_all_logs(user_id)

    try:
        await asyncio.to_thread(_write_diary_excel, path, logs)
    except Exception as e:
        logger.error(f"Error saving diary file {path}: {e}")
        await message.answer("Ошибка при сохранении файла дневника.")
        return None

    # Дни без дел НЕ выбрасываем: они должны обрывать серию
    activity_history = [(log[0], log[1]) for log in logs]
    answer = await counter_max_days(
        activity_history=activity_history,
        message=message,
        activities=activities,
        personal_records=personal_records,
    )
    if answer is not None:
        personal_records = answer
        return personal_records


# day_to_prefix удалён - используется из config.py


def parse_time_key(key: str) -> int:
    """
    Преобразует строку вида "H", "HH", "H:MM" или "HH:MM" в число минут с начала суток.
    """
    if ":" in key:
        hours, minutes = map(int, key.split(":"))
    else:
        hours, minutes = int(key), 0
    return hours * 60 + minutes


def _activity_list(raw) -> List[str]:
    """Разбирает строку дел за день в список названий (без дублей, в исходном порядке)."""
    if not isinstance(raw, str):
        return []
    return dedupe_preserve_order([part.strip() for part in raw.split(",") if part.strip()])


def _split_activities(raw) -> set:
    """Разбирает строку дел за день в множество названий."""
    return set(_activity_list(raw))


def _activities_by_day(history) -> Dict[Any, set]:
    """Приводит историю [(дата, дела), ...] к словарю {date: {дела}}.

    Пустые дни сохраняются (пустым множеством) — они обрывают серию.
    """
    by_day: Dict[Any, set] = {}
    for row in history:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        raw_date, raw_activities = row[0], row[1]
        if isinstance(raw_date, datetime):
            day = raw_date.date()
        else:
            try:
                day = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
        by_day.setdefault(day, set()).update(_split_activities(raw_activities))
    return by_day


def counter_positive(current_word, history) -> int:
    """Серия подряд идущих КАЛЕНДАРНЫХ дней с делом, считая от последней записи.

    Пропущенный календарный день (нет записи или дело не отмечено) обрывает серию.
    """
    by_day = _activities_by_day(history)
    if not by_day:
        return 0
    day = max(by_day)
    count = 0
    while current_word in by_day.get(day, set()):
        count += 1
        day -= timedelta(days=1)
    return count


def _plural_days(count: int) -> str:
    """Склонение слова «день» для числа."""
    if 11 <= count % 100 <= 14:
        return "дней"
    last = count % 10
    if last == 1:
        return "день"
    if last in (2, 3, 4):
        return "дня"
    return "дней"


def _parse_job_datetime(
    values: dict, field: str, fallback_fmt: str
) -> Optional[datetime]:
    """Парсит datetime из значений job."""
    val = values.get(field)
    if not val:
        return None
    if isinstance(val, datetime):
        dt_val = val
    elif isinstance(val, str):
        try:
            dt_val = datetime.fromisoformat(val)
        except Exception:
            try:
                dt_val = datetime.strptime(val, fallback_fmt)
            except Exception:
                return None
    else:
        return None
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=TARGET_TZ)
    return dt_val


def _prepare_job_values(values: dict, state: FSMContext, key: str) -> dict:
    """Подготавливает значения для добавления job в scheduler."""
    values_copy = values.copy()
    values_copy["args"] = (state, key)

    if "date" in values_copy:
        dt_val = _parse_job_datetime(values, "date", "%Y-%m-%d")
        if dt_val:
            values_copy["date"] = dt_val
    elif "run_date" in values_copy:
        dt_val = _parse_job_datetime(values, "run_date", "%Y-%m-%d %H:%M")
        if dt_val:
            values_copy["run_date"] = dt_val

    if "day_of_week" in values_copy and isinstance(values_copy["day_of_week"], list):
        values_copy["day_of_week"] = values_copy["day_of_week"][0]

    return values_copy


async def scheduler_in(data, state, message):
    scheduler_arguments = data.get("scheduler_arguments", {})
    if not scheduler_arguments:
        return

    # Кэшируем существующие job IDs для быстрого поиска
    existing_job_ids = {job.id for job in scheduler.get_jobs()}
    current_date = datetime.now(TARGET_TZ)
    expired_keys = []

    await state.update_data(user_id=message.from_user.id)

    for key, values in scheduler_arguments.items():
        # Проверяем просроченные run_date задачи
        if "run_date" in values:
            dt_val = _parse_job_datetime(values, "run_date", "%Y-%m-%d %H:%M")
            if dt_val and current_date > (dt_val + timedelta(minutes=1)):
                expired_keys.append(key)
                continue

        values_copy = _prepare_job_values(values, state, key)
        unique_id = generate_unique_id_from_args(values_copy)

        if unique_id not in existing_job_ids:
            values_copy["id"] = unique_id
            scheduler.add_job(executing_scheduler_job, **values_copy)
            existing_job_ids.add(unique_id)  # Добавляем в кэш чтобы не дублировать

    # Удаляем просроченные задачи
    if expired_keys:
        for key in expired_keys:
            del scheduler_arguments[key]
        await state.update_data(scheduler_arguments=scheduler_arguments)
        await edit_database(
            scheduler_arguments=scheduler_arguments, user_id=message.from_user.id
        )


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
    add_money: bool = False,
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
        sorted_tasks = sorted(
            tasks_dict.items(), key=lambda item: parse_time_key(item[0])
        )
        for time, task in sorted_tasks:
            if checks:
                data_builder.button(
                    text=f"{time} {task} \u2714\ufe0f", callback_data=time
                )
            elif not price_tag:
                mark = "\u2705\ufe0f" if time in chosen_set else "\u2714\ufe0f"
                data_builder.button(text=f"{time} {task} {mark}", callback_data=time)

    data_builder.adjust(grid, grid)
    d_new_builder = InlineKeyboardBuilder()

    if add_money:
        d_new_builder.button(text="\U0001f4b0 Начислить", callback_data="Начислить")
    if add_dell:
        if add_save:
            d_new_builder.button(
                text="\U0001f4be Сохранить \U0001f4be", callback_data="Сохранить"
            )
        d_new_builder.button(
            text="\U0001f4bc Добавить \U0001f4bc", callback_data="Добавить"
        )
        d_new_builder.button(text="\u274c Удалить \u274c", callback_data="Удалить")
    if last_button:
        callback = re.sub(r"[\U0001F000-\U0001FAFF\s]+", "", last_button)
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
    copy_args_dict["args"] = args_dict["args"][1]

    # Define a converter for non-serializable types
    def datetime_converter(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    serialized_args = json.dumps(
        copy_args_dict, sort_keys=True, default=datetime_converter
    )
    # Generate a unique hash
    return hashlib.sha256(serialized_args.encode()).hexdigest()


async def handle_new_user(message: Message, state: FSMContext) -> None:
    # Сохраняем user_id в state для использования в scheduler jobs
    await state.update_data(user_id=message.from_user.id)
    info = await bot.get_me()
    try:
        await message.answer_sticker(
            "CAACAgIAAxkBAAIsZGVY5wgzBq6lUUSgcSYTt99JnOBbAAIIAAPANk8Tb2wmC94am2kzBA"
        )
    except Exception as e:
        logger.warning(f"Could not send sticker: {e}")
    await message.answer(
        f"""Привет, {message.from_user.full_name}! \nДобро пожаловать в {info.username}!
Он поможет тебе вести отчет о твоих днях и делать выводы почему день был плохим или хорошим
Для начала нужно задать список дел через запятую. Какие у вас есть дела в течении дня? Например:"""
    )
    await message.answer(
        "подьем, отбой, зарядка, массаж головы и ступ, подтягивания, завтрак, обед, ужин, прогулка, расстяжка"
    )
    await message.answer(
        "Вы можете воспользоваться предложенным списком или написать свой. Данные могут быть какие угодно",
        reply_markup=remove_markup,
    )
    await state.set_state(ClientState.add_tasks_pool)


@timed
async def tasks_pool_function(message, state: FSMContext):
    """Показывает расписание на сегодня для заполнения дневника."""
    import time

    t0 = time.perf_counter()
    user_data = await state.get_data()
    t1 = time.perf_counter()
    # Используем user_id из state если есть (для scheduler jobs), иначе из message
    user_id_str = str(user_data.get("user_id") or message.from_user.id)

    # Собираем все данные для одного update_data в конце
    state_updates = {}

    now = datetime.now(ZoneInfo("Europe/Moscow"))
    today_str = now.strftime("%Y-%m-%d")

    # Проверяем, изменился ли день с последнего открытия расписания
    last_tasks_date = user_data.get("today_tasks_date", None)
    # Проверяем, был ли дневник уже отправлен за текущую сессию
    diary_submitted_date = user_data.get("diary_submitted_date", None)

    # "Рабочая" дата расписания — дата, для которой мы показываем дела.
    # ВАЖНО: после 00:00 мы продолжаем работать с предыдущей датой (last_tasks_date),
    # пока дневник за неё явно не отправлен. Это предотвращает "пропадание" отмеченных дел.
    if (
        last_tasks_date is not None
        and last_tasks_date != today_str
        and diary_submitted_date != last_tasks_date
    ):
        working_date_str = last_tasks_date
    else:
        working_date_str = today_str

    # Новый день только если:
    # 1) дата расписания была установлена
    # 2) календарная дата уже другая
    # 3) дневник был ЯВНО отправлен за last_tasks_date
    # Это гарантирует, что сброс происходит только после отправки дневника, а не просто после полуночи.
    is_new_day = (
        last_tasks_date is not None
        and last_tasks_date != today_str
        and diary_submitted_date == last_tasks_date
    )

    logger.info(
        f"tasks_pool_function: user={user_id_str}, now={today_str}, last_date={last_tasks_date}, "
        f"working_date={working_date_str}, diary_submitted={diary_submitted_date}, is_new_day={is_new_day}"
    )

    # Получаем данные из state или БД
    tasks_pool = user_data.get("tasks_pool", [])
    one_time_tasks = user_data.get("one_time_tasks", [])
    daily_tasks = user_data.get("daily_tasks", {})
    daily_tasks_not_time = user_data.get("daily_tasks_not_time", [])
    scheduler_arguments = user_data.get("scheduler_arguments", {})

    # Загружаем из БД только если данных нет в state
    need_db_load = not tasks_pool or is_new_day
    t2 = time.perf_counter()
    if need_db_load:
        user_db_data = await get_user_data(user_id_str, include_today=not is_new_day)
        t3 = time.perf_counter()
        logger.debug(
            f"tasks_pool_function: get_data={int((t1 - t0) * 1000)}ms, get_user_data={int((t3 - t2) * 1000)}ms"
        )
        if user_db_data:
            p = parse_profile(user_db_data["profile"])
            if not tasks_pool:
                tasks_pool = user_db_data["tasks_pool"] or (
                    dedupe_preserve_order(p.tasks_pool) if p else []
                )
                state_updates["tasks_pool"] = tasks_pool

            one_time_tasks = user_db_data["one_time_tasks"]
            state_updates["one_time_tasks"] = one_time_tasks

            if p:
                # Используем daily_tasks из state если есть, иначе из профиля
                if not daily_tasks:
                    daily_tasks = p.daily_tasks
                if not daily_tasks_not_time:
                    daily_tasks_not_time = p.daily_tasks_not_time
                if not scheduler_arguments:
                    scheduler_arguments = p.scheduler_arguments
                    state_updates["scheduler_arguments"] = scheduler_arguments

    if not tasks_pool:
        await message.answer(
            "Ваш список дел пуст! Добавьте ваши общие дела через запятую."
        )
        await state.set_state(ClientState.add_tasks_pool)
        return

    # Множество допустимых задач (tasks_pool + one_time_tasks + scheduled)
    valid_tasks = set(tasks_pool) | set(one_time_tasks)

    # Фильтруем разовые дела из daily_tasks и проверяем валидность
    daily_tasks = {
        k: v
        for k, v in daily_tasks.items()
        if v not in one_time_tasks
        and (v in valid_tasks or _is_scheduled_task(v) or v == "закат ☀️")
    }
    daily_tasks_not_time = [
        t
        for t in daily_tasks_not_time
        if not _is_scheduled_task(t) and t not in one_time_tasks and t in valid_tasks
    ]
    # Убираем дубли: если дело уже есть с временем, оно не должно быть в списке без времени
    if daily_tasks:
        timed_daily_tasks = set(daily_tasks.values())
        daily_tasks_not_time = [
            t for t in daily_tasks_not_time if t not in timed_daily_tasks
        ]
    state_updates["daily_tasks"] = daily_tasks
    state_updates["daily_tasks_not_time"] = daily_tasks_not_time

    today_tasks = user_data.get("today_tasks", {})
    today_tasks_not_time = user_data.get("today_tasks_not_time", [])
    # Множество удалённых дел за сегодня — не восстанавливаем их из daily_tasks
    today_tasks_deleted = set(user_data.get("today_tasks_deleted", []))

    # Если новый день - сбрасываем today_tasks до daily_tasks
    if is_new_day:
        logger.info(
            f"New day detected ({last_tasks_date} -> {today_str}), resetting today_tasks for user {user_id_str}"
        )
        today_tasks = {
            k: v for k, v in daily_tasks.items() if not _is_scheduled_task(v)
        }
        today_tasks_not_time = [
            t for t in daily_tasks_not_time if not _is_scheduled_task(t)
        ]
        state_updates["today_tasks_date"] = today_str
        state_updates["today_tasks_chosen"] = []
        state_updates["today_tasks_not_time_chosen"] = []
        state_updates["today_tasks_deleted"] = []  # Сбрасываем удалённые дела для нового дня
        state_updates["diary_submitted_date"] = (
            None  # Сбрасываем флаг отправки дневника для нового дня
        )
        today_tasks_deleted = set()  # Обнуляем локальную переменную тоже
    else:
        # Сохраняем дату только при первом открытии (last_tasks_date is None)
        # После полуночи НЕ обновляем дату - продолжаем работать с предыдущим днём
        if last_tasks_date is None:
            state_updates["today_tasks_date"] = today_str
        # Восстанавливаем расписание из daily_*, если оно отсутствует
        if not today_tasks:
            # Не восстанавливаем удалённые дела
            today_tasks = {
                k: v for k, v in daily_tasks.items() if v not in today_tasks_deleted
            }
        else:
            for time_key, task in daily_tasks.items():
                # Не восстанавливаем удалённые дела
                if task not in today_tasks_deleted:
                    today_tasks.setdefault(time_key, task)

        if not today_tasks_not_time:
            # Не восстанавливаем удалённые дела
            today_tasks_not_time = [
                t for t in daily_tasks_not_time if t not in today_tasks_deleted
            ]
        else:
            for task in daily_tasks_not_time:
                # Не восстанавливаем удалённые дела
                if task not in today_tasks_not_time and task not in today_tasks_deleted:
                    today_tasks_not_time.append(task)

    # Фильтруем невалидные и старые scheduled задачи
    today_tasks = {
        k: v
        for k, v in today_tasks.items()
        if not _is_scheduled_task(v) and (v in valid_tasks or v == "закат ☀️")
    }
    today_tasks_not_time = [
        t
        for t in today_tasks_not_time
        if not _is_scheduled_task(t) and t in valid_tasks
    ]

    # Определяем "рабочую дату" расписания - это дата для которой мы показываем задачи.
    # Используем stable working_date_str (см. выше), чтобы после 00:00 не перескакивать на новый день,
    # пока дневник за прошлую дату не отправлен.
    if working_date_str != today_str:
        schedule_date = datetime.strptime(working_date_str, "%Y-%m-%d").replace(
            tzinfo=ZoneInfo("Europe/Moscow")
        )
    else:
        schedule_date = now

    # 1) Удаляем из today_* все одноразовые date-задачи, чтобы после 00:00 они не "залипали"
    # НО: только если дневник уже отправлен (is_new_day) или это тот же день.
    # Если после полуночи дневник ещё не отправлен — не удаляем задачи, чтобы сохранить выбор.
    date_task_texts: set[str] = set()
    if working_date_str == today_str:
        # Только если работаем с сегодняшним днём — удаляем и пересчитываем date-задачи
        for key, values in scheduler_arguments.items():
            if values.get("trigger") == "date" or "run_date" in values:
                try:
                    task_text = (
                        normalize_preserve_case(key.split(" : ")[1])
                        .replace('"', "")
                        .replace(" - ", "-")
                    )
                    date_task_texts.add(task_text)
                except (IndexError, AttributeError):
                    continue

        if date_task_texts:
            # удаляем time-based date задачи
            to_delete_time_keys = []
            for time_key, task_val in today_tasks.items():
                # date задачи с временем хранятся как "task_display" (название + суффиксы),
                # поэтому проверяем вхождение исходного task_text как префикса.
                for full_text in date_task_texts:
                    tmp = full_text.split("-")
                    if len(tmp) >= 2:
                        task_name = tmp[0].strip()
                        suffix_parts = tmp[1].split(" ")[1:]
                        task_display = f"{task_name} {' '.join(suffix_parts)}".strip()
                        if task_val == task_display:
                            to_delete_time_keys.append(time_key)
                            break
            for k in to_delete_time_keys:
                today_tasks.pop(k, None)

            # удаляем not-time date задачи по точному совпадению текста
            today_tasks_not_time = [
                t for t in today_tasks_not_time if t not in date_task_texts
            ]

    # 2) Добавляем scheduled задачи по schedule_date (рабочая дата заполнения)
    # Это гарантирует, что после полуночи мы продолжаем показывать задачи за вчера,
    # пока дневник не отправлен.
    for key, values in scheduler_arguments.items():
        run_on = schedule_date
        if should_task_run_today(values, run_on):
            try:
                task_text = (
                    normalize_preserve_case(key.split(" : ")[1])
                    .replace('"', "")
                    .replace(" - ", "-")
                )
                tmp = task_text.split("-")
                if len(tmp) >= 2:
                    time_part = tmp[1].split(" ")[0]
                    if ":" in time_part and len(time_part) == 5:
                        job_timing = time_part
                        task_name = tmp[0].strip()
                        suffix_parts = tmp[1].split(" ")[1:]
                        task_display = f"{task_name} {' '.join(suffix_parts)}".strip()
                        # Не добавляем удалённые дела
                        if job_timing not in today_tasks and task_display not in today_tasks_deleted:
                            today_tasks[job_timing] = task_display
                    else:
                        # Не добавляем удалённые дела
                        if task_text not in today_tasks_not_time and task_text not in today_tasks_deleted:
                            today_tasks_not_time.append(task_text)
                else:
                    # Не добавляем удалённые дела
                    if task_text not in today_tasks_not_time and task_text not in today_tasks_deleted:
                        today_tasks_not_time.append(task_text)
            except (IndexError, AttributeError):
                pass

    # Убираем дубли: если дело уже есть с временем, оно не должно быть в списке без времени
    if today_tasks:
        timed_today_tasks = set(today_tasks.values())
        today_tasks_not_time = [
            t for t in today_tasks_not_time if t not in timed_today_tasks
        ]

    # Обновляем закат (асинхронно, не блокируя)
    # Используем рабочую дату расписания для определения нужен ли пересчёт заката
    schedule_date_str = schedule_date.strftime("%Y-%m-%d")
    sunrise = user_data.get("sunrise", None)
    if sunrise != schedule_date_str:
        old_sunset_keys = [k for k, v in today_tasks.items() if v == "закат ☀️"]
        for key in old_sunset_keys:
            del today_tasks[key]

        try:
            sunset_time = await get_sunset_minus_30_safe(date=schedule_date_str)
            if sunset_time:
                today_tasks[sunset_time.strftime("%H:%M")] = "закат ☀️"
                state_updates["sunrise"] = schedule_date_str
        except Exception as e:
            logger.error(f"Error getting sunset time: {e}")

    # Теперь валидируем chosen - ПОСЛЕ добавления scheduled задач и заката
    # Используем chosen из state_updates если они были сброшены (новый день), иначе из user_data
    today_tasks_chosen = state_updates.get(
        "today_tasks_chosen", user_data.get("today_tasks_chosen", [])
    )
    today_tasks_not_time_chosen = state_updates.get(
        "today_tasks_not_time_chosen", user_data.get("today_tasks_not_time_chosen", [])
    )

    # Валидируем chosen - убираем выбранные дела которых больше нет в расписании
    original_chosen = today_tasks_chosen.copy() if today_tasks_chosen else []
    original_not_time_chosen = (
        today_tasks_not_time_chosen.copy() if today_tasks_not_time_chosen else []
    )
    today_tasks_chosen = [k for k in today_tasks_chosen if k in today_tasks]
    today_tasks_not_time_chosen = [
        t for t in today_tasks_not_time_chosen if t in today_tasks_not_time
    ]

    # Сохраняем валидированные chosen только если они изменились
    if today_tasks_chosen != original_chosen:
        state_updates["today_tasks_chosen"] = today_tasks_chosen
    if today_tasks_not_time_chosen != original_not_time_chosen:
        state_updates["today_tasks_not_time_chosen"] = today_tasks_not_time_chosen

    logger.debug(
        f"tasks_pool_function: chosen={today_tasks_chosen}, not_time_chosen={today_tasks_not_time_chosen}"
    )

    # Один вызов update_data
    state_updates["today_tasks"] = today_tasks
    state_updates["today_tasks_not_time"] = today_tasks_not_time
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

    msg = (
        'Отметьте выполненные дела\nДля формирования расписания нажмите "Добавить"'
        if today_tasks
        else 'Ваш список дел пуст! Добавьте их нажав на кнопку "Добавить'
    )
    try:
        await message.answer(msg, reply_markup=keyboard)
        await state.set_state(ClientState.greet)
    except Exception as e:
        logger.error(f"Failed to send notification to user {user_id_str}: {e}")


async def scheduler_list(
    message_or_call: Union[Message, types.CallbackQuery],
    state: FSMContext,
    out_message: str,
    userdata: Dict[str, Any],
    **kwargs,
) -> None:
    """Добавляет задачу в планировщик."""
    actor_id = message_or_call.from_user.id

    data = await state.get_data()
    scheduler_arguments = data.get("scheduler_arguments", {})
    scheduler_arguments[out_message] = kwargs

    # использовать actor_id, а не message_obj.from_user.id
    await edit_database(scheduler_arguments=scheduler_arguments, user_id=actor_id)
    await state.update_data(scheduler_arguments=scheduler_arguments, user_id=actor_id)

    # 2) Немедленно добавляем job в APScheduler (без ожидания рестарта)
    values_copy = _prepare_job_values(
        scheduler_arguments[out_message], state, out_message
    )
    unique_id = generate_unique_id_from_args(values_copy)

    existing_job_ids = {job.id for job in scheduler.get_jobs()}
    if unique_id not in existing_job_ids:
        values_copy["id"] = unique_id
        scheduler.add_job(executing_scheduler_job, **values_copy)
    # Задачи добавятся в расписание при открытии "Заполнить дневник"


@timed
async def start(state: FSMContext, message: Message) -> None:
    import time

    t0 = time.perf_counter()
    user_data = await state.get_data()
    t1 = time.perf_counter()
    user_id_str = str(message.from_user.id)

    # Получаем все данные пользователя одним оптимизированным запросом
    db_data = await get_full_user_state(user_id_str)
    t2 = time.perf_counter()
    logger.debug(
        f"start: get_data={int((t1 - t0) * 1000)}ms, get_full_user_state={int((t2 - t1) * 1000)}ms"
    )

    if db_data["profile"] is None:
        # Создаём профиль если не существует
        answer = await create_profile(user_id=message.from_user.id)
        if answer is None:
            await handle_new_user(message, state)
            return
        db_data = await get_full_user_state(user_id_str)

    if db_data["profile"] is not None:
        # Парсим профиль один раз
        p = parse_profile(db_data["profile"])
        if not p:
            await handle_new_user(message, state)
            return

        # Используем данные из отдельных таблиц или из профиля
        tasks_pool = (
            db_data["tasks_pool"]
            if db_data["tasks_pool"]
            else dedupe_preserve_order(p.tasks_pool)
        )
        one_time_tasks = (
            db_data["one_time_tasks"] if db_data["one_time_tasks"] else p.one_time_tasks
        )
        # Используем данные из таблиц если они есть, иначе из JSON профиля
        daily_tasks_raw = (
            db_data["daily_tasks"] if db_data["daily_tasks"] else p.daily_tasks
        )
        daily_tasks_not_time_raw = (
            db_data["daily_tasks_not_time"]
            if db_data["daily_tasks_not_time"]
            else p.daily_tasks_not_time
        )

        # Множество допустимых задач
        valid_tasks = set(tasks_pool) | set(one_time_tasks)

        # Фильтруем daily_tasks - только задачи из tasks_pool/one_time_tasks, не разовые
        daily_tasks = {
            k: v
            for k, v in daily_tasks_raw.items()
            if v not in one_time_tasks and v in valid_tasks
        }
        daily_tasks_not_time = [
            t
            for t in daily_tasks_not_time_raw
            if t not in one_time_tasks and t in valid_tasks
        ]

        # Если данные изменились после фильтрации - сохраняем в БД (обе таблицы)
        if (
            daily_tasks != daily_tasks_raw
            or daily_tasks_not_time != daily_tasks_not_time_raw
        ):
            await edit_database(
                daily_tasks=daily_tasks,
                daily_tasks_not_time=daily_tasks_not_time,
                user_id=message.from_user.id,
            )
            await batch_update_tasks(
                user_id_str,
                daily_tasks=daily_tasks,
                daily_tasks_not_time=daily_tasks_not_time,
            )

        # Собираем все обновления state в один словарь
        # Сохраняем user_id для использования в scheduler jobs
        state_updates = {
            "user_id": message.from_user.id,
            "tasks_pool": dedupe_preserve_order(tasks_pool),
            "one_time_tasks": one_time_tasks,
            "daily_tasks": daily_tasks,
            "daily_tasks_not_time": daily_tasks_not_time,
            "scheduler_arguments": p.scheduler_arguments,
            "previous_diary": p.previous_diary,
            "notifications_data": p.notifications_data,
            "chosen_collected_data": p.chosen_collected_data,
        }

        if p.personal_records:
            state_updates["personal_records"] = p.personal_records

        # Настройка уведомлений
        if (
            p.notifications_data.get("chosen_notifications") == ["Включено"]
            and "hours" in p.notifications_data
            and "minutes" in p.notifications_data
        ):
            job_id = ensure_notification_job(
                user_id=message.from_user.id,
                hours=p.notifications_data["hours"],
                minutes=p.notifications_data["minutes"],
                message=message,
                state=state,
            )
            state_updates["job_id"] = job_id.id
        else:
            remove_notification_jobs(
                message.from_user.id, legacy_job_id=user_data.get("job_id")
            )
            state_updates["job_id"] = ""

        # Один вызов update_data
        t3 = time.perf_counter()
        await state.update_data(**state_updates)
        t4 = time.perf_counter()
        logger.debug(f"start: update_data={int((t4 - t3) * 1000)}ms")

        user_id = p.user_id
        path = diary_excel_path(user_id)
        if os.path.exists(path):
            keyboard = generate_keyboard(
                ["Вывести Дневник", "Анализ 📊", "Настройки"],
                first_button="Заполнить Дневник",
            )
        else:
            keyboard = generate_keyboard(["Заполнить Дневник"], last_button="Настройки")

        out_message = ""
        if p.personal_records:
            # Фильтруем рекорды — показываем только актуальные дела из tasks_pool
            filtered_records = {
                k: v for k, v in p.personal_records.items() if k in tasks_pool
            }
            if filtered_records != p.personal_records:
                await state.update_data(personal_records=filtered_records)
                await edit_database(
                    personal_records=filtered_records, user_id=message.from_user.id
                )
            record_message = format_records(filtered_records)
            if record_message:
                out_message += f"\n\n🏆 Ваши рекорды:\n{record_message}"
                await message.answer(out_message.strip(), reply_markup=keyboard)
            else:
                await message.answer("Главное меню", reply_markup=keyboard)
        else:
            await message.answer("Главное меню", reply_markup=keyboard)

        await scheduler_in(state_updates, state, message=message)
    else:
        await handle_new_user(message, state)


async def close_db_pool():
    """Закрывает пул соединений БД."""
    from sqlite import _pool

    if _pool:
        await _pool.close()


async def restore_notification_jobs(dp) -> int:
    """Восстанавливает jobs уведомлений для всех пользователей при старте бота.

    ВАЖНО: используем стабильный job id на пользователя, чтобы:
    - не плодить дубликаты после рестартов
    - при восстановлении всегда "переопределять" один и тот же job
    """
    from aiogram.fsm.storage.base import StorageKey

    from handlers.common import MessageProxy

    users = await get_users_with_notifications()
    restored = 0

    for user_info in users:
        try:
            user_id = int(user_info["user_id"])
            hours = user_info["hours"]
            minutes = user_info["minutes"]

            # Создаём MessageProxy для отправки сообщений
            message_proxy = MessageProxy(chat_id=user_id, from_user=None, bot=bot)
            message_proxy.from_user = type(
                "User", (), {"id": user_id, "full_name": "User"}
            )()

            # Получаем state для пользователя через storage
            storage_key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
            state = FSMContext(storage=dp.storage, key=storage_key)

            # Сохраняем user_id в state
            await state.update_data(user_id=user_id)

            job = ensure_notification_job(
                user_id=user_id,
                hours=hours,
                minutes=minutes,
                message=message_proxy,
                state=state,
            )

            # Сохраняем job_id в state (теперь он стабилен)
            await state.update_data(job_id=job.id)

            restored += 1
            logger.info(
                f"Restored notification job for user {user_id} at {hours}:{minutes:02d} (job_id={job.id})"
            )
        except Exception as e:
            logger.error(
                f"Failed to restore job for user {user_info['user_id']}: {e}",
                exc_info=True,
            )

    logger.info(f"Restored {restored} notification jobs")
    return restored


async def send_diary_analysis(message) -> None:
    """Считает и отправляет разбор дневника пользователю."""
    user_id = message.from_user.id
    logs = await get_all_logs(user_id)
    text = build_analysis(logs, today=datetime.now(TARGET_TZ).date())
    for chunk in _chunk_lines(text.split("\n")):
        await message.answer(chunk)
    await message.answer(
        'Полный список слов из записей — напишите «слова».'
    )


async def send_word_report(message) -> None:
    """Отправляет полный список частых слов из «о дне» с их влиянием."""
    logs = await get_all_logs(message.from_user.id)
    text = build_word_report(logs)
    for chunk in _chunk_lines(text.split("\n")):
        await message.answer(chunk)


async def weekly_analysis_broadcast() -> int:
    """Раз в неделю шлёт разбор тем, у кого включены напоминания.

    Пользователям без данных не пишем — незачем шуметь.
    """
    from handlers.common import MessageProxy

    users = await get_users_with_notifications()
    sent = 0
    today = datetime.now(TARGET_TZ).date()

    for user_info in users:
        try:
            user_id = int(user_info["user_id"])
        except (TypeError, ValueError, KeyError):
            continue
        try:
            logs = await get_all_logs(user_id)
            rated_days = sum(1 for row in logs if _to_float(row[5]) is not None)
            if rated_days < MIN_RATED_DAYS:
                continue
            message_proxy = MessageProxy(chat_id=user_id, from_user=None, bot=bot)
            text = build_analysis(logs, today=today)
            for chunk in _chunk_lines(text.split("\n")):
                await message_proxy.answer(chunk)
            sent += 1
        except Exception as e:
            logger.error(f"Weekly analysis failed for user {user_id}: {e}")

    logger.info(f"Weekly analysis sent to {sent} users")
    return sent


def ensure_weekly_analysis_job():
    """Регистрирует еженедельную рассылку разбора (воскресенье, 20:07 МСК)."""
    return scheduler.add_job(
        weekly_analysis_broadcast,
        trigger="cron",
        day_of_week="sun",
        hour=20,
        minute=7,
        id="weekly:analysis",
        replace_existing=True,
    )


async def restore_scheduler_jobs(dp) -> int:
    """Восстанавливает запланированные напоминания (scheduler_arguments) при старте.

    APScheduler держит jobs в памяти: без этого напоминания «в определённую дату»
    не срабатывали, пока пользователь сам не откроет бота (только тогда
    вызывался scheduler_in из start()).
    """
    from aiogram.fsm.storage.base import StorageKey

    from handlers.common import MessageProxy

    users = await get_users_with_scheduler_jobs()
    restored = 0

    for user_id_raw, scheduler_arguments in users:
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError):
            logger.warning(f"Skip scheduler restore for bad user_id: {user_id_raw!r}")
            continue
        if not scheduler_arguments:
            continue
        try:
            message_proxy = MessageProxy(chat_id=user_id, from_user=None, bot=bot)
            message_proxy.from_user = type(
                "User", (), {"id": user_id, "full_name": "User"}
            )()

            storage_key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
            state = FSMContext(storage=dp.storage, key=storage_key)
            await state.update_data(
                user_id=user_id, scheduler_arguments=scheduler_arguments
            )

            await scheduler_in(
                {"scheduler_arguments": scheduler_arguments},
                state,
                message=message_proxy,
            )
            restored += len(scheduler_arguments)
        except Exception as e:
            logger.error(
                f"Failed to restore scheduler jobs for user {user_id_raw}: {e}",
                exc_info=True,
            )

    logger.info(f"Restored {restored} scheduled reminders")
    return restored


async def executing_scheduler_job(state: FSMContext, out_message: str) -> None:
    # функция, которая срабатывает, когда срабатывает scheduler

    # Безопасно получаем название задачи из сообщения
    try:
        text_normalized = (
            normalize_preserve_case(out_message.split(" : ")[1])
            .replace('"', "")
            .replace(" - ", "-")
        )
    except (IndexError, AttributeError):
        logger.error(f"Error parsing job text from: {out_message}")
        return

    tmp = text_normalized.split("-")

    # Получаем данные как можно позже, чтобы уменьшить вероятность гонки
    user_states_data = await state.get_data()
    user_id = user_states_data.get("user_id", None)
    state_updates = {}

    if len(tmp) == 2:
        job, job_timing = (
            text_normalized.split("-")[0],
            text_normalized.split("-")[1].split(" ")[0],
        )
        today_tasks = user_states_data.get("today_tasks", {})
        if today_tasks.get(job_timing) != job:
            today_tasks[job_timing] = job
            state_updates["today_tasks"] = today_tasks
        # Если дело уже есть без времени — удаляем дубликат
        today_tasks_not_time = user_states_data.get("today_tasks_not_time", [])
        if job in today_tasks_not_time:
            today_tasks_not_time = [t for t in today_tasks_not_time if t != job]
            state_updates["today_tasks_not_time"] = today_tasks_not_time
    else:
        # Не добавляем дело без времени, если оно уже есть с временем
        today_tasks = user_states_data.get("today_tasks", {})
        if text_normalized in today_tasks.values():
            today_tasks_not_time = user_states_data.get("today_tasks_not_time", [])
            if text_normalized in today_tasks_not_time:
                today_tasks_not_time = [
                    t for t in today_tasks_not_time if t != text_normalized
                ]
                state_updates["today_tasks_not_time"] = today_tasks_not_time
        else:
            today_tasks_not_time = user_states_data.get("today_tasks_not_time", [])
            if text_normalized not in today_tasks_not_time:
                today_tasks_not_time.append(text_normalized)
                state_updates["today_tasks_not_time"] = today_tasks_not_time

    # Если это было разовое напоминание (trigger='date'), удаляем его из scheduler_arguments
    scheduler_arguments = user_states_data.get("scheduler_arguments", {})
    if (
        out_message in scheduler_arguments
        and scheduler_arguments[out_message].get("trigger") == "date"
    ):
        del scheduler_arguments[out_message]
        state_updates["scheduler_arguments"] = scheduler_arguments
        if user_id:
            await edit_database(
                scheduler_arguments=scheduler_arguments, user_id=user_id
            )

    if state_updates:
        await state.update_data(**state_updates)
    logger.info(
        f"Successfully added scheduled task '{text_normalized}' to daily_tasks for user {user_id}"
    )


async def counter_max_days(
    activity_history, message, activities, personal_records, output=""
):
    """Считает серии подряд идущих дней по выполненным делам и шлёт итог.

    Показываем только то, что человек ДЕЛАЕТ: сколько дней подряд держится
    каждое дело и побит ли личный рекорд. Дела, которые давно не делались,
    больше не считаем и не показываем.
    """
    if personal_records is None:
        personal_records = {}
    if not activity_history:
        await message.answer("Поздравляю! дневник заполнен")
        return personal_records

    streak_lines = []
    started_lines = []
    for current_word in dict.fromkeys(activities):
        streak = counter_positive(current_word=current_word, history=activity_history)
        if streak <= 0:
            continue
        previous_record = personal_records.get(current_word, 0)
        try:
            previous_record = int(previous_record)
        except (TypeError, ValueError):
            previous_record = 0
        if streak > previous_record:
            personal_records[current_word] = streak
            is_record = streak >= 2
        else:
            is_record = False
        if streak >= 2:
            line = f"{current_word} : {streak} {_plural_days(streak)}"
            if is_record:
                line += " 🏆 личный рекорд!"
            streak_lines.append(line)
        else:
            started_lines.append(current_word)

    if streak_lines:
        if output:
            output += "\n\n"
        output += "🔥 Вы держите эти дела уже:\n" + "\n".join(streak_lines)
    elif started_lines:
        if output:
            output += "\n\n"
        output += (
            "Отличное начало! Сегодня отмечено:\n"
            + "\n".join(started_lines)
            + "\n\nПовторите завтра — и пойдёт серия 🔥"
        )

    if output:
        send_message = await message.answer(output)
        try:
            # Снимаем предыдущий закреп, иначе они копятся по одному за день
            await message.bot.unpin_chat_message(message.chat.id)
        except Exception as e:
            logger.debug(f"Could not unpin previous message: {e}")
        try:
            await message.bot.pin_chat_message(message.chat.id, send_message.message_id)
        except Exception as e:
            logger.debug(f"Could not pin message: {e}")
    return personal_records


def format_records(records: Optional[Dict[str, Any]]) -> str:
    """Формирует список личных рекордов с правильным склонением дней."""
    if not records:
        return ""
    lines = []
    for key, value in records.items():
        try:
            days = int(value)
        except (TypeError, ValueError):
            continue
        if days <= 0:
            continue
        lines.append(f"{key} : {days} {_plural_days(days)}")
    return "\n".join(lines)


def generate_keyboard(
    buttons: List[str],
    last_button: Optional[str] = None,
    first_button: Optional[str] = None,
) -> types.ReplyKeyboardMarkup:
    # ✅️✔️

    if last_button is not None:
        kb = [
            [types.KeyboardButton(text=f"{button}") for button in buttons],
            [types.KeyboardButton(text=last_button)],
        ]
    elif first_button is not None:
        kb = [
            [types.KeyboardButton(text=first_button)],
            [types.KeyboardButton(text=f"{button}") for button in buttons],
        ]
    else:
        kb = [[types.KeyboardButton(text=f"{button}") for button in buttons]]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
    )
    return keyboard


def normalized(text: str) -> str:
    return re.sub(r",(?=\S)", ", ", text).strip().lower().replace("ё", "е")


def normalize_preserve_case(text: str) -> str:
    """Нормализует текст, сохраняя регистр букв."""
    return re.sub(r",(?=\S)", ", ", text).strip().replace("ё", "е")


def _get_rate_emoji(rate: int) -> str:
    """Возвращает эмодзи в зависимости от оценки дня."""
    if rate >= 9:
        return "🌟"
    elif rate >= 7:
        return "😊"
    elif rate >= 5:
        return "😐"
    elif rate >= 3:
        return "😔"
    else:
        return "😢"


def _chunk_lines(lines: List[str], limit: int = 4000) -> List[str]:
    """Собирает строки в блоки не длиннее limit, не разрывая строку посередине."""
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        # Одна строка длиннее лимита (длинное «о дне») — режем её отдельно
        while line_len > limit:
            chunks.append(line[:limit])
            line = line[limit:]
            line_len = len(line) + 1
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def _get_weekday_ru(date_str: str) -> str:
    """Возвращает день недели на русском."""
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return weekdays[dt.weekday()]
    except (TypeError, ValueError):
        return ""


@timed
async def diary_out(message: Message) -> None:
    logs = await get_last_logs(message.from_user.id, limit=7)
    logger.info(
        f"diary_out: user_id={message.from_user.id}, logs_count={len(logs) if logs else 0}"
    )
    if not logs:
        await message.answer(
            "📔 Дневник пуст\n\nНачните вести записи через «Заполнить Дневник»"
        )
        return

    lines = ["📔 <b>Ваш дневник</b>\n"]

    for (
        log_date,
        activities,
        steps,
        sleep_quality,
        about_day,
        personal_rate,
    ) in reversed(logs):
        try:
            formatted_date = datetime.strptime(log_date, "%Y-%m-%d").strftime("%d.%m")
        except (TypeError, ValueError):
            formatted_date = log_date

        weekday = _get_weekday_ru(log_date)
        rate_float = _to_float(personal_rate)
        rate = int(rate_float) if rate_float is not None else 0
        rate_emoji = _get_rate_emoji(rate)

        # Заголовок дня
        lines.append(f"{'─' * 20}")
        lines.append(
            f"📅 <b>{html_escape(str(formatted_date))}</b> ({weekday})  "
            f"{rate_emoji} <b>{rate}/10</b>"
        )

        # Статистика
        stats = []
        if steps is not None and steps != 0:
            steps_int = int(steps) if steps == int(steps) else steps
            stats.append(f"👣 {steps_int:,}".replace(",", " "))
        if sleep_quality is not None and sleep_quality != 0:
            stats.append(f"😴 {sleep_quality}")
        if stats:
            lines.append("   " + "  •  ".join(stats))

        # Дела
        if activities and activities != "-":
            acts = _activity_list(activities)
            if len(acts) <= 5:
                shown = ", ".join(acts)
            else:
                shown = f"{', '.join(acts[:5])} +{len(acts) - 5}"
            lines.append(f"   ✅ {html_escape(shown)}")

        # О дне (длинные записи подрезаем — полный текст всегда есть в Excel)
        if about_day and about_day != "-":
            about_text = str(about_day)
            if len(about_text) > 1000:
                about_text = about_text[:1000].rstrip() + "…"
            lines.append(f"   💬 <i>{html_escape(about_text)}</i>")

        lines.append("")

    lines.append(f"{'─' * 20}")

    # Режем по строкам, а не по символам: разрыв внутри тега ломает parse_mode=HTML
    for chunk in _chunk_lines(lines):
        await message.answer(chunk, parse_mode="HTML")


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


async def _fetch_with_retries(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    tries: int = 3,
    backoff: float = 0.5,
):
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.get(
                url, params=params, timeout=timeout, ssl=ssl_ctx
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            last_exc = e
            logger.debug("Fetch attempt %d failed for %s: %s", attempt, url, e)
            if attempt < tries:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
    raise last_exc


def _try_parse_sunset_string(
    s: str, assume_msk_when_naive: bool = True, date_for_time: Optional[str] = None
) -> Optional[datetime]:
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
                    return dt.replace(
                        tzinfo=MSK if assume_msk_when_naive else ZoneInfo("UTC")
                    )
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


@timed
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
                dt = _try_parse_sunset_string(
                    sunset_raw, assume_msk_when_naive=False, date_for_time=date
                )
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
                    sunset_raw = (
                        res.get("sunset")
                        or res.get("sunset_time")
                        or res.get("sunsetLocal")
                    )
                else:
                    sunset_raw = data2.get("sunset")
                dt = _try_parse_sunset_string(
                    sunset_raw, assume_msk_when_naive=True, date_for_time=date
                )
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
