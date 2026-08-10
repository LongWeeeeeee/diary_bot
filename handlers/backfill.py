"""Заполнение пропущенных дней дневника.

Обычный поток пишет запись за сегодня или за вчера. Здесь пользователь выбирает
конкретную пропущенную дату (кнопкой или вводом), отмечает дела за тот день и
отвечает на те же вопросы; запись уходит в daily_logs именно за эту дату.

Состояние держим в отдельных ключах backfill_* — today_tasks_* трогать нельзя,
иначе заполнение старого дня собьёт отметки за сегодня.
"""
import logging
import os
from datetime import datetime

from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    ABOUT_DAY_PROMPT, BACKFILL_MAX_BUTTONS, BACKFILL_WINDOW_DAYS,
    ClientState, bot
)
from functions import (
    BACKFILL_STATE_KEYS, _plural_days, _to_float, build_day_schedule, day_label,
    dedupe_preserve_order, diary_excel_path, export_diary_excel, keyboard_builder,
    main_menu_keyboard, missed_days, parse_user_date, refresh_personal_records,
    start
)
from sqlite import add_daily_log, has_daily_log

from .common import MessageProxy

logger = logging.getLogger(__name__)

router = Router(name="backfill")

CANCEL_WORDS = {'в главное меню', 'отмена', 'назад'}
DATE_HINT = 'Пришлите дату: 04.08, 04.08.2026 или 2026-08-04'


def _clear_backfill() -> dict:
    """Значения для сброса незавершённого заполнения."""
    return {key: None for key in BACKFILL_STATE_KEYS}


def dates_keyboard(dates) -> types.InlineKeyboardMarkup:
    """Кнопки с пропущенными датами."""
    builder = InlineKeyboardBuilder()
    for iso in dates:
        builder.button(text=day_label(iso), callback_data=f"bf:{iso}")
    builder.adjust(2)
    return builder.as_markup()


def _tasks_keyboard(tasks, tasks_not_time, chosen) -> types.InlineKeyboardMarkup:
    return keyboard_builder(
        tasks_dict=tasks,
        tasks_list=tasks_not_time,
        chosen=chosen,
        grid=1,
        last_button="🚀Отправить 🚀",
    )


async def show_missed_days(message, state: FSMContext) -> None:
    """Показывает пропущенные дни и переводит в режим выбора даты."""
    user_id = str(message.from_user.id)
    missed = await missed_days(user_id, limit=BACKFILL_MAX_BUTTONS)
    await state.update_data(**_clear_backfill())
    await state.set_state(ClientState.backfill_pick)

    if missed:
        await message.answer(
            'Какой день заполняем?\n'
            f'Показаны пропуски за последние {BACKFILL_WINDOW_DAYS} дней. '
            f'День постарше — {DATE_HINT.lower()}',
            reply_markup=dates_keyboard(missed),
        )
    else:
        await message.answer(
            f'Пропущенных дней за последние {BACKFILL_WINDOW_DAYS} дней нет 👍\n'
            f'Если нужен день постарше — {DATE_HINT.lower()}'
        )


async def _start_day(message, state: FSMContext, user_id, iso: str) -> None:
    """Начинает заполнение конкретной даты: сначала дела, потом обычные вопросы."""
    try:
        target_day = datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        await message.answer('Не понял дату, попробуйте ещё раз.')
        return

    tasks, tasks_not_time = await build_day_schedule(user_id, target_day)
    await state.update_data(
        backfill_date=iso,
        backfill_tasks=tasks,
        backfill_tasks_not_time=tasks_not_time,
        backfill_chosen=[],
        backfill_not_time_chosen=[],
    )

    if tasks or tasks_not_time:
        await message.answer(
            f'📆 {day_label(iso)}\nОтметьте дела, которые сделали в этот день',
            reply_markup=_tasks_keyboard(tasks, tasks_not_time, []),
        )
        await state.set_state(ClientState.backfill_tasks)
    else:
        await _ask_first_question(message, state, iso)


