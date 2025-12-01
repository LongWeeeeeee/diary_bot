"""Обработчики для напоминаний и дел по датам."""
import datetime
import json
import logging
from datetime import date
from datetime import datetime as dt

from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    bot, scheduler, ClientState, TARGET_TZ, has_user_data,
    WEEKDAY_TRANSLATE, day_to_prefix, MONTH_NAMES_RU
)
from functions import (
    generate_keyboard, keyboard_builder, generate_unique_id_from_args,
    start, scheduler_list, normalized
)
from sqlite import create_profile, edit_database

router = Router(name="scheduler")


def scheduler_display(key: str) -> str:
    """Извлекает отображаемое название задачи из ключа, убирая служебное время."""
    try:
        text = key.split('Я напомню вам : ')[1].replace('"', '')
        # Убираем "в 00:00" из конца для разовых дел
        if text.endswith(' в 00:00'):
            text = text[:-8]
        return text
    except IndexError:
        return key


# Все состояния настроек для навигации между пунктами
SETTINGS_STATES = (
    ClientState.settings, ClientState.collected_data, ClientState.notification_proceed, 
    ClientState.notification_set_date, ClientState.edit_tasks_pool, ClientState.one_time_tasks_2,
    ClientState.one_time_tasks_3, ClientState.date_jobs, ClientState.date_jobs_1, ClientState.date_jobs_2,
    ClientState.date_jobs_week, ClientState.date_jobs_month, ClientState.date_jobs_year, ClientState.date_jobs_once,
    ClientState.add_tasks_pool
)


@router.message(lambda message: message.text and message.text.lower() == 'в определенную дату', StateFilter(*SETTINGS_STATES))
async def date_jobs_keyboard(message: Message, state: FSMContext) -> None:
    """Меню дел в определенную дату."""
    user_data = await state.get_data()
    state_updates = {'message_ctx': {'chat_id': message.chat.id}}
    
    profile_row = await create_profile(user_id=message.from_user.id)
    if profile_row:
        scheduler_arguments = json.loads(profile_row[3])
        state_updates['scheduler_arguments'] = scheduler_arguments
    
    if has_user_data(user_data):
        await state.update_data(**state_updates)
        data = await state.get_data()
        if data.get('scheduler_arguments'):
            scheduler_keys = sorted(data['scheduler_arguments'].keys())
            display = [scheduler_display(key) for key in scheduler_keys]
            keyboard = keyboard_builder(tasks_list=display, chosen=[], add_dell=True)
            await state.update_data(date_jobs_keys=scheduler_keys, date_jobs_display=display)
            settings_buttons = ['Напоминания', 'В определенную дату', 'Опрашиваемые данные', 'Список дел', 'Разовые дела']
            await message.answer('Ваши задачи', reply_markup=keyboard)
            await message.answer(
                'Для удаления выберите интересующие вас дела и нажмите "Удалить"\n'
                '"Добавить" - если хотите добавить новую задачу',
                reply_markup=generate_keyboard(settings_buttons, last_button='В Главное Меню')
            )
            await state.set_state(ClientState.date_jobs)
        else:
            settings_buttons = ['Напоминания', 'В определенную дату', 'Опрашиваемые данные', 'Список дел', 'Разовые дела']
            await message.answer(
                'Введите новое дело и время через "-". Например:\n'
                'cходить на кружок - 18:00\n\n'
                'Если дело без времени, то впишите просто дело',
                reply_markup=generate_keyboard(settings_buttons, last_button='В Главное Меню')
            )
            await state.set_state(ClientState.date_jobs_1)
    else:
        await start(message=message, state=state)


