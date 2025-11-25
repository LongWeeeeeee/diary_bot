"""Обработчики настроек."""
from aiogram import Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import bot, scheduler, ClientState, has_user_data
from functions import generate_keyboard, keyboard_builder, start, tasks_pool_function
from sqlite import edit_database

from .common import MessageProxy

router = Router(name="settings")


@router.message(lambda message: message.text and message.text.lower() == 'настройки')
async def settings(message: Message, state: FSMContext = None) -> None:
    """Меню настроек."""
    user_data = await state.get_data()
    if has_user_data(user_data):
        inp = [
            'Напоминания', 
            'Дела в определенную дату', 
            'Опрашиваемые данные', 
            'Редактировать список дел', 
            'Разовые дела'
        ]
        if 'personal_records' in user_data:
            inp.append('Мои рекорды')

        keyboard = generate_keyboard(buttons=inp, last_button='В Главное Меню')
        await message.answer(text='Ваши Настройки', reply_markup=keyboard)
        await state.set_state(ClientState.settings)
    else:
        await start(message=message, state=state)


@router.message(lambda message: message.text and message.text.lower() == 'мои рекорды', StateFilter(ClientState.settings))
async def my_records(message: Message, state: FSMContext) -> None:
    """Показ рекордов пользователя."""
    user_data = await state.get_data()
    if user_data:
        personal_records = user_data.get('personal_records', {})
        output = [f'{key} : {value}' for key, value in personal_records.items()]
        await message.answer('Ваши рекорды:\n' + '\n'.join(output))
    else:
        await start(message=message, state=state)


@router.message(lambda message: message.text and message.text.lower() == 'опрашиваемые данные', StateFilter(ClientState.settings))
async def collected_data(message: Message, state: FSMContext) -> None:
    """Настройка опрашиваемых данных (шаги, сон)."""
    user_data = await state.get_data()
    chosen_collected_data = user_data.get('chosen_collected_data', [])
    keyboard = keyboard_builder(
        tasks_list=['Шаги', 'Сон'], 
        add_dell=False, 
        chosen=chosen_collected_data, 
        grid=2, 
        price_tag=False
    )
    await message.answer(
        reply_markup=keyboard, 
        text='Зеленая галочка означает что настройки будут работать, серая - что выключены'
    )
    await state.set_state(ClientState.collected_data)


@router.callback_query(StateFilter(ClientState.collected_data))
async def collected_data_proceed(call, state):
    """Переключение опрашиваемых данных."""
    await call.answer()
    data = int(call.data)
    user_data = await state.get_data()
    options = ['Шаги', 'Сон']
    
    chosen_collected_data = user_data.get('chosen_collected_data', [])
    if options[data] in chosen_collected_data:
        chosen_collected_data.remove(options[data])
    else:
        chosen_collected_data.append(options[data])
    
    await state.update_data(chosen_collected_data=chosen_collected_data, daily_chosen_tasks=[])
    await edit_database(chosen_collected_data=chosen_collected_data, user_id=call.from_user.id)
    
    keyboard = keyboard_builder(
        tasks_list=options, 
        chosen=chosen_collected_data,
        add_dell=False, 
        grid=2
    )
    await bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard
    )


@router.message(lambda message: message.text and message.text.lower() == 'напоминания', StateFilter(ClientState.settings))
async def notifications(message: Message, state: FSMContext) -> None:
    """Настройка ежедневных напоминаний."""
    user_data = await state.get_data()
    await state.update_data(message_ctx={'chat_id': message.chat.id})
    notifications_data = user_data.get('notifications_data', {})
    chosen_notifications = notifications_data.get('chosen_notifications', [])
    
    date_builder = InlineKeyboardBuilder()
    if 'Включено' in chosen_notifications:
        date_builder.button(text="Включено ✅️", callback_data="0")
    else:
        date_builder.button(text="Включено ✔️", callback_data="0")
    date_builder.button(text="Выбрать дату", callback_data="1")
    date_builder.adjust(2, 1)
    
    hours = int(notifications_data.get('hours', 9))
    minutes = int(notifications_data.get('minutes', 0))
    await message.answer(
        reply_markup=date_builder.as_markup(),
        text=f'Текущее время ежедневных уведомлений {hours}:{minutes:02d}'
    )
    await state.set_state(ClientState.notification_proceed)