async def _ask_first_question(message, state: FSMContext, iso: str) -> None:
    """Первый вопрос анкеты — с учётом настройки «опрашиваемые данные»."""
    user_data = await state.get_data()
    collected_data = user_data.get('chosen_collected_data', []) or []
    prefix = f'📆 {day_label(iso)}\n'

    if 'Шаги' in collected_data:
        await message.answer(f'{prefix}Сколько сделал шагов?')
        await state.set_state(ClientState.steps)
    elif 'Сон' in collected_data:
        await state.update_data(my_steps='-')
        await message.answer(f'{prefix}Введите индекс качества сна')
        await state.set_state(ClientState.total_sleep)
    else:
        await state.update_data(my_steps='-', sleep_quality='-')
        await message.answer(f'{prefix}{ABOUT_DAY_PROMPT}')
        await state.set_state(ClientState.about_day)


@router.callback_query(StateFilter(ClientState.backfill_pick))
async def backfill_pick_callback(call: types.CallbackQuery, state: FSMContext) -> None:
    """Выбор даты кнопкой и подтверждение перезаписи."""
    await call.answer()
    data = call.data or ''
    chat_id = call.message.chat.id if call.message else call.from_user.id
    # from_user у call.message — это бот, поэтому автора берём из самого call
    message = MessageProxy(chat_id=chat_id, from_user=call.from_user, bot=bot)

    if data == 'bf:cancel':
        await state.update_data(**_clear_backfill())
        await state.set_state(ClientState.greet)
        await start(message=message, state=state)
        return

    if data.startswith('bf:ow:'):
        await _start_day(message, state, call.from_user.id, data[len('bf:ow:'):])
        return

    if not data.startswith('bf:'):
        return

    iso = data[len('bf:'):]
    if await has_daily_log(str(call.from_user.id), iso):
        await message.answer(
            f'За {day_label(iso)} запись уже есть. Перезаписать?',
            reply_markup=_confirm_overwrite_keyboard(iso),
        )
        return
    await _start_day(message, state, call.from_user.id, iso)


def _confirm_overwrite_keyboard(iso: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='Перезаписать', callback_data=f'bf:ow:{iso}')
    builder.button(text='Отмена', callback_data='bf:cancel')
    builder.adjust(2)
    return builder.as_markup()


@router.message(StateFilter(ClientState.backfill_pick))
async def backfill_manual_date(message: Message, state: FSMContext) -> None:
    """Дата, введённая сообщением (в том числе старше окна кнопок)."""
    text = (message.text or '').strip()
    if not text:
        await message.answer(DATE_HINT)
        return

    if text.lower() in CANCEL_WORDS:
        await state.update_data(**_clear_backfill())
        await state.set_state(ClientState.greet)
        await start(message=message, state=state)
        return

    target_day = parse_user_date(text)
    if target_day is None:
        await message.answer(
            f'Не понял дату. {DATE_HINT}\n'
            'Будущие дни заполнять нельзя.'
        )
        return

    iso = target_day.isoformat()
    if await has_daily_log(str(message.from_user.id), iso):
        await message.answer(
            f'За {day_label(iso)} запись уже есть. Перезаписать?',
            reply_markup=_confirm_overwrite_keyboard(iso),
        )
        return
    await _start_day(message, state, message.from_user.id, iso)