@router.callback_query(StateFilter(ClientState.date_jobs))
async def date_jobs_keyboard_callback(call: types.CallbackQuery, state: FSMContext):
    """Обработка списка запланированных дел."""
    await call.answer()
    data = call.data

    if data == 'Удалить':
        await state.update_data(date_jobs_keyboard={
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id
        })
        user_data = await state.get_data()
        date_chosen_tasks = user_data.get('date_chosen_tasks', [])
        scheduler_arguments = user_data.get('scheduler_arguments', {})
        
        for itr in date_chosen_tasks:
            for key in list(scheduler_arguments.keys()):
                values = scheduler_arguments[key]
                values_copy = values.copy()
                values_copy['args'] = (state, key)
                if 'date' in values_copy:
                    values_copy['date'] = dt.strptime(values['date'], '%Y-%m-%d')
                elif 'run_date' in values_copy:
                    try:
                        values_copy['run_date'] = dt.strptime(values['run_date'], '%Y-%m-%d %H:%M')
                    except ValueError:
                        values_copy['run_date'] = dt.fromisoformat(values['run_date'])
                unique_id = generate_unique_id_from_args(values_copy)
                if any(job.id == unique_id for job in scheduler.get_jobs()):
                    scheduler.remove_job(job_id=unique_id)

            for key in list(scheduler_arguments.keys()):
                if key.split('Я напомню вам : ')[1].replace('"', '') == itr:
                    del scheduler_arguments[key]
                    break

        if len(scheduler_arguments) == 0:
            user_data.pop('scheduler_arguments', None)
            user_data.pop('date_jobs_keys', None)
            user_data.pop('date_jobs_display', None)
            # Убеждаемся что user_id сохраняется
            user_data['user_id'] = call.from_user.id
            new_ot_builder = InlineKeyboardBuilder()
            new_ot_builder.button(text="💼Добавить 💼", callback_data="Добавить")
            try:
                await bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=new_ot_builder.as_markup()
                )
            except TelegramBadRequest as exc:
                if 'message is not modified' not in str(exc).lower():
                    raise
            await state.set_data(user_data)
            await edit_database(scheduler_arguments={}, user_id=call.from_user.id)
        else:
            scheduler_keys = sorted(scheduler_arguments.keys())
            display = [scheduler_display(key) for key in scheduler_keys]
            keyboard = keyboard_builder(tasks_list=display, chosen=date_chosen_tasks, add_dell=True)
            try:
                await call.message.edit_reply_markup(reply_markup=keyboard)
            except TelegramBadRequest as exc:
                if 'message is not modified' not in str(exc).lower():
                    raise
            await state.update_data(
                scheduler_arguments=scheduler_arguments, date_chosen_tasks=[],
                date_jobs_keys=scheduler_keys, date_jobs_display=display
            )
            await edit_database(scheduler_arguments=scheduler_arguments, user_id=call.from_user.id)

    elif data == 'Добавить':
        await call.message.answer(
            'Введите новое дело и время через "-". Например:\n'
            'cходить на кружок - 18:00\n\n'
            'Если дело без времени, то впишите просто дело'
        )
        await state.update_data(date_jobs_keyboard={
            'chat_id': call.message.chat.id,
            'message_id': call.message.message_id
        })
        await state.set_state(ClientState.date_jobs_1)

    else:
        try:
            data = int(data)
        except (ValueError, TypeError):
            await call.answer("Некорректный выбор.", show_alert=True)
            return
        user_data = await state.get_data()
        scheduler_keys = user_data.get('date_jobs_keys', []) or sorted(user_data.get('scheduler_arguments', {}).keys())
        scheduler_arguments = user_data.get('date_jobs_display', []) or [scheduler_display(key) for key in scheduler_keys]
        date_chosen_tasks = user_data.get('date_chosen_tasks', [])
        
        if data < 0 or data >= len(scheduler_arguments):
            await call.answer("Задача не найдена.", show_alert=True)
            return
        if scheduler_arguments[data] in date_chosen_tasks:
            date_chosen_tasks.remove(scheduler_arguments[data])
        else:
            date_chosen_tasks.append(scheduler_arguments[data])
        
        await state.update_data(date_chosen_tasks=date_chosen_tasks)
        keyboard = keyboard_builder(tasks_list=scheduler_arguments, chosen=date_chosen_tasks, add_dell=True)
        try:
            await call.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise


# Кнопки настроек для проверки
SETTINGS_BUTTONS = [
    'в главное меню', 'напоминания', 'в определенную дату', 
    'опрашиваемые данные', 'список дел', 'разовые дела', 'настройки'
]


