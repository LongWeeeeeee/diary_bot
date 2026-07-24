"""Разбор дневника: связь дел, сна и шагов с оценкой дня.

Считается только по структурированным данным (`daily_logs`): дела, шаги,
качество сна, оценка дня. Свободный текст «о дне» не анализируется.

Все функции чистые — на вход список записей БД, на выход текст сообщения.
"""
import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

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


@dataclass
class DayRecord:
    """Одна запись дневника, приведённая к типам."""

    day: date
    activities: Set[str] = field(default_factory=set)
    steps: Optional[float] = None
    sleep: Optional[float] = None
    rate: Optional[float] = None


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
        records[day] = DayRecord(
            day=day,
            activities=activities,
            steps=_to_float(row[2]),
            sleep=_to_float(row[3]),
            rate=_to_float(row[5]),
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

    weekday = weekday_insight(records)
    if weekday:
        parts += ["", f"🗓 {weekday}"]

    if not insights and not numeric and not weekday:
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
