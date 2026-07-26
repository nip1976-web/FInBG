from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def account_name(item: dict) -> str:
    if item.get("account_code"):
        return str(item["account_code"]).strip()
    if item["source_sheet"] == "ПоступленияН":
        return "Касса"
    return str(item["source_sheet"]).strip()


def account_type(item: dict, name: str) -> str:
    normalized = f"{item['source_sheet']} {name}".lower()
    return "cash" if "касс" in normalized or item["source_sheet"] == "ПоступленияН" else "bank"


def description(item: dict) -> str | None:
    parts = [
        item.get("explanation"),
        item.get("payment_basis"),
        item.get("client"),
    ]
    cleaned = [str(value).strip() for value in parts if value and str(value).strip()]
    return " | ".join(dict.fromkeys(cleaned)) or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period-from", default="2026-01-01")
    parser.add_argument("--database-url", default="dbname=finbg")
    args = parser.parse_args()

    period_from = date.fromisoformat(args.period_from)

    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        operations = connection.execute(
            """
            select *
            from staging_payment_operations
            where direction = 'inflow'
              and operation_date >= %(period_from)s
              and validation_status not in ('error', 'excluded')
            order by operation_date, source_sheet, source_row
            """,
            {"period_from": period_from},
        ).fetchall()

        account_ids: dict[str, int] = {}
        inserted = 0
        existing = 0
        total = Decimal("0")

        for item in operations:
            name = account_name(item)
            if name not in account_ids:
                account = connection.execute(
                    """
                    select id
                    from financial_accounts
                    where legal_entity_id is null and name = %(name)s
                    order by id
                    limit 1
                    """,
                    {"name": name},
                ).fetchone()
                if account is None:
                    account = connection.execute(
                        """
                        insert into financial_accounts (
                            legal_entity_id,
                            name,
                            account_type,
                            currency
                        ) values (null, %(name)s, %(account_type)s, 'RUB')
                        returning id
                        """,
                        {
                            "name": name,
                            "account_type": account_type(item, name),
                        },
                    ).fetchone()
                account_ids[name] = account["id"]

            result = connection.execute(
                """
                insert into payments (
                    account_id,
                    counterparty_id,
                    payment_date,
                    direction,
                    operation_type,
                    amount,
                    currency,
                    amount_rub,
                    description,
                    external_id,
                    source,
                    status,
                    raw_counterparty,
                    source_sheet,
                    source_row,
                    is_internal_transfer,
                    source_payload
                ) values (
                    %(account_id)s,
                    null,
                    %(payment_date)s,
                    'inflow',
                    %(operation_type)s,
                    %(amount)s,
                    'RUB',
                    %(amount)s,
                    %(description)s,
                    %(external_id)s,
                    'payment_battery',
                    'posted',
                    %(raw_counterparty)s,
                    %(source_sheet)s,
                    %(source_row)s,
                    %(is_internal_transfer)s,
                    %(source_payload)s
                )
                on conflict (source, external_id) do nothing
                returning id
                """,
                {
                    "account_id": account_ids[name],
                    "payment_date": item["operation_date"],
                    "operation_type": (
                        "internal_transfer"
                        if item["likely_internal_transfer"]
                        else item.get("category") or "incoming"
                    ),
                    "amount": item["amount_rub"],
                    "description": description(item),
                    "external_id": item["source_record_key"],
                    "raw_counterparty": item.get("counterparty"),
                    "source_sheet": item["source_sheet"],
                    "source_row": item["source_row"],
                    "is_internal_transfer": item["likely_internal_transfer"],
                    "source_payload": Jsonb(item["source_payload"]),
                },
            ).fetchone()

            if result is None:
                existing += 1
            else:
                inserted += 1
            total += item["amount_rub"]

        connection.execute(
            """
            update staging_payment_operations
            set validation_status = 'imported',
                updated_at = now()
            where direction = 'inflow'
              and operation_date >= %(period_from)s
              and validation_status not in ('error', 'excluded')
            """,
            {"period_from": period_from},
        )

    print(
        json.dumps(
            {
                "eligible_rows": len(operations),
                "inserted_rows": inserted,
                "existing_rows": existing,
                "amount_rub": str(total),
                "accounts": account_ids,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
