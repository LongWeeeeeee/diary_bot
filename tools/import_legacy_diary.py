"""Восстановление истории дневника из скачанных Excel-файлов.

База daily_scores.db дважды пересоздавалась, из-за чего история до мая 2026
пропала. Excel-выгрузки в ~/Downloads — снимки базы на разные даты, и вместе
они восстанавливают потерянные дни.

Запуск (подготовка данных на маке):
    python3 tools/import_legacy_diary.py --scan ~/Downloads --out rows.json
Запуск (импорт на сервере):
    python3 tools/import_legacy_diary.py --apply rows.json --db daily_scores.db --user <id>

Импорт добавляет ТОЛЬКО отсутствующие даты и приводит названия дел к текущим
(tools/legacy_synonyms.py). Существующие записи не перезаписываются.
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.legacy_synonyms import canonical  # noqa: E402

# Тексты-подсказки самого бота: если они попали в «о дне», записи не было
PROMPTS = (
    "Подробно расскажи про свой день",
    "В чем ты лучше себя вчерашнего",
)
JUNK_ACTIVITIES = {"", "-", "nan", "тест", "тест_р", "тест_разовый", "akbars"}


def scan(folder: str, user_id: str):
    """Собирает записи из всех Excel-снимков пользователя."""
    import pandas as pd

    merged = defaultdict(
        lambda: {"activities": set(), "about": "", "rate": None, "steps": None, "sleep": None}
    )
    files = sorted(glob.glob(os.path.join(os.path.expanduser(folder), f"{user_id}_Diary*.xlsx")))
    for path in files:
        try:
            df = pd.read_excel(path)
        except Exception as e:
            print(f"пропускаю {os.path.basename(path)}: {e}")
            continue
        if "Дата" not in df.columns:
            continue
        for _, row in df.iterrows():
            about = str(row["О дне"]).strip()
            if about.startswith(PROMPTS) or about in ("nan", "-", ""):
                continue
            try:
                day = datetime.strptime(str(row["Дата"]), "%d.%m.%Y").date().isoformat()
            except ValueError:
                continue
            record = merged[day]
            raw_activities = str(row["Дела за день"])
            if raw_activities.strip().lower() not in JUNK_ACTIVITIES:
                for part in raw_activities.split(","):
                    part = part.strip()
                    if part and part.lower() not in JUNK_ACTIVITIES:
                        record["activities"].update(canonical(part))
            if len(about) > len(record["about"]):
                record["about"] = about
            try:
                rate = float(row["My rate"])
            except (TypeError, ValueError):
                rate = None
            if rate is not None and (record["rate"] is None or rate != 0):
                record["rate"] = rate

    rows = []
    for day in sorted(merged):
        record = merged[day]
        rows.append(
            {
                "date": day,
                "activities": ", ".join(sorted(record["activities"])),
                "about_day": record["about"],
                "rate": record["rate"],
                "steps": record["steps"],
                "sleep": record["sleep"],
            }
        )
    print(f"файлов просмотрено: {len(files)}, дней собрано: {len(rows)}")
    return rows


def apply(rows_path: str, db_path: str, user_id: str, dry_run: bool = False):
    """Добавляет отсутствующие дни в daily_logs (существующие не трогает)."""
    rows = json.load(open(rows_path, encoding="utf-8"))
    if not dry_run:
        backup = f"{db_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(db_path, backup)
        print(f"бэкап базы: {backup}")

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT date FROM daily_logs WHERE user_id = ?", (str(user_id),)
            )
        }
        to_insert = [row for row in rows if row["date"] not in existing]
        print(f"в базе {len(existing)} дней, к добавлению {len(to_insert)}")
        if dry_run:
            return to_insert

        conn.executemany(
            "INSERT INTO daily_logs (user_id, date, activities, steps, sleep_quality, "
            "about_day, personal_rate) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(user_id),
                    row["date"],
                    row["activities"],
                    row["steps"],
                    row["sleep"],
                    row["about_day"],
                    row["rate"],
                )
                for row in to_insert
            ],
        )
        conn.commit()
        total = conn.execute(
            "SELECT COUNT(*) FROM daily_logs WHERE user_id = ?", (str(user_id),)
        ).fetchone()[0]
        print(f"добавлено {len(to_insert)}, теперь дней в базе: {total}")
        return to_insert
    finally:
        conn.close()


def normalize_existing(db_path: str, user_id: str, dry_run: bool = False):
    """Приводит названия дел в уже существующих записях к каноническим."""
    conn = sqlite3.connect(db_path, timeout=30)
    changes = []
    try:
        for date, activities in conn.execute(
            "SELECT date, activities FROM daily_logs WHERE user_id = ?", (str(user_id),)
        ):
            if not activities:
                continue
            names = []
            for part in activities.split(","):
                part = part.strip()
                if part:
                    names.extend(canonical(part))
            new_value = ", ".join(sorted(dict.fromkeys(names)))
            if new_value != activities:
                changes.append((date, activities, new_value))
        if not dry_run and changes:
            conn.executemany(
                "UPDATE daily_logs SET activities = ? WHERE user_id = ? AND date = ?",
                [(new, str(user_id), date) for date, _, new in changes],
            )
            conn.commit()
    finally:
        conn.close()
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", help="папка со скачанными Excel-дневниками")
    parser.add_argument("--out", help="куда сохранить собранные записи (json)")
    parser.add_argument("--apply", help="json с записями для импорта")
    parser.add_argument("--db", default="daily_scores.db")
    parser.add_argument("--user", required=True)
    parser.add_argument("--normalize-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.scan:
        rows = scan(args.scan, args.user)
        with open(args.out or "legacy_rows.json", "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print(f"сохранено в {args.out or 'legacy_rows.json'}")

    if args.apply:
        apply(args.apply, args.db, args.user, dry_run=args.dry_run)

    if args.normalize_existing:
        changes = normalize_existing(args.db, args.user, dry_run=args.dry_run)
        print(f"переименований в существующих записях: {len(changes)}")
        for date, old, new in changes:
            print(f"  {date}: {old}  ->  {new}")


if __name__ == "__main__":
    main()
