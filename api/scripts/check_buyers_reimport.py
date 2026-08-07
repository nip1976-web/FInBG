"""Проверка вхолостую перед перезаливкой Buyers: что опознается, а что оборвётся.

Ничего не записывает. Ни одной строки в базе не меняет.

Зачем. Загрузчик опознаёт сделку не по номеру строки в Excel, а по её паспорту:

    покупатель + вид документа + номер документа + дата документа

(а для строк без номера — покупатель + дата оплаты + сумма). Совпал паспорт —
сделка та же, привязанные к ней платежи остаются на месте. Изменилось хоть одно
поле — программа сочтёт строку новой сделкой, заведёт её пустой, а старая
останется в базе с деньгами, но без строки в файле. Внешне это выглядит как
«платежи потерялись», хотя они никуда не делись.

Отдельная ловушка — покупатель. Он ищется по точному совпадению названия. Лишний
пробел, «ООО» переехавшее в конец, кавычки другого вида — и это уже другой
покупатель, а значит другой паспорт у всех его сделок разом.

Поэтому перед заливкой прогоняем эту проверку, смотрим на список обрывов,
правим файл и повторяем. Записывать — только когда список пуст или понятен.

Использование:
    python check_buyers_reimport.py новый_файл.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def text(value: object) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def normalize_name(value: object) -> str:
    """Повторяет normalize_counterparty_name в базе и normalize_name в загрузчике.

    Три реализации обязаны совпадать: разойдутся — проверка покажет
    благополучие там, где заливка заведёт клиента заново.
    """
    normalized = str(value or "").lower()
    normalized = re.sub(r'[«»"\'.,()№]', " ", normalized)
    normalized = re.sub(r"\b(ооо|зао|оао|пао|ип|ао|филиал)\b", " ", normalized)
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def buyers_deal_number(
    customer_id: int,
    document_type: str | None,
    document_number: str | None,
    document_date: str | None,
    payment_date: str | None,
    deal_amount: Decimal,
) -> str:
    """Повторяет формулу из import_buyers_deals.py и 013_buyers_deal_natural_key.sql.

    Три места обязаны считать одинаково, посимвольно: разойдутся — проверка
    покажет благополучие там, где заливка наделает дублей.
    """
    if document_number:
        return "-".join(
            [
                f"BUYERS-{customer_id}",
                (document_type or "").strip().lower(),
                document_number.strip().lower(),
                (document_date or "").strip(),
            ]
        )
    return "-".join(
        [
            f"BUYERS-{customer_id}",
            (payment_date or "").strip(),
            f"{deal_amount:.2f}",
        ]
    )


def money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument(
        "--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg")
    )
    parser.add_argument(
        "--limit", type=int, default=25, help="сколько строк показывать в списках"
    )
    arguments = parser.parse_args()

    items = json.loads(arguments.input_json.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("В файле должен быть список сделок")
        return 2

    with psycopg.connect(arguments.database_url, row_factory=dict_row) as connection:
        # покупатели: ищем ровно так же, как загрузчик — по нормализованному
        # названию, а если не нашлось, то по списку альтернативных написаний
        known_customers = {
            row["key"]: row["id"]
            for row in connection.execute(
                """
                select normalize_counterparty_name(name) as key, id
                from counterparties
                union all
                select normalize_counterparty_name(alias_name), counterparty_id
                from counterparty_name_aliases
                """
            ).fetchall()
        }

        existing = {
            row["deal_number"]: row
            for row in connection.execute(
                """
                select
                    d.deal_number,
                    d.id,
                    d.planned_revenue_rub,
                    c.name as customer_name,
                    d.original_document_type,
                    d.original_document_number,
                    coalesce(link.payment_count, 0) as payment_count,
                    coalesce(link.allocated_rub, 0) as allocated_rub
                from deals d
                join counterparties c on c.id = d.customer_id
                left join lateral (
                    select count(*) as payment_count,
                           sum(a.allocated_amount_rub) as allocated_rub
                    from payment_allocations a
                    where a.deal_id = d.id
                ) link on true
                where d.source = 'buyers'
                """
            ).fetchall()
        }

        # Строки, исключённые вручную: загрузчик их пропускает, значит и
        # проверка не должна считать их будущими сделками
        excluded_rows = {
            row["source_row"]
            for row in connection.execute(
                "select source_row from deal_row_exclusions where source_sheet = 'СчетаРуб'"
            ).fetchall()
        }

        new_customers: dict[str, int] = {}
        file_keys: set[str] = set()
        unresolved_rows = 0
        skipped_excluded = 0

        for item in items:
            if item.get("excelRow") in excluded_rows:
                skipped_excluded += 1
                continue
            customer = text(item.get("customer"))
            if customer is None:
                unresolved_rows += 1
                continue
            customer_id = known_customers.get(normalize_name(customer))
            if customer_id is None:
                # такого покупателя в базе нет — заливка заведёт нового, и все
                # его сделки получат новые паспорта
                new_customers[customer] = new_customers.get(customer, 0) + 1
                continue
            file_keys.add(
                buyers_deal_number(
                    customer_id,
                    text(item.get("documentType")),
                    text(item.get("documentNumber")),
                    text(item.get("documentDate")),
                    text(item.get("paymentDate")),
                    Decimal(str(item.get("dealAmount") or "0")),
                )
            )

    matched = sorted(file_keys & existing.keys())
    created = sorted(file_keys - existing.keys())
    missing = sorted(existing.keys() - file_keys)

    # Дубли строк файла помечены суффиксом «-dubl-<id>» ещё миграцией 013:
    # их паспорт занят более ранней сделкой, и переимпорт их не трогает.
    # Это известное состояние, а не новая беда, поэтому считаем отдельно.
    known_duplicates = [key for key in missing if "-dubl-" in key]
    orphaned = [key for key in missing if "-dubl-" not in key]

    print("=" * 70)
    print(f"строк в файле: {len(items)}, сделок в базе: {len(existing)}")
    if unresolved_rows:
        print(f"строк без покупателя (загрузчик их пропустит): {unresolved_rows}")
    if skipped_excluded:
        print(f"строк исключено вручную: {skipped_excluded}")
    print("=" * 70)
    print(f"опознаются как те же сделки:  {len(matched)}")
    print(f"будут заведены как новые:     {len(created)}")
    print(f"останутся без строки в файле: {len(orphaned)}")
    if known_duplicates:
        print(f"известные дубли строк:        {len(known_duplicates)}")

    if new_customers:
        print()
        print(f"--- покупателей, которых нет в базе: {len(new_customers)} ---")
        print("    их сделки все до одной получат новые паспорта")
        for name, count in sorted(new_customers.items(), key=lambda pair: -pair[1])[
            : arguments.limit
        ]:
            print(f"    {name} — строк: {count}")

    lost_payments = sum(existing[key]["payment_count"] for key in orphaned)
    lost_money = sum(Decimal(str(existing[key]["allocated_rub"])) for key in orphaned)

    if known_duplicates:
        print()
        print(f"--- известные дубли строк: {len(known_duplicates)} ---")
        print("    переимпорт их не тронет, это давно так задумано")
        for key in known_duplicates[: arguments.limit]:
            row = existing[key]
            print(
                f"      {row['customer_name']} · "
                f"{row['original_document_type']} {row['original_document_number']} · "
                f"платежей: {row['payment_count']} · "
                f"{money(Decimal(str(row['allocated_rub'])))} руб."
            )

    print()
    print("--- ЧЕМ ЭТО ГРОЗИТ ---")
    if not orphaned:
        print("    ничем: каждая сделка из базы нашлась в файле")
    else:
        print(f"    сделок останется в базе без строки в файле: {len(orphaned)}")
        print(f"    на них висит привязанных платежей: {lost_payments}")
        print(f"    на сумму: {money(lost_money)} руб.")
        print()
        print("    самые тяжёлые (с деньгами) — их и надо чинить в файле:")
        with_money = sorted(
            (existing[k] for k in orphaned),
            key=lambda row: Decimal(str(row["allocated_rub"])),
            reverse=True,
        )
        for row in with_money[: arguments.limit]:
            document = " ".join(
                part
                for part in (
                    row["original_document_type"],
                    row["original_document_number"],
                )
                if part
            )
            print(
                f"      {row['customer_name']} · {document or 'без номера'} · "
                f"платежей: {row['payment_count']} · "
                f"{money(Decimal(str(row['allocated_rub'])))} руб."
            )

    if created:
        print()
        print(f"--- будут заведены заново: {len(created)} ---")
        print("    если это не новые продажи, значит у них изменился паспорт")
        for key in created[: arguments.limit]:
            print(f"      {key}")

    print()
    print("В базе ничего не изменено: это проверка.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