@router.message(StateFilter(ClientState.date_jobs_1))
async def change_date_jobs_job(message: Message, state: FSMContext) -> None:
    """Ввод нового дела для напоминания."""
    if not message.text:
        await message.answer('Введите название дела.')
        return
    
    # Проверка на кнопки настроек
    if message.text.lower() in SETTINGS_BUTTONS:
        if message.text.lower() == 'в главное меню':
            await start(message=message, state=state)
        elif message.text.lower() == 'настройки':
            from handlers.settings import settings
            await settings(message=message, state=state)
        elif message.text.lower() == 'напоминания':
            from handlers.settings import notifications
            await notifications(message=message, state=state)
        elif message.text.lower() == 'опрашиваемые данные':
            from handlers.settings import collected_data
            await collected_data(message=message, state=state)
        elif message.text.lower() == 'список дел':
            from handlers.tasks import edit_tasks_pool_handler
            await edit_tasks_pool_handler(message=message, state=state)
        elif message.text.lower() == 'разовые дела':
            from handlers.tasks import change_one_time_tasks
            await change_one_time_tasks(message=message, state=state)
        elif message.text.lower() == 'в определенную дату':
            await date_jobs_keyboard(message=message, state=state)
        return
    
    await state.update_data(new_date_jobs=message.text)
    keyboard = generate_keyboard(['В день недели', 'Число месяца', 'Каждый год', 'Разово'])
    await message.answer(
        'Выберите как и когда вы бы желали чтобы вам напомнили об этом деле',
        reply_markup=keyboard
    )
    await state.set_state(ClientState.date_jobs_2)


@router.message(StateFilter(ClientState.date_jobs_2))
async def date_jobs_job_2(message: Message, state: FSMContext) -> None:
    """Выбор типа напоминания."""
    user_message = normalized(message.text)
    
    if user_message == 'в день недели':
        keyboard = keyboard_builder(
            tasks_list=['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье'],
            grid=1, add_dell=False, price_tag=False, chosen=[], last_button="🚀Отправить 🚀"
        )
        await message.answer('В какой день недели?', reply_markup=keyboard)
        await message.answer('можно выбрать сразу несколько', reply_markup=generate_keyboard(buttons=['В Главное Меню']))
        await state.set_state(ClientState.date_jobs_week)
    
    elif user_message == 'число месяца':
        await message.answer('Какого числа месяца вам нужно напомнить об этом деле?')
        await state.set_state(ClientState.date_jobs_month)
    
    elif user_message == 'каждый год':
        await message.answer('Введите дату когда вам о нем напомнить в формате день-месяц, например:')
        next_day = date.today()
        await message.answer(next_day.strftime("%d-%m"))
        await state.set_state(ClientState.date_jobs_year)
    
    elif user_message == 'разово':
        await message.answer('Введите дату когда вам о нем напомнить в формате год-месяц-день, например:')
        await message.answer(str(date.today()))
        await state.set_state(ClientState.date_jobs_once)


@router.callback_query(StateFilter(ClientState.date_jobs_week))
async def date_jobs_week(call: types.CallbackQuery, state: FSMContext) -> None:
    """Выбор дней недели для напоминания."""
    await call.answer()
    data = call.data
    user_data = await state.get_data()
    date_jobs_week_list = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье']
    date_jobs_week_chosen_tasks = user_data.get('date_jobs_week_chosen_tasks', [])
    
    if data == 'Отправить':
        if len(date_jobs_week_chosen_tasks) != 0:
            new_date_jobs = user_data.get('new_date_jobs', {})
            for day in date_jobs_week_chosen_tasks:
                day_of_week = WEEKDAY_TRANSLATE[day]
                out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(day)} {day}'
                await scheduler_list(
                    call, state, out_message, user_data, 
                    trigger="cron", day_of_week=day_of_week, args=new_date_jobs
                )
            all_days = ''.join(f'\n{day_to_prefix(day)} {day}' for day in date_jobs_week_chosen_tasks)
            out_message = f'Я напомню вам "{new_date_jobs}":{all_days}'
            await call.message.answer(out_message)
            await state.update_data(date_jobs_week_chosen_tasks=[])
            # Используем MessageProxy с правильным from_user
            from .common import MessageProxy
            message_proxy = MessageProxy(chat_id=call.message.chat.id, from_user=call.from_user, bot=bot)
            await start(message=message_proxy, state=state)
    else:
        try:
            idx = int(data)
            if idx < 0 or idx >= len(date_jobs_week_list):
                await call.answer("День не найден.", show_alert=True)
                return
            data = date_jobs_week_list[idx]
        except (ValueError, TypeError):
            await call.answer("Некорректный выбор.", show_alert=True)
            return
        if data in date_jobs_week_chosen_tasks:
            date_jobs_week_chosen_tasks.remove(data)
        else:
            date_jobs_week_chosen_tasks.append(data)

        keyboard = keyboard_builder(
            tasks_list=date_jobs_week_list, chosen=date_jobs_week_chosen_tasks,
            grid=1, add_dell=False, price_tag=False, last_button="🚀Отправить 🚀"
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)
        await state.update_data(date_jobs_week_chosen_tasks=date_jobs_week_chosen_tasks)


