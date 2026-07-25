"""Разбор дневника: связь дел, слов и самочувствия с оценкой дня.

Считается по `daily_logs`: дела, шаги, качество сна, оценка дня и слова из
свободного текста «о дне» (частотно, без LLM — см. text_signals.py).

Все функции чистые — на вход список записей БД, на выход текст сообщения.
"""
import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from text_signals import activity_stems, cluster_stems, day_terms

logger = logging.getLogger(__name__)

# Сколько дней должно быть В КАЖДОЙ группе, чтобы сравнение вообще имело смысл
MIN_GROUP_SIZE = 4
# Минимальная «уверенность» (статистика Уэлча): на 3-4 днях разница в балл
# получается случайно, поэтому одного порога по разнице средних мало
MIN_T_STAT = 1.8
# Минимум оценённых дней, ниже которого не показываем выводы вообще
MIN_RATED_DAYS = 5
# Разница в средней оценке, ниже которой считаем это шумом
MIN_RATE_DIFF = 0.7
# Сколько выводов показывать максимум
MAX_INSIGHTS = 4

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Тексты-подсказки самого бота, если они попали в поле «о дне»
BOT_PROMPTS = ("Подробно расскажи про свой день", "В чем ты лучше себя вчерашнего")

# Слов в тексте много, поэтому случайных совпадений больше — планка выше,
# чем для дел, иначе в выводы попадёт первое попавшееся слово
MIN_WORD_DAYS = 4
MIN_WORD_T_STAT = 2.3
MAX_WORD_INSIGHTS = 4


@dataclass
class DayRecord:
    """Одна запись дневника, приведённая к типам."""

    day: date
    activities: Set[str] = field(default_factory=set)
    steps: Optional[float] = None
    sleep: Optional[float] = None
    rate: Optional[float] = None
    about: str = ""


def _to_float(value) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _parse_day(raw) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_logs(logs: Sequence[tuple]) -> List[DayRecord]:
    """Превращает строки daily_logs в список DayRecord, отсортированный по дате."""
    records: Dict[date, DayRecord] = {}
    for row in logs or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        day = _parse_day(row[0])
        if day is None:
            continue
        activities = set()
        if isinstance(row[1], str):
            activities = {part.strip() for part in row[1].split(",") if part.strip()}
        about = row[4] if isinstance(row[4], str) else ""
        if about.strip().startswith(BOT_PROMPTS):
            about = ""  # в «о дне» попал вопрос самого бота, а не запись
        records[day] = DayRecord(
            day=day,
            activities=activities,
            steps=_to_float(row[2]),
            sleep=_to_float(row[3]),
            rate=_to_float(row[5]),
            about=about,
        )
    return [records[day] for day in sorted(records)]


