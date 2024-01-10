import re
from datetime import timedelta

import pandas as pd
from aiogram import types


async def add_day_to_excel(date, activities: list, total_sleep: float, deep_sleep: float, personal_rate: float,
                           my_steps: int,
                           user_id: int,
                           daily_scores: list,
                           user_message: str, message):
    path = str(user_id) + '_Diary.xlsx'
    try:
        data = pd.read_excel(path)
    except FileNotFoundError:
        data = pd.DataFrame(
            columns=['Дата', 'Дела за день', 'Шаги', 'Total sleep', 'Deep sleep', 'О дне', 'My rate'])

    # Вывод подсчета сколько дней делали и не делал дела из daily_scores в бота

    last_row = data.index.max() + 1
    yesterday = date - timedelta(days=1)
    data.loc[last_row, 'Дата'] = yesterday.strftime("%d.%m.%Y")
    data.loc[last_row, 'Дела за день'] = ", ".join(activities)
    data.loc[last_row, 'Шаги'] = my_steps
    data.loc[last_row, 'Total sleep'] = total_sleep
    data.loc[last_row, 'Deep sleep'] = deep_sleep
    data.loc[last_row, 'О дне'] = user_message
    data.loc[last_row, 'My rate'] = personal_rate

    data.to_excel(path, index=False)

    await counter_max_days(data=data, daily_scores=daily_scores, message=message, activities=activities)


def counter_negative(column, current_word, count=0):
    for words in column.iloc[::-1]:
        split_words = words.split(', ')
        for word in split_words:
            if word == current_word:
                return count
        count += 1
    return count


def day_to_prefix(day: str) -> str:
    day_to_prefix_dict = {
        'воскресенье' : 'каждое',
        'субботу' : 'каждую',
        'пятницу' : 'каждую',
        'четверг' : 'каждый',
        'среду' : 'каждую',
        'вторник' : 'каждый',
        'понедельник' : 'каждый'
    }
    return day_to_prefix_dict[day]


def counter_positive(current_word, column, count=0):
    for words in column.iloc[::-1]:
        split_words = words.split(', ')
        if current_word in split_words:
            count += 1
        else:
            return count
    return count


async def counter_max_days(data, daily_scores, message, activities, output=''):
    negative_dict, positive_dict = {}, {}
    if negative_dict is None:
        negative_dict = {}
    column = data['Дела за день']
    negative_dict = {current_word: counter_negative(current_word=current_word, column=column) for current_word in
                     daily_scores}
    positive_dict = {current_word: counter_positive(current_word=current_word, column=column) for current_word in
                     activities}
    negative_output = '\n'.join(
        ['{} : {}'.format(key, value) for key, value in negative_dict.items() if value not in [0, 1]])
    positive_output = '\n'.join(
        ['{} : {}'.format(key, value) for key, value in positive_dict.items() if value not in [0, 1]])
    if positive_output:
        output += f'Поздравляю! Вы соблюдаете эти дела уже столько дней:\n{positive_output}'
    if negative_output:
        if output != '': output += '\n\n'
        output += f'Вы не делали эти дела уже столько дней:\n{negative_output}\n\nМожет стоит дать им еще один шанс?'
    sent_message = await message.answer(output)
    await message.bot.pin_chat_message(message.chat.id, sent_message.message_id)


def generate_keyboard(buttons: list):
    """
    Create a main keyboard with customizable buttons.

    Args:
        buttons (list): A list of strings representing the text for each button.

    Returns:
        types.ReplyKeyboardMarkup: The created main keyboard.
    """
    kb = [[types.KeyboardButton(text=button) for button in buttons]]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
    )
    return keyboard


def normalized(text):
    return re.sub(r',(?=[^\s])', ', ', text).lower().replace('ё', 'е')


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
        message_sheet = "{} | {} | {} | {} | {} | {} | {} | {}".format(row["Дата"], row["Дела за день"], row["Шаги"],
                                                                       row["Total sleep"], row['Deep sleep'],
                                                                       row['О дне'], row['My rate'], row['Total'])

        # Разделение длинного сообщения на части
        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)
