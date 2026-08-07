from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PureWindowsPath

import psycopg
from psycopg.types.json import Jsonb


def canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def duplicate_key(item: dict) -> tuple:
    return (
        item.get("sheet"),
        item.get("date"),
        item.get("counterparty"),
        str(item.get("amount")),
        item.get("explanation"),
        item.get("category"),
    )


def text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--source-size", type=int)
    parser.add_argument("--source-modified-at")
    parser.add_argument("--period-from", default="2026-01-01")
    parser.add_argument("--database-url", default="dbname=finbg")
    args = parser.parse_args()

    operations = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not isinstance(operations, list):
        raise ValueError("Input JSON must contain a list of operations")

    duplicate_counts = Counter(duplicate_key(item) for item in operations)
    input_hash = canonical_hash(operations)
    source_file = (
        PureWindowsPath(args.source_path).name
        if "\\" in args.source_path
        else Path(args.source_path).name
    )

    insert_sql = """
        insert into staging_payment_operations (
            import_batch_id,
            source_file,
            source_sheet,
            source_row,
            source_record_key,
            content_hash,
            direction,
            sequence_number,
            account_code,
            counterparty,
            payer_tax_id,
            amount_rub,
            operation_date,
            raw_date,
            explanation,
            payment_basis,
            client,
            category,
            reporting_period,
            plan_code,
            movement_type,
            manager,
            revenue_type,
            likely_internal_transfer,
            possible_duplicate,
            validation_status,
            validation_errors,
            source_payload,
            updated_at
        ) values (
            %(import_batch_id)s,
            %(source_file)s,
            %(source_sheet)s,
            %(source_row)s,
            %(source_record_key)s,
            %(content_hash)s,
            %(direction)s,
            %(sequence_number)s,
            %(account_code)s,
            %(counterparty)s,
            %(payer_tax_id)s,
            %(amount_rub)s,
            %(operation_date)s,
            %(raw_date)s,
            %(explanation)s,
            %(payment_basis)s,
            %(client)s,
            %(category)s,
            %(reporting_period)s,
            %(plan_code)s,
            %(movement_type)s,
            %(manager)s,
            %(revenue_type)s,
            %(likely_internal_transfer)s,
            %(possible_duplicate)s,
            %(validation_status)s,
            %(validation_errors)s,
            %(source_payload)s,
            now()
        )
        on conflict (source_record_key) do update set
            import_batch_id = excluded.import_batch_id,
            content_hash = excluded.content_hash,
            direction = excluded.direction,
            sequence_number = excluded.sequence_number,
            account_code = excluded.account_code,
            counterparty = excluded.counterparty,
            payer_tax_id = excluded.payer_tax_id,
            amount_rub = excluded.amount_rub,
            operation_date = excluded.operation_date,
            raw_date = excluded.raw_date,
            explanation = excluded.explanation,
            payment_basis = excluded.payment_basis,
            client = excluded.client,
            category = excluded.category,
            reporting_period = excluded.reporting_period,
            plan_code = excluded.plan_code,
            movement_type = excluded.movement_type,
            manager = excluded.manager,
            revenue_type = excluded.revenue_type,
            likely_internal_transfer = excluded.likely_internal_transfer,
            possible_duplicate = excluded.possible_duplicate,
            validation_status = excluded.validation_status,
            validation_errors = excluded.validation_errors,
            source_payload = excluded.source_payload,
            updated_at = now()
    """

    with psycopg.connect(args.database_url) as connection:
        batch_id = connection.execute(
            """
            insert into import_batches (
                source_file,
                source_path,
                source_size,
                source_modified_at,
                period_from,
                content_hash,
                status
            ) values (
                %(source_file)s,
                %(source_path)s,
                %(source_size)s,
                %(source_modified_at)s,
                %(period_from)s,
                %(content_hash)s,
                'started'
            )
            returning id
            """,
            {
                "source_file": source_file,
                "source_path": args.source_path,
                "source_size": args.source_size,
                "source_modified_at": args.source_modified_at,
                "period_from": date.fromisoformat(args.period_from),
                "content_hash": input_hash,
            },
        ).fetchone()[0]

        rows = []
        for item in operations:
            errors: list[str] = []
            if not item.get("date"):
                errors.append("missing_operation_date")
            if not item.get("counterparty"):
                errors.append("missing_counterparty")
            if Decimal(str(item.get("amount", 0))) <= 0:
                errors.append("invalid_amount")

            record_key = canonical_hash(
                {
                    "source_path": args.source_path,
                    "sheet": item["sheet"],
                    "row": item["excelRow"],
                }
            )
            rows.append(
                {
                    "import_batch_id": batch_id,
                    "source_file": source_file,
                    "source_sheet": item["sheet"],
                    "source_row": item["excelRow"],
                    "source_record_key": record_key,
                    "content_hash": canonical_hash(item),
                    "direction": item["direction"],
                    "sequence_number": text(item.get("sequence")),
                    "account_code": text(item.get("account")),
                    "counterparty": text(item.get("counterparty")),
                    "payer_tax_id": text(item.get("payerTaxId")),
                    "amount_rub": Decimal(str(item["amount"])),
                    "operation_date": date.fromisoformat(item["date"]),
                    "raw_date": text(item.get("rawDate")),
                    "explanation": text(item.get("explanation")),
                    "payment_basis": text(item.get("basis")),
                    "client": text(item.get("client")),
                    "category": text(item.get("category")),
                    "reporting_period": (
                        date.fromisoformat(item["period"])
                        if item.get("period")
                        else None
                    ),
                    "plan_code": text(item.get("plan")),
                    "movement_type": text(item.get("movement")),
                    "manager": text(item.get("manager")),
                    "revenue_type": text(item.get("revenueType")),
                    "likely_internal_transfer": bool(
                        item.get("likelyInternalTransfer")
                    ),
                    "possible_duplicate": duplicate_counts[
                        duplicate_key(item)
                    ] > 1,
                    # «excluded» — строка не той статьи поступлений: заносим её
                    # ради истории, но в платежи не переводим. Снятие своих
                    # денег в кассу выручкой не является, а выбросить строку
                    # совсем — значит через месяц не вспомнить, была она в файле
                    # или нет. Перенос такие строки пропускает.
                    "validation_status": (
                        "error" if errors
                        else "excluded" if item.get("excluded")
                        else "pending"
                    ),
                    "validation_errors": Jsonb(errors),
                    "source_payload": Jsonb(item),
                }
            )

        with connection.cursor() as cursor:
            cursor.executemany(insert_sql, rows)
            cursor.execute(
                """
                update import_batches
                set imported_rows = %(imported_rows)s,
                    skipped_rows = 0,
                    status = 'completed'
                where id = %(batch_id)s
                """,
                {"imported_rows": len(rows), "batch_id": batch_id},
            )

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "rows": len(operations),
                "possible_duplicate_rows": sum(
                    1
                    for item in operations
                    if duplicate_counts[duplicate_key(item)] > 1
                ),
                "content_hash": input_hash,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