@router.callback_query(StateFilter(ClientState.notification_proceed))
async def notifications_proceed(call, state):
    """Обработка настроек напоминаний."""
    await call.answer()
    data = int(call.data)
    user_data = await state.get_data()
    message_ctx = user_data.get('message_ctx', {})
    message_proxy = MessageProxy(
        chat_id=message_ctx.get('chat_id', call.message.chat.id),
        from_user=call.from_user,
        bot=bot
    )
    notifications_data = user_data.get('notifications_data', {})
    hours = int(notifications_data.get('hours', 9))
    minutes = int(notifications_data.get('minutes', 0))
    
    if data == 0:
        # Переключение вкл/выкл
        chosen_notifications = notifications_data.get('chosen_notifications', [])
        if 'Включено' in chosen_notifications:
            notifications_data['chosen_notifications'] = []
        else:
            notifications_data['chosen_notifications'] = ['Включено']
        
        await state.update_data(notifications_data=notifications_data)
        await edit_database(notifications_data=notifications_data, user_id=call.from_user.id)
        
        date_builder = InlineKeyboardBuilder()
        if notifications_data['chosen_notifications']:
            date_builder.button(text="Включено ✅️", callback_data="0")
        else:
            date_builder.button(text="Включено ✔️", callback_data="0")
        date_builder.button(text="Выбрать дату", callback_data="1")
        date_builder.adjust(2, 1)

        if notifications_data['chosen_notifications'] == ['Включено']:
            job_id = scheduler.add_job(
                tasks_pool_function,
                trigger='cron',
                hour=hours,
                minute=minutes,
                args=(message_proxy, state)
            )
            await state.update_data(job_id=job_id.id)
        else:
            job_id = user_data.get('job_id', '')
            if job_id:
                scheduler.remove_job(job_id=job_id)
                await state.update_data(job_id='')
        
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=date_builder.as_markup()
        )
    
    elif data == 1:
        await call.message.answer('Введите время ежедневных уведомлений в формате часы:минуты')
        await state.set_state(ClientState.notification_set_date)


@router.message(StateFilter(ClientState.notification_set_date))
async def notification_set_date(message, state):
    """Установка времени напоминаний."""
    user_data = await state.get_data()
    notifications_data = user_data.get('notifications_data', {})
    notification_time = message.text.split(':')
    
    if len(notification_time) != 2:
        await message.answer(f'{message.text} должно быть временем, например 14:20')
        return
    
    try:
        hours = int(notification_time[0])
        minutes = int(notification_time[1])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            await message.answer('Часы должны быть 0-23, минуты 0-59.')
            return
    except ValueError:
        await message.answer('Часы и минуты должны быть числами.')
        return
    
    notifications_data['hours'] = hours
    notifications_data['minutes'] = minutes
    await edit_database(notifications_data=notifications_data, user_id=message.from_user.id)
    await state.update_data(notifications_data=notifications_data)
    
    job_id = user_data.get('job_id', '')
    if job_id:
        scheduler.remove_job(job_id=job_id)
    
    job_id = scheduler.add_job(
        tasks_pool_function,
        trigger='cron',
        hour=hours,
        minute=minutes,
        args=(message, state)
    )
    notifications_data['chosen_notifications'] = ['Включено']
    await state.update_data(job_id=job_id.id, notifications_data=notifications_data)
    await edit_database(notifications_data=notifications_data, user_id=message.from_user.id)
    await message.answer(f'Отлично! Теперь напоминания будут приходить каждый день в {hours}:{minutes:02d}')
    await start(message=message, state=state)
