"""Проверка вхолостую перед перезаливкой платежей. Ничего не записывает.

Платёж опознаётся не так, как сделка. У сделки паспорт — сам документ, а у
платежа это **путь к файлу, лист и номер строки**. Отсюда главная опасность:
вставленная или удалённая строка сдвигает все строки ниже, и каждая из них
становится «новым» платежом. Старые при этом остаются в базе вместе с
привязками к сделкам, новые приходят пустыми — деньги задваиваются.

Поэтому проверка сверяет не только наличие строки, но и её содержимое:
плательщика, сумму и дату. Совпал номер строки, а внутри другое — значит
строки сдвинулись, и заливать нельзя.

Использование:
    python check_payments_reimport.py payments.json
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--limit", type=int, default=15)
    arguments = parser.parse_args()

    items = json.loads(arguments.input_json.read_text(encoding="utf-8"))

    with psycopg.connect(arguments.database_url, row_factory=dict_row) as connection:
        # Строки, исключённые вручную: загрузчик их в платежи не переводит,
        # значит и проверка не должна считать их будущими платежами
        manual_exclusions = {
            (row["source_sheet"], row["source_row"])
            for row in connection.execute(
                "select source_sheet, source_row from payment_row_exclusions"
            ).fetchall()
        }
        incoming = {
            (item["sheet"], item["excelRow"]): item
            for item in items
            if not item.get("excluded")
            and (item["sheet"], item["excelRow"]) not in manual_exclusions
        }
        excluded_count = len(items) - len(incoming)

        existing = {
            (row["source_sheet"], row["source_row"]): row
            for row in connection.execute(
                """
                select
                    p.id, p.source_sheet, p.source_row, p.raw_counterparty,
                    p.amount_rub, p.payment_date,
                    coalesce(link.cnt, 0) as allocations,
                    coalesce(link.amount, 0) as allocated_rub
                from payments p
                left join lateral (
                    select count(*) as cnt, sum(a.allocated_amount_rub) as amount
                    from payment_allocations a where a.payment_id = p.id
                ) link on true
                where p.source = 'payment_battery'
                """
            ).fetchall()
        }

    same, moved, created = [], [], []
    for key, item in incoming.items():
        row = existing.get(key)
        if row is None:
            created.append(item)
            continue
        # содержимое строки: плательщик, сумма, дата. Разошлось — строки сдвинулись.
        # Имя сравниваем без крайних пробелов: в файле они встречаются, на
        # опознание строки не влияют, а тревогу поднимают на пустом месте.
        matches = (
            (row["raw_counterparty"] or "").strip() == (item.get("counterparty") or "").strip()
            and Decimal(str(row["amount_rub"])) == Decimal(str(item["amount"]))
            and row["payment_date"].isoformat() == item["date"]
        )
        (same if matches else moved).append((item, row))

    missing = [row for key, row in existing.items() if key not in incoming]

    print("=" * 70)
    print(f"строк в файле к переносу: {len(incoming)}, помечено исключёнными: {excluded_count}")
    print(f"платежей в базе: {len(existing)}")
    print("=" * 70)
    print(f"совпадают строка в строку:     {len(same)}")
    print(f"добавятся как новые:           {len(created)}")
    print(f"СТРОКИ СДВИНУЛИСЬ:             {len(moved)}")
    print(f"в базе без строки в файле:     {len(missing)}")

    if moved:
        print()
        print("--- ОПАСНО: по этому номеру строки в файле теперь другая операция ---")
        print("    заливать нельзя: платежи задвоятся, привязки останутся на старых")
        for item, row in moved[: arguments.limit]:
            print(
                f"      {item['sheet']} строка {item['excelRow']}:\n"
                f"        в базе:  {row['payment_date']} {row['raw_counterparty']} "
                f"{money(Decimal(str(row['amount_rub'])))}\n"
                f"        в файле: {item['date']} {item.get('counterparty')} "
                f"{money(Decimal(str(item['amount'])))}"
            )

    lost = [row for row in missing if row["allocations"] > 0]
    print()
    print("--- ЧЕМ ЭТО ГРОЗИТ ---")
    if not moved and not lost:
        print("    ничем: строки на местах, ни одна привязка не повисает")
    if lost:
        total = sum(Decimal(str(row["allocated_rub"])) for row in lost)
        print(f"    платежей с привязками, которых нет в файле: {len(lost)}")
        print(f"    на сумму: {money(total)} руб.")
        for row in sorted(lost, key=lambda r: Decimal(str(r["allocated_rub"])), reverse=True)[
            : arguments.limit
        ]:
            print(
                f"      {row['source_sheet']} строка {row['source_row']} · "
                f"{row['raw_counterparty']} · {money(Decimal(str(row['allocated_rub'])))} руб."
            )

    print()
    print("В базе ничего не изменено: это проверка.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