def _fmt(value: float, digits: int = 1) -> str:
    """Число без хвоста .0 и с запятой как разделителем."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") or "0"


def _mean(values: Sequence[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return statistics.fmean(values)


def welch_t(first: Sequence[float], second: Sequence[float]) -> float:
    """|t| Уэлча для двух выборок — грубая мера того, что разница не случайна.

    Чем меньше группа и чем больше разброс внутри неё, тем ниже результат,
    поэтому редкие дела перестают выигрывать у частых на случайном разбросе.
    """
    if len(first) < 2 or len(second) < 2:
        return 0.0
    mean_diff = statistics.fmean(first) - statistics.fmean(second)
    var = statistics.variance(first) / len(first) + statistics.variance(second) / len(second)
    if var <= 0:
        # Разброса нет вообще: считаем разницу значимой, если она есть
        return 99.0 if mean_diff else 0.0
    return abs(mean_diff) / (var ** 0.5)


def _split_by_activity(
    records: Sequence[DayRecord], activity: str
) -> Tuple[List[float], List[float]]:
    """Оценки дней с делом и без него (только дни, где оценка есть)."""
    with_it, without_it = [], []
    for rec in records:
        if rec.rate is None:
            continue
        if activity in rec.activities:
            with_it.append(rec.rate)
        else:
            without_it.append(rec.rate)
    return with_it, without_it


def activity_insights(records: Sequence[DayRecord]) -> List[Tuple[str, str, float]]:
    """Дела, в дни с которыми оценка заметно отличается.

    Возвращает список (дело, текст, |разница|), отсортированный по силе эффекта.
    """
    all_activities: Set[str] = set()
    for rec in records:
        if rec.rate is not None:
            all_activities |= rec.activities

    insights = []
    for activity in sorted(all_activities):
        with_it, without_it = _split_by_activity(records, activity)
        if len(with_it) < MIN_GROUP_SIZE or len(without_it) < MIN_GROUP_SIZE:
            continue
        mean_with, mean_without = _mean(with_it), _mean(without_it)
        diff = mean_with - mean_without
        if abs(diff) < MIN_RATE_DIFF:
            continue
        strength = welch_t(with_it, without_it)
        if strength < MIN_T_STAT:
            continue
        sign = "выше" if diff > 0 else "ниже"
        text = (
            f"{activity}: {_fmt(mean_with)} против {_fmt(mean_without)} "
            f"({sign} на {_fmt(abs(diff))}, дней {len(with_it)}/{len(without_it)})"
        )
        insights.append((activity, text, strength))

    # Сортируем по уверенности, а не по величине разницы: иначе наверх лезут
    # редкие дела со случайным разбросом
    insights.sort(key=lambda item: item[2], reverse=True)
    return insights


def _numeric_insight(
    records: Sequence[DayRecord], attr: str, label: str, unit: str = ""
) -> Optional[str]:
    """Сравнивает оценку дня в дни с высоким и низким значением метрики."""
    pairs = [
        (getattr(rec, attr), rec.rate)
        for rec in records
        if getattr(rec, attr) is not None and rec.rate is not None
    ]
    if len(pairs) < MIN_GROUP_SIZE * 2:
        return None

    values = sorted(value for value, _ in pairs)
    median = statistics.median(values)
    high = [rate for value, rate in pairs if value > median]
    low = [rate for value, rate in pairs if value <= median]
    if len(high) < MIN_GROUP_SIZE or len(low) < MIN_GROUP_SIZE:
        return None

    mean_high, mean_low = _mean(high), _mean(low)
    diff = mean_high - mean_low
    if abs(diff) < MIN_RATE_DIFF or welch_t(high, low) < MIN_T_STAT:
        return None

    direction = "выше" if diff > 0 else "ниже"
    suffix = f" {unit}" if unit else ""
    return (
        f"{label}: в дни выше {_fmt(median)}{suffix} оценка {_fmt(mean_high)}, "
        f"в остальные {_fmt(mean_low)} ({direction} на {_fmt(abs(diff))})"
    )


def weekday_insight(records: Sequence[DayRecord]) -> Optional[str]:
    """Лучший и худший день недели по средней оценке."""
    by_weekday: Dict[int, List[float]] = {}
    for rec in records:
        if rec.rate is None:
            continue
        by_weekday.setdefault(rec.day.weekday(), []).append(rec.rate)

    usable = {wd: rates for wd, rates in by_weekday.items() if len(rates) >= 2}
    if len(usable) < 3:
        return None

    means = {wd: _mean(rates) for wd, rates in usable.items()}
    best = max(means, key=lambda wd: means[wd])
    worst = min(means, key=lambda wd: means[wd])
    if best == worst or means[best] - means[worst] < 1.0:
        return None
    return (
        f"Лучше всего идут {WEEKDAYS_RU[best]} ({_fmt(means[best])}), "
        f"тяжелее всего {WEEKDAYS_RU[worst]} ({_fmt(means[worst])})"
    )


def word_insights(records: Sequence[DayRecord]) -> List[Tuple[str, str, float]]:
    """Слова из «о дне», связанные с оценкой дня.

    Слова, уже покрытые названиями дел, отбрасываются — иначе одно и то же
    («подтягивался» в тексте и дело «Подтягивания») попадёт в разбор дважды.
    """
    known = activity_stems({name for rec in records for name in rec.activities})

    per_day: List[Tuple[Set[str], float]] = []
    display: Dict[str, str] = {}
    name_stems: Set[str] = set()
    for rec in records:
        if rec.rate is None or not rec.about:
            continue
        stems, day_display, names = day_terms(rec.about, exclude=known)
        per_day.append((stems, rec.rate))
        name_stems |= names
        for base, word in day_display.items():
            display.setdefault(base, word)

    if len(per_day) < MIN_WORD_DAYS * 2:
        return []

    # Склеиваем формы одного слова, иначе «ставку» и «ставить» считаются порознь
    canonical = cluster_stems({base for stems, _ in per_day for base in stems})
    per_day = [({canonical.get(b, b) for b in stems}, rate) for stems, rate in per_day]
    name_stems = {canonical.get(b, b) for b in name_stems}
    display = {
        canonical.get(base, base): word
        for base, word in sorted(display.items(), key=lambda item: len(item[1]))
    }

    counts: Dict[str, int] = {}
    for stems, _ in per_day:
        for base in stems:
            counts[base] = counts.get(base, 0) + 1

    insights = []
    for base, count in counts.items():
        if count < MIN_WORD_DAYS or len(per_day) - count < MIN_WORD_DAYS:
            continue
        with_it = [rate for stems, rate in per_day if base in stems]
        without_it = [rate for stems, rate in per_day if base not in stems]
        mean_with, mean_without = _mean(with_it), _mean(without_it)
        diff = mean_with - mean_without
        if abs(diff) < MIN_RATE_DIFF:
            continue
        strength = welch_t(with_it, without_it)
        if strength < MIN_WORD_T_STAT:
            continue
        word = display.get(base, base)
        if base in name_stems:
            label = f"«{word.capitalize()}» (имя)"
        else:
            label = f"«{word}»"
        sign = "выше" if diff > 0 else "ниже"
        text = (
            f"{label}: {_fmt(mean_with)} против {_fmt(mean_without)} "
            f"({sign} на {_fmt(abs(diff))}, дней {len(with_it)}/{len(without_it)})"
        )
        insights.append((base, text, strength))

    insights.sort(key=lambda item: item[2], reverse=True)
    return insights


def build_word_report(
    logs: Sequence[tuple], min_days: int = 3, limit: int = 40
) -> str:
    """Полный список самых частых слов из «о дне» с их влиянием на оценку.

    В отличие от build_analysis показывает всё подряд, без порога значимости —
    чтобы можно было глазами посмотреть на слабые и пограничные связи.
    """
    records = parse_logs(logs)
    rated = [rec for rec in records if rec.rate is not None and rec.about]
    if len(rated) < MIN_WORD_DAYS * 2:
        return (
            "📝 Слова из записей о дне\n\n"
            f"Нужно хотя бы {MIN_WORD_DAYS * 2} дней с текстом, "
            f"сейчас {len(rated)}."
        )

    known = activity_stems({name for rec in records for name in rec.activities})
    per_day: List[Tuple[Set[str], float]] = []
    display: Dict[str, str] = {}
    name_stems: Set[str] = set()
    for rec in rated:
        stems, day_display, names = day_terms(rec.about, exclude=known)
        per_day.append((stems, rec.rate))
        name_stems |= names
        for base, word in day_display.items():
            if base not in display or len(word) < len(display[base]):
                display[base] = word

    canonical = cluster_stems({base for stems, _ in per_day for base in stems})
    per_day = [({canonical.get(b, b) for b in stems}, rate) for stems, rate in per_day]
    name_stems = {canonical.get(b, b) for b in name_stems}
    grouped_display: Dict[str, str] = {}
    for base, word in display.items():
        root = canonical.get(base, base)
        if root not in grouped_display or len(word) < len(grouped_display[root]):
            grouped_display[root] = word

    counts: Dict[str, int] = {}
    for stems, _ in per_day:
        for base in stems:
            counts[base] = counts.get(base, 0) + 1

    rows = []
    for base, count in counts.items():
        if count < min_days or len(per_day) - count < min_days:
            continue
        with_it = [rate for stems, rate in per_day if base in stems]
        without_it = [rate for stems, rate in per_day if base not in stems]
        diff = _mean(with_it) - _mean(without_it)
        rows.append((count, diff, welch_t(with_it, without_it), base))

    rows.sort(key=lambda item: (-item[0], -abs(item[1])))
    lines = [
        f"📝 Слова из записей о дне ({len(per_day)} дней с текстом)",
        "",
        "слово · дней · разница в оценке дня",
    ]
    for count, diff, strength, base in rows[:limit]:
        word = grouped_display.get(base, base)
        if base in name_stems:
            word = word.capitalize()
        sign = "+" if diff > 0 else "−"
        mark = " ★" if abs(diff) >= MIN_RATE_DIFF and strength >= MIN_WORD_T_STAT else ""
        lines.append(f"{word} · {count} · {sign}{_fmt(abs(diff))}{mark}")
    lines += [
        "",
        "★ — разница не похожа на случайную. Остальное смотрите как подсказку, "
        "а не как вывод: слова из дел сюда не попадают, они в основном разборе.",
    ]
    return "\n".join(lines)


def _window(records: Sequence[DayRecord], start: date, end: date) -> List[DayRecord]:
    return [rec for rec in records if start <= rec.day <= end]


def week_summary(records: Sequence[DayRecord], today: date) -> List[str]:
    """Сводка за последние 7 дней с дельтой к предыдущей неделе."""
    this_week = _window(records, today - timedelta(days=6), today)
    prev_week = _window(records, today - timedelta(days=13), today - timedelta(days=7))

    lines = [f"📅 Неделя: заполнено {len(this_week)} из 7 дней"]
    if not this_week:
        lines.append("Записей за неделю нет — начните с одного дня, этого достаточно.")
        return lines

    rates = [rec.rate for rec in this_week if rec.rate is not None]
    if rates:
        line = f"Средняя оценка дня: {_fmt(_mean(rates))}"
        prev_rates = [rec.rate for rec in prev_week if rec.rate is not None]
        if prev_rates:
            diff = _mean(rates) - _mean(prev_rates)
            if abs(diff) >= 0.2:
                arrow = "↑" if diff > 0 else "↓"
                line += f" ({arrow} {_fmt(abs(diff))} к прошлой неделе)"
            else:
                line += " (как на прошлой неделе)"
        lines.append(line)

    sleep = _mean([rec.sleep for rec in this_week if rec.sleep is not None])
    if sleep is not None:
        lines.append(f"Средний сон: {_fmt(sleep)}")
    steps = _mean([rec.steps for rec in this_week if rec.steps is not None])
    if steps is not None:
        lines.append(f"Средние шаги: {_fmt(steps, 0)}")

    counts: Dict[str, int] = {}
    for rec in this_week:
        for activity in rec.activities:
            counts[activity] = counts.get(activity, 0) + 1
    if counts:
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        lines.append("Чаще всего делали: " + ", ".join(f"{name} — {n}" for name, n in top))
    return lines


def build_analysis(logs: Sequence[tuple], today: Optional[date] = None) -> str:
    """Собирает текст разбора дневника."""
    records = parse_logs(logs)
    if today is None:
        today = datetime.now().date()

    if not records:
        return (
            "📊 Разбор дневника\n\n"
            "Пока нет ни одной записи. Заполните дневник хотя бы несколько дней — "
            "и я покажу, что влияет на ваши хорошие дни."
        )

    parts = ["📊 Разбор дневника", ""]
    parts.extend(week_summary(records, today))

    rated = [rec for rec in records if rec.rate is not None]
    if len(rated) < MIN_RATED_DAYS:
        parts += [
            "",
            f"Для выводов нужно хотя бы {MIN_RATED_DAYS} оценённых дней, "
            f"сейчас {len(rated)}. Продолжайте — осталось немного.",
        ]
        return "\n".join(parts)

    insights = [text for _, text, _ in activity_insights(records)[:MAX_INSIGHTS]]
    if insights:
        parts += ["", "🔍 Что влияет на оценку дня:"]
        parts += [f"• {text}" for text in insights]

    numeric = [
        _numeric_insight(records, "sleep", "Сон"),
        _numeric_insight(records, "steps", "Шаги"),
    ]
    numeric = [text for text in numeric if text]
    if numeric:
        parts += ["", "😴 Сон и активность:"]
        parts += [f"• {text}" for text in numeric]

    words = [text for _, text, _ in word_insights(records)[:MAX_WORD_INSIGHTS]]
    if words:
        parts += ["", "💬 Слова из ваших записей о дне:"]
        parts += [f"• {text}" for text in words]

    weekday = weekday_insight(records)
    if weekday:
        parts += ["", f"🗓 {weekday}"]

    if not insights and not numeric and not words and not weekday:
        parts += [
            "",
            "Пока устойчивых связей не видно — оценки дней слишком похожи. "
            "Чем больше записей, тем точнее будет разбор.",
        ]
    else:
        parts += [
            "",
            f"Это наблюдения по {len(rated)} оценённым дням, а не доказанные причины.",
        ]
    return "\n".join(parts)
