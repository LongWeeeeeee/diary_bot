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
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import keys
from sqlite import database_start, create_profile, edit_database
from functions import generate_keyboard, diary_out, add_day_to_excel, normalized, day_to_prefix

bot = Bot(token=keys.Token)
dp = Dispatcher()
already_started = False
start = True
locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
remove_markup = types.ReplyKeyboardRemove()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
negative_responses = {'не', 'нет', '-', 'pass', 'пасс', 'не хочу', 'скип', 'неа', 'не-а', '0', 0}
translate = {'понедельник': 'mon', 'вторник': 'tue', 'среда': 'wed', 'четверг': 'thu', 'пятница': 'fri',
             'суббота': 'sat',
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
    jobs = State()
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
        if answer[3] != '{}':
            scheduler = json.loads(answer[3])
            data['scheduler_arguments'] = scheduler
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
    keyboard = generate_keyboard(["Изменить Дела", "Скачать дневник", "Заполнить дневник"])
    await message.answer(text='Здесь вы можете изменить свои настройки', reply_markup=keyboard)
    await state.set_state(ClientState.settings)


@dp.message(F.text == 'Изменить Дела', ClientState.settings)
async def jobs_change(message: Message, state: FSMContext) -> None:
    keyboard = generate_keyboard(["Ежедневные дела", "Разовые дела", 'В определенную дату'])
    await message.answer(text='Выберите интересующее вас дело', reply_markup=keyboard)
    await state.set_state(ClientState.jobs)


@dp.message(F.text == 'В определенную дату', ClientState.jobs)
async def date_jobs_0(message: Message, state: FSMContext) -> None:
    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    data = await state.get_data()
    if 'scheduler_arguments' in data:
        output = data['scheduler_arguments']
        output_list = list(output.keys())
        numbered_list = [f"{i + 1}. {output_list[i]}" for i in range(len(output_list))]
        await message.answer('Ваш предыдущий список дел:')
        await message.answer("\n".join(numbered_list))
        await message.answer('Вы хотите добавить или удалить дело?', reply_markup=generate_keyboard(['Добавить', 'Удалить']))
        await state.set_state(ClientState.date_jobs_1)
    else:
        await message.answer('Введите запланированную задачу', reply_markup=remove_markup)
        await state.set_state(ClientState.date_jobs)

@dp.message(F.text == 'Добавить', ClientState.date_jobs_1)
@dp.message(F.text == 'Удалить', ClientState.date_jobs_1)
async def date_jobs_1(message: Message, state: FSMContext) -> None:
    normalized_message = normalized(message.text)
    if normalized_message == 'добавить':
        await message.answer('Введите новое дело', reply_markup=remove_markup)
        await state.set_state(ClientState.date_jobs)

    elif normalized_message == 'удалить':
        await message.answer('Введите номер задачи, которую хотели бы удалить', reply_markup=remove_markup)
        await state.set_state(ClientState.del_date_job)



@dp.message(ClientState.del_date_job)
async def del_date_job(message: Message, state: FSMContext) -> None:
    try:
        num = int(message.text)
        data = await state.get_data()
        scheduler_arguments = data['scheduler_arguments']
        keys_list = list(scheduler_arguments.keys())
        del scheduler_arguments[keys_list[num-1]]
        if len(scheduler_arguments) == 0:
            scheduler_arguments = '{}'
        await edit_database(scheduler_arguments=scheduler_arguments)
        await start(message, state)
    except ValueError:
        await message.answer('Введите номер задачи, которую хотели бы удалить', reply_markup=remove_markup)

@dp.message(ClientState.date_jobs)
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
        next_day = datetime.date.today() + timedelta(days=1)
        await message.answer(next_day.strftime("%d-%m"))
        await state.set_state(ClientState.date_jobs_year)
    elif user_message == 'разово':
        await message.answer(
            'Введите дату когда вам о нем напомнить в формате год-месяц-день, например:', reply_markup=remove_markup)
        await message.answer(str(datetime.date.today() + timedelta(days=1)))
        await state.set_state(ClientState.date_jobs_once)


async def scheduler_list(message, state, out_message, user_states_data, **kwargs):
    #загрузка аргументов в database
    await message.answer(out_message)
    try:
        scheduler_arguments = user_states_data['scheduler_arguments']
        scheduler_arguments[out_message] = {**kwargs}
    except KeyError:
        scheduler_arguments = {out_message: {**kwargs}}
    await edit_database(scheduler_arguments = scheduler_arguments)
    await state.update_data(scheduler_arguments = scheduler_arguments)


@dp.message(ClientState.date_jobs_week)
async def date_jobs_week(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    day_of_week = translate[user_message]
    scheduler.add_job(executing_scheduler_job, trigger="cron", hour=7, minute=50, day_of_week=day_of_week,
                      args=(state, new_date_jobs))
    out_message = f'Я напомню вам : "{new_date_jobs}" {day_to_prefix(user_message)} {user_message}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron", hour=7, minute=50,
                         day_of_week=day_of_week,
                         args=new_date_jobs)
    await state.set_state(ClientState.settings)
    await settings(message, state)


@dp.message(ClientState.date_jobs_month)
async def date_jobs_month(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    day_of_month = message.text
    scheduler.add_job(executing_scheduler_job, trigger="cron", day=f"{day_of_month}", hour=7, minute=50,
                      args=(state, new_date_jobs))
    out_message = f'Я напомню вам : "{new_date_jobs}" каждый {day_of_month} день месяца'
    await scheduler_list(message, state, out_message, user_states_data, day=day_of_month, hour=7, minute=50,
                         trigger="cron", args=new_date_jobs)
    await state.set_state(ClientState.settings)
    await settings(message, state)


@dp.message(ClientState.date_jobs_year)
async def date_jobs_year(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    date = datetime.datetime.strptime(message.text, '%d-%m')
    scheduler.add_job(executing_scheduler_job, trigger="cron", day=date.day, month=date.month, hour=7, minute=50,
                      args=(state, new_date_jobs))
    out_message = f'Я напомню вам : "{new_date_jobs}" каждое {date.day} {date.strftime("%B")}'
    await scheduler_list(message, state, out_message, user_states_data, trigger="cron", day=date.day, month=date.month,
                         hour=7, minute=50,
                         args=new_date_jobs)
    await state.set_state(ClientState.settings)
    await settings(message, state)


@dp.message(ClientState.date_jobs_once)
async def date_jobs_once(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    new_date_jobs = user_states_data['new_date_jobs']
    date = datetime.datetime.strptime(message.text, '%Y-%m-%d') + timedelta(hours=7, minutes=50)
    if datetime.datetime.now() < date:
        out_message = f'Я напомню вам : "{new_date_jobs}" {date.day} {date.strftime("%B")} {date.year}'

        scheduler.add_job(executing_scheduler_job, trigger="date", run_date=date, args=(state, out_message))

        await state.set_state(ClientState.settings)
        await settings(message, state)
        await scheduler_list(message, state, out_message, user_states_data, trigger="date", run_date=message.text,
                             args=new_date_jobs)

    else:
        await message.answer(f'{message.text} меньше текущей даты')


async def executing_scheduler_job(state, out_message):
    # функция которая срабатывает когда срабатывает scheduler
    user_states_data = await state.get_data()
    scheduler_arguments = user_states_data['scheduler_arguments']
    append_to_database = {}
    if scheduler_arguments[out_message]['trigger'] == 'date':
        del scheduler_arguments[out_message]
        append_to_database['scheduler_arguments'] = scheduler_arguments
    job = normalized(out_message.split(' : '[1]))
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        one_time_jobs.append(job)
        user_states_data(one_time_jobs=one_time_jobs)
    except KeyError:
        user_states_data(one_time_jobs=job)
    append_to_database['one_time_jobs'] = job
    await edit_database(**append_to_database)


@dp.message(F.text == 'Разовые дела', ClientState.jobs)
async def change_one_time_jobs(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    try:
        one_time_jobs = user_states_data['one_time_jobs']
        await message.answer(
            'Введите новый список разовых дел через запятую. Ваш предыдущий список:',
            reply_markup=remove_markup)
        await message.answer(one_time_jobs)
    except KeyError:
        await message.answer('Введите новый список разовых дел через запятую', reply_markup=remove_markup)
    await state.set_state(ClientState.one_time_jobs_2)


@dp.message(ClientState.one_time_jobs_2)
async def change_one_time_jobs_2(message: Message, state: FSMContext) -> None:
    one_time_jobs_str = normalized(message.text)
    await edit_database(one_time_jobs=one_time_jobs_str)
    await message.answer(f'Отлично, теперь ваш список разовых дел выглядит так:')
    await message.answer(one_time_jobs_str)
    await state.update_data(one_time_jobs=one_time_jobs_str)
    await settings(message, state)


@dp.message(F.text == 'Ежедневные дела', ClientState.jobs)
async def daily_jobs(message: Message, state: FSMContext) -> None:
    await message.answer('Введите новый список ежедневных. Ваш предыдущий список: ',
                         reply_markup=remove_markup)
    user_states_data = await state.get_data()
    daily_scores = user_states_data['daily_scores']
    await message.answer(', '.join(daily_scores))
    await state.set_state(ClientState.new_daily_scores)


@dp.message(F.text == 'Скачать дневник')
async def diary_download(message: Message) -> None:
    try:
        await message.answer_document(
            document=FSInputFile(f'{message.from_user.id}_Diary.xlsx'),
            disable_content_type_detection=True,
        )
    except FileNotFoundError:
        await message.answer('Сначала заполните данные')


@dp.message(ClientState.new_daily_scores)
async def update_daily_jobs(message: Message, state: FSMContext) -> None:
    user_message = normalized(message.text)
    str_data = user_message.split(', ')
    daily_scores = [one_jobs for one_jobs in str_data]
    await state.update_data(daily_scores=daily_scores)
    await edit_database(daily_scores=daily_scores)
    await message.answer('Отлично, ваш список ежедневных дел обновлен!')
    await state.set_state(ClientState.settings)
    await settings(message, state)


@dp.message(ClientState.greet)
async def my_steps(message: Message, state: FSMContext) -> None:
    user_states_data = await state.get_data()
    daily_scores = user_states_data['daily_scores']
    user_message = normalized(message.text)
    # обработка ежедневных дел
    errors = [activity for activity in user_message.split(', ') if
              activity not in daily_scores]
    if errors:
        for error in errors:
            await message.answer(f"{error} нету в списке!")
    else:
        await state.update_data(activities=user_message.split(', '))
        try:
            one_time_jobs = user_states_data['one_time_jobs']
            await message.answer(f'Введите разовые дела, которые выполнили. Список разовых дел:')
            await message.answer(one_time_jobs)
            await state.set_state(ClientState.one_time_jobs_proceed)
        except KeyError:
            await message.answer("Сколько сделал шагов?")
            await state.set_state(ClientState.steps)


@dp.message(ClientState.one_time_jobs_proceed)
async def process_one_time(message: Message, state: FSMContext) -> None:
    text = normalized(message.text).split(', ')
    data = await state.get_data()
    one_time_jobs = data['one_time_jobs']
    for jobs in text:
        if jobs in negative_responses:
            await message.answer("Сколько сделал шагов?")
            await state.set_state(ClientState.steps)
            return
        elif jobs not in one_time_jobs:
            await message.answer(f'{jobs} нету в списке разовых дел!')
            return
        else:
            one_time_jobs.remove(jobs)
    await edit_database(one_time_jobs=one_time_jobs)
    if not one_time_jobs:
        await message.answer('Поздравляю! Вы выполнили все разовые дела, так держать')
        del data['one_time_jobs']
        await state.clear()
        await state.set_data(data)
    else:
        await state.update_data(one_time_jobs=one_time_jobs)
    await message.answer("Сколько сделал шагов?")
    await state.set_state(ClientState.steps)


@dp.message(ClientState.steps)
async def process_steps(message: Message, state: FSMContext) -> None:
    await state.update_data(mysteps=message.text)
    await message.answer('Сколько всего спал?')
    await state.set_state(ClientState.total_sleep)


@dp.message(ClientState.total_sleep)
async def process_total_sleep(message: Message, state: FSMContext) -> None:
    user_message = message.text
    if user_message not in negative_responses:
        await state.update_data(total_sleep=user_message)
        await message.answer('Сколько из них глубокий сон?')
        await state.set_state(ClientState.deep_sleep)
    else:
        await state.update_data(total_sleep=0.0)
        await state.update_data(deep_sleep=0.0)
        await message.answer(
            'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
        await state.set_state(ClientState.about_day)


@dp.message(ClientState.deep_sleep)
async def process_deep_sleep(message: Message, state: FSMContext) -> None:
    try:
        user_message = float(message.text)
        await state.update_data(deep_sleep=message.text)
        await message.answer(
            'Хочешь рассказать как прошел день? Это поможет отслеживать почему день был хороший или нет')
        await state.set_state(ClientState.about_day)
    except ValueError:
        await message.answer(f'"{message.text}" должен быть в десятичном формате, например: 1.1 или 0.3')


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


@dp.message(ClientState.personal_rate)
async def process_personal_rate(message: Message, state: FSMContext) -> None:
    try:
        personal_rate = float(message.text)
        if personal_rate <= 10.0 and personal_rate >= 0.0:
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

    except Exception as e:
        print(e)
        await message.answer(f'"{message.text}" должен быть числом от 0 до 10')


async def existing_user(message, state):
    user_data = await state.get_data()
    if 'daily_scores' in user_data:
        daily_scores = user_data['daily_scores']
        await message.answer(
            'Расскажи мне как провел вчерашний день?' + '\n' + 'Вот список ежедневных дел:')
        keyboard = generate_keyboard(['Вывести дневник', 'Настройки', 'Заполнить дневник'])
        await message.answer(', '.join(daily_scores), reply_markup=keyboard)
        await message.answer(
            'Впишите ежедневные дела которые вы вчера делали' + '\n'
            + 'Вы можете изменить списки в любой момент')
    try:
        one_time_jobs = user_data['one_time_jobs']
        await message.answer(
            'Разовые дела:')
        await message.answer(one_time_jobs)
    except KeyError:
        pass
    if 'scheduler' in user_data:
        #загрузка в scheduler заданий из database
        output = []
        for key, values in user_data['scheduler_arguments'].items():
            output.append(key)
            values['args'] = (state, values['args'])
            if 'date' in values:
                values['date'] = datetime.datetime.strptime(values['date'], '%Y-%m-%d')
            scheduler.add_job(executing_scheduler_job, **values)
        await state.update_data(scheduler_data = output)
    # if 'scheduler_jobs' in user_data:
    #     output = "\n".join(user_data['scheduler_jobs'])
    #     await message.answer(
    #         'Запланированные дела:')
    #     await message.answer(output)
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


async def start_scheduler(message, state):
    global already_started
    if already_started:
        return
    scheduler.add_job(start, 'cron', hour=8, minute=00, args=(message, state))
    scheduler.start()
    already_started = True


async def main():
    await database_start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
