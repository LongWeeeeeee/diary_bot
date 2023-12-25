from datetime import timedelta

import pandas as pd
from aiogram import types


async def add_day_to_excel(date, activities: [], total_sleep: float, deep_sleep: float, rate: int, mysteps: int,
                           user_id: int,
                           daily_scores: [],
                           user_message: str, message):
    path = str(user_id) + '_Diary.xlsx'

    try:
        data = pd.read_excel(path)
    except FileNotFoundError:
        data = pd.DataFrame(
            columns=['Дата', 'Дела за день', 'Шаги', 'Total sleep', 'Deep sleep', 'О дне', 'My rate', 'Total'])

    last_row = data.index.max() + 1

    yesterday = date - timedelta(days=1)
    data.loc[last_row, 'Дата'] = yesterday.strftime("%d.%m.%Y")

    data.loc[last_row, 'Дела за день'] = ", ".join(activities)
    data.loc[last_row, 'Шаги'] = mysteps
    data.loc[last_row, 'Total sleep'] = total_sleep
    data.loc[last_row, 'Deep sleep'] = deep_sleep
    data.loc[last_row, 'О дне'] = user_message
    data.loc[last_row, 'My rate'] = rate

    score = sum(int(daily_scores[activity]) for activity in activities)

    data.loc[last_row, 'Total'] = score

    data.to_excel(path, index=False)

    total_days = {current_word: str(counter_days) + ' дня' if counter_days in [2, 3, 4] else str(counter_days) + ' дней'
                  for current_word in daily_scores if
                  (counter_days := counter_negavite(current_word, path)) is not None and counter_days >= 3}
    output = ['{}: {}'.format(key, value) for key, value in total_days.items()]
    result = '\n'.join(output)
    keyboard = generate_keyboard(['Вывести дневник', 'Настройки', 'Заполнить дневник'])
    if result != '':
        await message.answer(f'Вы не делали эти дела уже столько дней:\n{result}\nМожет стоит дать им еще один шанс?')

    total_days = {current_word: str(counter_days) + ' дня' if counter_days in [2, 3, 4] else str(counter_days) + ' дней'
                  for current_word in activities if
                  (counter_days := counter_positive(current_word, path)) is not None}
    output = ['{} : {}'.format(key, value) for key, value in total_days.items()]
    result = '\n'.join(output)
    if result != '':
        await message.answer('Поздравляю! Вы соблюдаете эти дела уже столько дней: ' + '\n' + result,
                             reply_markup=keyboard)
    await message.answer('Дневник заполнен!', reply_markup=keyboard)


def counter_negavite(current_word, path):
    data = pd.read_excel(path)

    # Выбор нужной колонки
    column = data['Дела за день']

    def perebor(count=0):
        for words in column.iloc[-1::-1]:
            split_words = words.split(', ')
            for word in split_words:
                if word == current_word:
                    return count
            count += 1
        return count

    return perebor()


def counter_positive(current_word, path):
    data = pd.read_excel(path)
    # Выбор нужной колонки
    column = data['Дела за день']

    def prohod(count=0):
        for words in column.iloc[-1::-1]:
            split_words = words.split(', ')
            if current_word in split_words:
                count += 1
            else:
                if count >= 2: return count

        if count >= 2: return count

    return prohod()


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


async def diary_out(message):
    data = pd.read_excel(f'{message.from_user.username}_Diary.xlsx')
    await message.answer(
        "{} | {} | {} | {} | {} | {} | {} | {}".format("Дата", "Дела за день", "Шаги", "Total sleep", 'Deep sleep',
                                                       'О дне', 'My rate', 'Total'))
    for index, row in data.iterrows():
        message_sheet = "{} | {} | {} | {} | {} | {} | {} | {}".format(row["Дата"], row["Дела за день"], row["Шаги"],
                                                                       row["Total sleep"], row['Deep sleep'],
                                                                       row['О дне'],
                                                                       row['My rate'], row['Total'])

        # разделение длинного сообщения на части
        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)
