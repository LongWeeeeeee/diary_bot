import re
from datetime import timedelta
import pandas as pd
from aiogram import types


async def add_day_to_excel(date, activities: list, sleep_quality: int, personal_rate: float,
                           my_steps: int,
                           daily_tasks: list,
                           user_message: str, message, excel_chosen_tasks=None, personal_records=None):
    path = str(message.from_user.id) + '_Diary.xlsx'
    try:
        data = pd.read_excel(path)
    except FileNotFoundError:
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
    if not len(activities):
        await message.answer('Поздравляю! дневник заполнен')
    else:
        personal_records = await counter_max_days(data=data, daily_scores=daily_tasks, message=message, activities=activities, personal_records=personal_records)
        return personal_records


def counter_negative(column, current_word, count=0):
    for words in column.iloc[::-1]:
        if not isinstance(words, float):
            split_words = words.split(', ')
            for word in split_words:
                if word == current_word:
                    return count
            count += 1
        else: return count
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
        if not isinstance(words, float):
            split_words = words.split(', ')
            if current_word in split_words:
                count += 1
            else:
                return count
        else:
            return count
    return count


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
                if personal_records[key] < value: personal_records[key] = value
            else:
                personal_records[key] = value
            if value not in [0, 1]:
                positive_output.append(f'{key} : {value}')
        positive_output = '\n'.join(positive_output)
        if positive_output:
            output += f'Поздравляю! Вы соблюдаете эти дела уже столько дней:\n{positive_output}'
        if negative_output:
            if output != '': output += '\n\n'
            output += f'Вы не делали эти дела уже столько дней:\n{negative_output}\n\nМожет стоит дать им еще один шанс?'
        if output:
            sent_message = await message.answer(output)
            await message.bot.pin_chat_message(message.chat.id, sent_message.message_id)
            return personal_records
    else:
        await message.answer('Поздравляю! дневник заполнен')


def generate_keyboard(buttons: list, last_button = None, first_button = None):
    if last_button != None:
        kb = [[types.KeyboardButton(text=button) for button in buttons], [types.KeyboardButton(text=last_button)]]
    elif first_button != None:
        kb = [[types.KeyboardButton(text=first_button)], [types.KeyboardButton(text=button) for button in buttons]]
    else:
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
        message_sheet = "{} | {} | {} | {} | {} | {} | {}".format(row["Дата"], row["Дела за день"], row["Шаги"],
                                                                       row["Total sleep"], row['Deep sleep'],
                                                                       row['О дне'], row['My rate'])

        # Разделение длинного сообщения на части
        message_parts = [message_sheet[i:i + 4096] for i in range(0, len(message_sheet), 4096)]

        for part in message_parts:
            await message.answer(part)
