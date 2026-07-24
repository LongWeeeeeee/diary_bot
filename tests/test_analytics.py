"""Тесты для analytics.py — разбор дневника."""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import (
    MIN_RATED_DAYS,
    activity_insights,
    build_analysis,
    parse_logs,
    week_summary,
    weekday_insight,
    _numeric_insight,
)


def log(day, activities="", steps=None, sleep=None, rate=None, about="-"):
    """Строка в формате daily_logs: (date, activities, steps, sleep, about, rate)."""
    return (day, activities, steps, sleep, about, rate)


class TestParseLogs:
    def test_parses_and_sorts(self):
        records = parse_logs(
            [
                log("2026-07-03", "Бег, Сон", 1000, 5, 8),
                log("2026-07-01", "Бег", None, None, 6),
            ]
        )
        assert [rec.day for rec in records] == [date(2026, 7, 1), date(2026, 7, 3)]
        assert records[1].activities == {"Бег", "Сон"}
        assert records[1].steps == 1000.0
        assert records[0].steps is None

    def test_skips_broken_rows(self):
        records = parse_logs(
            [log("не дата", "Бег", rate=8), ("короткая", "строка"), None]
        )
        assert records == []

    def test_non_numeric_values_become_none(self):
        records = parse_logs([log("2026-07-01", "Бег", "-", "-", "-")])
        assert records[0].rate is None
        assert records[0].steps is None


class TestActivityInsights:
    def _records(self):
        # Бег в дни с оценкой 9, без бега — 5
        rows = []
        for i in range(4):
            rows.append(log(f"2026-06-0{i + 1}", "Бег", rate=9))
        for i in range(4):
            rows.append(log(f"2026-06-1{i + 1}", "Сон", rate=5))
        return parse_logs(rows)

    def test_finds_strong_effect(self):
        insights = activity_insights(self._records())
        names = [name for name, _, _ in insights]
        assert "Бег" in names
        text = dict((name, text) for name, text, _ in insights)["Бег"]
        assert "9 против 5" in text and "дней 4/4" in text

    def test_ignores_small_groups(self):
        # Бег всего в 2 днях — меньше MIN_GROUP_SIZE
        rows = [log("2026-06-01", "Бег", rate=10), log("2026-06-02", "Бег", rate=10)]
        rows += [log(f"2026-06-1{i}", "Сон", rate=4) for i in range(4)]
        assert [n for n, _, _ in activity_insights(parse_logs(rows))] == []

    def test_ignores_weak_difference(self):
        rows = [log(f"2026-06-0{i + 1}", "Бег", rate=7) for i in range(4)]
        rows += [log(f"2026-06-1{i}", "Сон", rate=7.2) for i in range(4)]
        assert activity_insights(parse_logs(rows)) == []

    def test_days_without_rate_are_skipped(self):
        rows = [log(f"2026-06-0{i + 1}", "Бег") for i in range(5)]
        assert activity_insights(parse_logs(rows)) == []


class TestNumericInsight:
    def test_sleep_effect(self):
        rows = [log(f"2026-06-0{i + 1}", "", sleep=8, rate=9) for i in range(4)]
        rows += [log(f"2026-06-1{i}", "", sleep=3, rate=5) for i in range(4)]
        text = _numeric_insight(parse_logs(rows), "sleep", "Сон")
        assert text is not None and "выше" in text

    def test_not_enough_data(self):
        rows = [log(f"2026-06-0{i + 1}", "", sleep=8, rate=9) for i in range(3)]
        assert _numeric_insight(parse_logs(rows), "sleep", "Сон") is None

    def test_noisy_small_sample_is_rejected(self):
        """Разница есть, но разброс внутри групп её съедает."""
        rows = [log(f"2026-06-0{i + 1}", "", sleep=8, rate=r) for i, r in enumerate([10, 4, 9, 5])]
        rows += [log(f"2026-06-1{i}", "", sleep=3, rate=r) for i, r in enumerate([9, 3, 8, 4])]
        assert _numeric_insight(parse_logs(rows), "sleep", "Сон") is None


class TestWeekdayInsight:
    def test_needs_three_weekdays(self):
        rows = [log("2026-06-01", "", rate=9), log("2026-06-08", "", rate=9)]
        assert weekday_insight(parse_logs(rows)) is None

    def test_reports_best_and_worst(self):
        rows = []
        for week in range(2):
            base = date(2026, 6, 1) + timedelta(days=7 * week)
            rows.append(log(str(base), "", rate=9))  # понедельник
            rows.append(log(str(base + timedelta(days=1)), "", rate=5))  # вторник
            rows.append(log(str(base + timedelta(days=2)), "", rate=7))  # среда
        text = weekday_insight(parse_logs(rows))
        assert text is not None and "Пн" in text and "Вт" in text


