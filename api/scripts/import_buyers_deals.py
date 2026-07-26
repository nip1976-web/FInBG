from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

import psycopg
from psycopg.rows import dict_row
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


def normalize_name(value: object) -> str:
    normalized = str(value or "").lower()
    normalized = re.sub(r'[«»"\'.,()№]', " ", normalized)
    normalized = re.sub(
        r"\b(ооо|зао|оао|пао|ип|ао|филиал)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def financial_status(balance: Decimal) -> str:
    if abs(balance) <= Decimal("0.01"):
        return "closed"
    if balance < 0:
        return "advance"
    return "open"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--source-size", type=int)
    parser.add_argument("--source-modified-at")
    parser.add_argument("--period-from", default="2026-01-01")
    parser.add_argument("--database-url", default="dbname=finbg")
    args = parser.parse_args()

    items = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("Input JSON must contain a list of deals")

    source_file = (
        PureWindowsPath(args.source_path).name
        if "\\" in args.source_path
        else Path(args.source_path).name
    )
    content_hash = canonical_hash(items)

    with psycopg.connect(
        args.database_url,
        row_factory=dict_row,
    ) as connection:
        batch = connection.execute(
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
        ).fetchone()
        batch_id = batch["id"]

        customer_ids: dict[str, int] = {}
        manager_ids: dict[str, int | None] = {}
        status_counts = {"closed": 0, "open": 0, "advance": 0}
        matched = 0
        unmatched = 0

        for item in items:
            customer = text(item["customer"])
            if customer is None:
                continue
            if customer not in customer_ids:
                counterparty = connection.execute(
                    """
                    select id
                    from counterparties
                    where name = %(name)s
                    order by id
                    limit 1
                    """,
                    {"name": customer},
                ).fetchone()
                if counterparty is None:
                    counterparty = connection.execute(
                        """
                        insert into counterparties (
                            name,
                            counterparty_type,
                            default_currency
                        ) values (%(name)s, 'customer', 'RUB')
                        returning id
                        """,
                        {"name": customer},
                    ).fetchone()
                customer_ids[customer] = counterparty["id"]

            manager = text(item.get("manager"))
            if manager not in manager_ids:
                if manager is None:
                    manager_ids[manager] = None
                else:
                    employee = connection.execute(
                        """
                        select id
                        from employees
                        where full_name = %(full_name)s
                        order by id
                        limit 1
                        """,
                        {"full_name": manager},
                    ).fetchone()
                    if employee is None:
                        employee = connection.execute(
                            """
                            insert into employees (full_name, is_manager)
                            values (%(full_name)s, true)
                            returning id
                            """,
                            {"full_name": manager},
                        ).fetchone()
                    manager_ids[manager] = employee["id"]

            amount = Decimal(str(item["dealAmount"]))
            paid = Decimal(str(item["paidAmount"]))
            balance = Decimal(str(item["balance"]))
            fin_status = financial_status(balance)
            status_counts[fin_status] += 1
            document_type = text(item.get("documentType"))
            document_number = text(item.get("documentNumber"))
            notes = text(item.get("notes"))
            deal_number = f"BUYERS-{item['excelRow']}"
            title = notes or " ".join(
                part
                for part in [customer, document_type, document_number]
                if part
            )

            deal = connection.execute(
                """
                insert into deals (
                    deal_number,
                    title,
                    customer_id,
                    manager_id,
                    status,
                    specification_number,
                    opened_on,
                    closed_on,
                    planned_revenue_rub,
                    notes,
                    source,
                    source_file,
                    source_sheet,
                    source_row,
                    original_document_type,
                    original_document_number,
                    paid_amount_rub,
                    balance_rub,
                    payment_date,
                    financial_status,
                    match_status,
                    source_payload,
                    updated_at
                ) values (
                    %(deal_number)s,
                    %(title)s,
                    %(customer_id)s,
                    %(manager_id)s,
                    %(status)s,
                    %(specification_number)s,
                    %(opened_on)s,
                    %(closed_on)s,
                    %(planned_revenue_rub)s,
                    %(notes)s,
                    'buyers',
                    %(source_file)s,
                    'СчетаРуб',
                    %(source_row)s,
                    %(document_type)s,
                    %(document_number)s,
                    %(paid_amount_rub)s,
                    %(balance_rub)s,
                    %(payment_date)s,
                    %(financial_status)s,
                    'unmatched',
                    %(source_payload)s,
                    now()
                )
                on conflict (deal_number) do update set
                    title = excluded.title,
                    customer_id = excluded.customer_id,
                    manager_id = excluded.manager_id,
                    status = excluded.status,
                    specification_number = excluded.specification_number,
                    opened_on = excluded.opened_on,
                    closed_on = excluded.closed_on,
                    planned_revenue_rub = excluded.planned_revenue_rub,
                    notes = excluded.notes,
                    paid_amount_rub = excluded.paid_amount_rub,
                    balance_rub = excluded.balance_rub,
                    payment_date = excluded.payment_date,
                    financial_status = excluded.financial_status,
                    source_payload = excluded.source_payload,
                    updated_at = now()
                returning id
                """,
                {
                    "deal_number": deal_number,
                    "title": title,
                    "customer_id": customer_ids[customer],
                    "manager_id": manager_ids[manager],
                    "status": (
                        "completed" if fin_status == "closed" else "active"
                    ),
                    "specification_number": document_number,
                    "opened_on": item.get("documentDate")
                    or item["paymentDate"],
                    "closed_on": (
                        item["paymentDate"]
                        if fin_status == "closed"
                        else None
                    ),
                    "planned_revenue_rub": amount,
                    "notes": notes,
                    "source_file": source_file,
                    "source_row": item["excelRow"],
                    "document_type": document_type,
                    "document_number": document_number,
                    "paid_amount_rub": paid,
                    "balance_rub": balance,
                    "payment_date": item["paymentDate"],
                    "financial_status": fin_status,
                    "source_payload": Jsonb(item),
                },
            ).fetchone()

            candidates = connection.execute(
                """
                select id, raw_counterparty, description
                from payments
                where source = 'payment_battery'
                  and direction = 'inflow'
                  and payment_date = %(payment_date)s
                  and amount_rub = %(amount_rub)s
                """,
                {
                    "payment_date": item["paymentDate"],
                    "amount_rub": paid,
                },
            ).fetchall()
            safe_candidates = []
            customer_key = normalize_name(customer)
            document_key = (document_number or "").lower()
            for candidate in candidates:
                counterparty_key = normalize_name(
                    candidate["raw_counterparty"]
                )
                description_key = str(
                    candidate["description"] or ""
                ).lower()
                name_match = bool(
                    customer_key
                    and counterparty_key
                    and (
                        customer_key in counterparty_key
                        or counterparty_key in customer_key
                    )
                )
                document_match = bool(
                    document_key and document_key in description_key
                )
                if name_match or document_match:
                    safe_candidates.append(candidate)

            if len(candidates) == 1 and len(safe_candidates) == 1:
                payment = safe_candidates[0]
                connection.execute(
                    """
                    insert into payment_allocations (
                        payment_id,
                        deal_id,
                        allocated_amount_rub,
                        source,
                        match_confidence
                    ) values (
                        %(payment_id)s,
                        %(deal_id)s,
                        %(amount)s,
                        'automatic_buyers_match',
                        'automatic'
                    )
                    on conflict (payment_id, deal_id) do update set
                        allocated_amount_rub = excluded.allocated_amount_rub,
                        source = excluded.source,
                        match_confidence = excluded.match_confidence
                    """,
                    {
                        "payment_id": payment["id"],
                        "deal_id": deal["id"],
                        "amount": paid,
                    },
                )
                connection.execute(
                    """
                    update deals
                    set match_status = 'matched',
                        updated_at = now()
                    where id = %(deal_id)s
                    """,
                    {"deal_id": deal["id"]},
                )
                matched += 1
            else:
                connection.execute(
                    """
                    update deals
                    set match_status = %(match_status)s,
                        updated_at = now()
                    where id = %(deal_id)s
                    """,
                    {
                        "deal_id": deal["id"],
                        "match_status": (
                            "review" if candidates else "unmatched"
                        ),
                    },
                )
                unmatched += 1

        connection.execute(
            """
            update import_batches
            set imported_rows = %(rows)s,
                skipped_rows = 0,
                status = 'completed'
            where id = %(batch_id)s
            """,
            {"rows": len(items), "batch_id": batch_id},
        )

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "rows": len(items),
                "customers": len(customer_ids),
                "managers": len(
                    [value for value in manager_ids.values() if value]
                ),
                "financial_status": status_counts,
                "matched": matched,
                "unmatched_or_review": unmatched,
                "content_hash": content_hash,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