@router.message(StateFilter(ClientState.date_jobs_month))
async def date_jobs_month(message: Message, state: FSMContext) -> None:
    """Напоминание по числу месяца."""
    if not message.text:
        await message.answer('Введите число от 1 до 31.')
        return
    try:
        day_num = int(message.text)
        if day_num < 1 or day_num > 31:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer('Введите корректное число от 1 до 31.')
        return
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', {})
    day_of_month = message.text
    out_message = f'Я напомню вам : "{new_date_jobs}" каждый {day_of_month} день месяца'
    await scheduler_list(message, state, out_message, user_data, day=day_of_month, trigger="cron", args=new_date_jobs)
    await message.answer(f'Готово! Буду напоминать про "{new_date_jobs}" {day_of_month}-го числа каждого месяца.')
    await start(message=message, state=state)


@router.message(StateFilter(ClientState.date_jobs_year))
async def date_jobs_year(message: Message, state: FSMContext) -> None:
    """Ежегодное напоминание."""
    if not message.text:
        await message.answer('Введите дату в формате день-месяц, например: 15-06')
        return
    try:
        parsed_date = dt.strptime(message.text, '%d-%m')
    except ValueError:
        await message.answer('Неверный формат. Используйте день-месяц, например: 15-06')
        return
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', {})
    out_message = f'Я напомню вам : "{new_date_jobs}" каждое {parsed_date.day} {parsed_date.strftime("%B")}'
    await scheduler_list(
        message, state, out_message, user_data,
        trigger="cron", day=parsed_date.day, month=parsed_date.month, args=new_date_jobs
    )
    await message.answer(f'Отлично! Буду напоминать про "{new_date_jobs}" каждый год {parsed_date.day}-{parsed_date.month:02d}.')
    await start(message=message, state=state)


@router.message(StateFilter(ClientState.date_jobs_once))
async def date_jobs_once(message: Message, state: FSMContext) -> None:
    """Разовое напоминание."""
    user_data = await state.get_data()
    new_date_jobs = user_data.get('new_date_jobs', 'Напоминание')

    try:
        user_date_part = dt.strptime(message.text, '%Y-%m-%d').date()
    except ValueError:
        await message.answer('Неверный формат даты. Используйте ГГГГ-ММ-ДД, например, 2025-12-31.')
        return

    # Парсим время из new_date_jobs если указано (формат "дело - ЧЧ:ММ")
    task_time = datetime.time(0, 0)
    if ' - ' in new_date_jobs:
        parts = new_date_jobs.split(' - ')
        time_str = parts[-1].strip()
        try:
            parsed_time = dt.strptime(time_str, '%H:%M')
            task_time = datetime.time(parsed_time.hour, parsed_time.minute)
        except ValueError:
            pass  # Если не удалось распарсить время, используем 00:00

    now_aware = dt.now(TARGET_TZ)
    naive_dt = datetime.datetime.combine(user_date_part, task_time)
    scheduled_dt_aware = naive_dt.replace(tzinfo=TARGET_TZ)

    if now_aware < scheduled_dt_aware:
        month_ru = MONTH_NAMES_RU[scheduled_dt_aware.month - 1]
        # Не добавляем время в сообщение если оно 00:00
        if task_time == datetime.time(0, 0):
            out_message = (
                f'Я напомню вам : "{new_date_jobs}" {scheduled_dt_aware.day} {month_ru} '
                f'{scheduled_dt_aware.year}'
            )
        else:
            out_message = (
                f'Я напомню вам : "{new_date_jobs}" {scheduled_dt_aware.day} {month_ru} '
                f'{scheduled_dt_aware.year}'
            )

        try:
            await scheduler_list(
                message, state, out_message, user_data,
                trigger="date", run_date=scheduled_dt_aware.isoformat(), args=new_date_jobs
            )
        except Exception as e:
            logging.error("Failed to schedule one-time reminder", exc_info=e)
            await message.answer("Не удалось запланировать напоминание.")
            return

        await message.answer(f'Отлично! Напомню про "{new_date_jobs}" {scheduled_dt_aware.strftime("%d.%m.%Y")}')
        await start(message=message, state=state)
    else:
        await message.answer(
            f'Рассчитанное время {scheduled_dt_aware.strftime("%Y-%m-%d %H:%M %Z%z")} уже в прошлом.'
        )