class TestWeekSummary:
    def test_counts_filled_days_and_delta(self):
        today = date(2026, 7, 24)
        rows = [
            log(str(today - timedelta(days=1)), "Бег", rate=8),
            log(str(today - timedelta(days=2)), "Бег, Сон", rate=8),
            log(str(today - timedelta(days=8)), "Бег", rate=6),
        ]
        lines = week_summary(parse_logs(rows), today)
        assert "заполнено 2 из 7 дней" in lines[0]
        assert any("Средняя оценка дня: 8" in line for line in lines)
        assert any("↑ 2" in line for line in lines)
        assert any("Бег — 2" in line for line in lines)

    def test_empty_week(self):
        today = date(2026, 7, 24)
        rows = [log("2026-05-01", "Бег", rate=8)]
        lines = week_summary(parse_logs(rows), today)
        assert "заполнено 0 из 7 дней" in lines[0]
        assert "Записей за неделю нет" in lines[1]


class TestBuildAnalysis:
    def test_no_logs(self):
        text = build_analysis([], today=date(2026, 7, 24))
        assert "Пока нет ни одной записи" in text

    def test_too_few_rated_days(self):
        rows = [log(f"2026-07-0{i + 1}", "Бег", rate=8) for i in range(3)]
        text = build_analysis(rows, today=date(2026, 7, 24))
        assert f"нужно хотя бы {MIN_RATED_DAYS}" in text
        assert "Что влияет" not in text

    def test_full_report(self):
        rows = [log(f"2026-07-0{i + 1}", "Бег", rate=9) for i in range(4)]
        rows += [log(f"2026-07-1{i}", "Сон", rate=5) for i in range(4)]
        text = build_analysis(rows, today=date(2026, 7, 24))
        assert "🔍 Что влияет на оценку дня:" in text
        assert "Бег" in text
        assert "не доказанные причины" in text

    def test_no_signal_message(self):
        rows = [log(f"2026-07-0{i + 1}", "Бег", rate=7) for i in range(6)]
        text = build_analysis(rows, today=date(2026, 7, 24))
        assert "устойчивых связей не видно" in text

    def test_report_is_plain_text(self):
        rows = [log(f"2026-07-0{i + 1}", "Бег <b>", rate=9) for i in range(4)]
        rows += [log(f"2026-07-1{i}", "Сон", rate=5) for i in range(4)]
        text = build_analysis(rows, today=date(2026, 7, 24))
        # Сообщение шлётся без parse_mode, поэтому теги не экранируем, но и не ломаемся
        assert "Бег <b>" in text


class TestWelchGuard:
    """Редкие дела не должны обгонять частые на случайном разбросе."""

    def test_rejects_noisy_small_group(self):
        from analytics import welch_t

        rows = [log(f"2026-06-0{i + 1}", "Редкое", rate=r) for i, r in enumerate([10, 5, 9, 4])]
        rows += [log(f"2026-06-1{i}", "Другое", rate=r) for i, r in enumerate([8, 3, 7, 4, 9, 5])]
        assert activity_insights(parse_logs(rows)) == []
        assert welch_t([10, 5, 9, 4], [8, 3, 7, 4, 9, 5]) < 1.8

    def test_keeps_consistent_group(self):
        rows = [log(f"2026-06-0{i + 1}", "Бег", rate=9) for i in range(5)]
        rows += [log(f"2026-06-1{i}", "Сон", rate=6) for i in range(5)]
        names = [name for name, _, _ in activity_insights(parse_logs(rows))]
        assert "Бег" in names

    def test_zero_variance_pairs(self):
        from analytics import welch_t

        assert welch_t([8, 8, 8], [5, 5, 5]) == 99.0
        assert welch_t([8, 8, 8], [8, 8, 8]) == 0.0
        assert welch_t([8], [5]) == 0.0


class TestWordInsights:
    """Слова из «о дне»: сигнал есть, дублей с делами нет."""

    def _rows(self):
        rows = []
        # 6 хороших дней с великом, 6 плохих без него
        for i in range(6):
            rows.append(
                log(f"2026-06-0{i + 1}", "Подтягивания", rate=9,
                    about="Катался на велике. Подтягивался. Встретился с Машей")
            )
        for i in range(6):
            rows.append(
                log(f"2026-06-1{i}", "Подтягивания", rate=5,
                    about="Играл в доту весь день. Подтягивался")
            )
        return rows

    def test_finds_word_signal(self):
        from analytics import word_insights

        texts = [text for _, text, _ in word_insights(parse_logs(self._rows()))]
        joined = " ".join(texts)
        assert "велик" in joined
        assert "Маш" in joined  # имя помечается отдельно

    def test_does_not_duplicate_activities(self):
        from analytics import word_insights

        texts = " ".join(t for _, t, _ in word_insights(parse_logs(self._rows())))
        # «Подтягивался» в тексте не должен попасть в выводы: есть дело
        assert "подтягив" not in texts.lower()

    def test_bot_prompt_text_is_ignored(self):
        rows = [
            log(f"2026-06-0{i + 1}", "Бег", rate=9,
                about="В чем ты лучше себя вчерашнего? Не обязательно быть супер")
            for i in range(6)
        ]
        assert parse_logs(rows)[0].about == ""

    def test_needs_enough_days(self):
        from analytics import word_insights

        rows = [log(f"2026-06-0{i + 1}", "", rate=9, about="велик") for i in range(3)]
        assert word_insights(parse_logs(rows)) == []
