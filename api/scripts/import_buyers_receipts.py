from __future__ import annotations

import argparse
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


def text(value: object) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--source-size", type=int)
    parser.add_argument("--source-modified-at")
    parser.add_argument("--period-from", default="2026-01-01")
    parser.add_argument("--database-url", default="dbname=finbg")
    args = parser.parse_args()

    receipts = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not isinstance(receipts, list):
        raise ValueError("Input JSON must contain a list of receipts")

    source_file = (
        PureWindowsPath(args.source_path).name
        if "\\" in args.source_path
        else Path(args.source_path).name
    )
    content_hash = canonical_hash(receipts)

    with psycopg.connect(args.database_url) as connection:
        batch_id = connection.execute(
            """
            insert into import_batches (
                source_file,
                source_sheet,
                source_path,
                source_size,
                source_modified_at,
                period_from,
                content_hash,
                status
            ) values (
                %(source_file)s,
                'СчетаРуб',
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
                "content_hash": content_hash,
            },
        ).fetchone()[0]

        inserted = 0
        updated = 0
        total = Decimal("0")

        skipped_zero = 0
        for item in receipts:
            # Аннулированная строка: суммы обнулены, а в пояснении написано
            # почему. Оплатой это не является, и таблица оплат её не принимает —
            # там стоит проверка «сумма больше нуля». Пропускаем, а не ломаем
            # загрузку и не ослабляем проверку: нулевая оплата бессмысленна.
            if Decimal(str(item.get("paidAmount") or 0)) <= 0:
                skipped_zero += 1
                continue
            source_key = canonical_hash(
                {
                    "source_path": args.source_path,
                    "sheet": "СчетаРуб",
                    "row": item["excelRow"],
                }
            )
            result = connection.execute(
                """
                insert into customer_receipts (
                    import_batch_id,
                    source_file,
                    source_sheet,
                    source_row,
                    source_record_key,
                    customer_name,
                    document_type,
                    document_number,
                    document_date,
                    paid_amount_rub,
                    payment_date,
                    manager_name,
                    notes,
                    source_payload,
                    updated_at
                ) values (
                    %(import_batch_id)s,
                    %(source_file)s,
                    'СчетаРуб',
                    %(source_row)s,
                    %(source_record_key)s,
                    %(customer_name)s,
                    %(document_type)s,
                    %(document_number)s,
                    %(document_date)s,
                    %(paid_amount_rub)s,
                    %(payment_date)s,
                    %(manager_name)s,
                    %(notes)s,
                    %(source_payload)s,
                    now()
                )
                on conflict (source_record_key) do update set
                    import_batch_id = excluded.import_batch_id,
                    customer_name = excluded.customer_name,
                    document_type = excluded.document_type,
                    document_number = excluded.document_number,
                    document_date = excluded.document_date,
                    paid_amount_rub = excluded.paid_amount_rub,
                    payment_date = excluded.payment_date,
                    manager_name = excluded.manager_name,
                    notes = excluded.notes,
                    source_payload = excluded.source_payload,
                    updated_at = now()
                returning (xmax = 0) as inserted
                """,
                {
                    "import_batch_id": batch_id,
                    "source_file": source_file,
                    "source_row": item["excelRow"],
                    "source_record_key": source_key,
                    "customer_name": text(item["customer"]),
                    "document_type": text(item.get("documentType")),
                    "document_number": text(item.get("documentNumber")),
                    "document_date": item.get("documentDate"),
                    "paid_amount_rub": Decimal(str(item["paidAmount"])),
                    "payment_date": item["paymentDate"],
                    "manager_name": text(item.get("manager")),
                    "notes": text(item.get("notes")),
                    "source_payload": Jsonb(item),
                },
            ).fetchone()[0]
            if result:
                inserted += 1
            else:
                updated += 1
            total += Decimal(str(item["paidAmount"]))

        connection.execute(
            """
            update import_batches
            set imported_rows = %(rows)s,
                skipped_rows = %(skipped)s,
                status = 'completed'
            where id = %(batch_id)s
            """,
            {
                "rows": len(receipts) - skipped_zero,
                "skipped": skipped_zero,
                "batch_id": batch_id,
            },
        )

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "rows": len(receipts),
                "inserted_rows": inserted,
                "updated_rows": updated,
                "amount_rub": str(total),
                "content_hash": content_hash,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