@router.callback_query(StateFilter(ClientState.backfill_tasks))
async def backfill_tasks_callback(call: types.CallbackQuery, state: FSMContext) -> None:
    """Отметка дел за выбранный день."""
    await call.answer()
    user_data = await state.get_data()
    chat_id = call.message.chat.id if call.message else call.from_user.id
    message = MessageProxy(chat_id=chat_id, from_user=call.from_user, bot=bot)

    iso = user_data.get('backfill_date')
    if not iso:
        await show_missed_days(message, state)
        return

    tasks = user_data.get('backfill_tasks', {}) or {}
    tasks_not_time = user_data.get('backfill_tasks_not_time', []) or []
    chosen = user_data.get('backfill_chosen', []) or []
    not_time_chosen = user_data.get('backfill_not_time_chosen', []) or []
    data = call.data or ''

    if data == 'Отправить':
        await _ask_first_question(message, state, iso)
        return

    if ':' in data:
        if data not in tasks:
            await call.answer('Дело не найдено, откройте список заново.', show_alert=True)
            return
        if data in chosen:
            chosen.remove(data)
        else:
            chosen.append(data)
        await state.update_data(backfill_chosen=chosen)
    else:
        try:
            index = int(data)
        except (TypeError, ValueError):
            await call.answer('Некорректный выбор.', show_alert=True)
            return
        if not 0 <= index < len(tasks_not_time):
            await call.answer('Дело не найдено, откройте список заново.', show_alert=True)
            return
        task = tasks_not_time[index]
        if task in not_time_chosen:
            not_time_chosen.remove(task)
        else:
            not_time_chosen.append(task)
        await state.update_data(backfill_not_time_chosen=not_time_chosen)

    try:
        await call.message.edit_reply_markup(
            reply_markup=_tasks_keyboard(tasks, tasks_not_time, chosen + not_time_chosen)
        )
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


async def finish_backfill(message, state: FSMContext) -> None:
    """Сохраняет ответы анкеты как запись за выбранный пропущенный день.

    После записи пересчитываем рекорды (закрытая дыра удлиняет серию) и заново
    шлём главное меню: счётчик пропусков живёт в reply-клавиатуре, без новой
    отправки Telegram продолжает показывать старое число.
    """
    user_data = await state.get_data()
    iso = user_data.get('backfill_date')
    if not iso:
        return

    user_id = message.from_user.id
    tasks = user_data.get('backfill_tasks', {}) or {}
    tasks_not_time = user_data.get('backfill_tasks_not_time', []) or []
    activities = [
        tasks[time_key]
        for time_key in (user_data.get('backfill_chosen') or [])
        if time_key in tasks
    ]
    activities += [
        task
        for task in (user_data.get('backfill_not_time_chosen') or [])
        if task in tasks_not_time
    ]
    activities = dedupe_preserve_order(activities)

    await add_daily_log(
        user_id=user_id,
        date=iso,
        activities=', '.join(activities),
        steps=_to_float(user_data.get('my_steps', 0)),
        sleep_quality=_to_float(user_data.get('sleep_quality', 0)),
        about_day=user_data.get('user_message', ''),
        personal_rate=_to_float(user_data.get('personal_rate')),
    )
    await export_diary_excel(user_id)
    records, improved = await refresh_personal_records(user_id)
    await state.update_data(personal_records=records, **_clear_backfill())
    logger.info(f"backfill saved: user={user_id}, date={iso}, activities={len(activities)}")

    rate = user_data.get('personal_rate')
    answer = f'✅ Записал день {day_label(iso)} — {rate}/10'
    if activities:
        answer += f"\nДела: {', '.join(activities)}"
    for activity, streak in improved.items():
        answer += f'\n🏆 Личный рекорд: {activity} — {streak} {_plural_days(streak)}'

    # Счётчик пропусков живёт в reply-клавиатуре — шлём её заново, иначе на
    # кнопке останется число, посчитанное до заполнения
    left = await missed_days(str(user_id))
    await message.answer(
        answer,
        reply_markup=main_menu_keyboard(
            has_diary=os.path.exists(diary_excel_path(user_id)),
            missed_count=len(left),
        ),
    )

    # Пропусков обычно несколько — сразу предлагаем следующий
    if left:
        await message.answer(
            'Остались пропущенные дни — выберите следующий или вернитесь в меню:',
            reply_markup=dates_keyboard(left[:BACKFILL_MAX_BUTTONS]),
        )
        await state.set_state(ClientState.backfill_pick)
    else:
        await state.set_state(ClientState.greet)
        await start(message=message, state=state)
