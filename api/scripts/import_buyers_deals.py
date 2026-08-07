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


def buyers_deal_number(
    customer_id: int,
    document_type: str | None,
    document_number: str | None,
    document_date: str | None,
    payment_date: str | None,
    deal_amount: Decimal,
) -> str:
    """Признак сделки — сам документ, а не номер строки в Excel.

    Повторяет функцию buyers_deal_number в api/sql/013_buyers_deal_natural_key.sql:
    строки должны совпадать посимвольно, иначе переимпорт создаст дубли.
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
    # Ozon и физлица приходят без номера документа: их различают дата оплаты и сумма.
    return "-".join(
        [
            f"BUYERS-{customer_id}",
            (payment_date or "").strip(),
            f"{deal_amount:.2f}",
        ]
    )


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
        seen_deal_numbers: list[str] = []

        # Строки, исключённые вручную: решение человека живёт отдельно от файла,
        # иначе перезаливка вернула бы удалённое уже через минуту.
        excluded_rows = {
            row["source_row"]
            for row in connection.execute(
                """
                select source_row from deal_row_exclusions
                where source_sheet = 'СчетаРуб'
                """
            ).fetchall()
        }
        skipped_excluded = 0

        for item in items:
            if item.get("excelRow") in excluded_rows:
                skipped_excluded += 1
                continue
            customer = text(item["customer"])
            if customer is None:
                continue
            if customer not in customer_ids:
                # Опознаём клиента, не завися от написания: форма собственности,
                # кавычки, порядок слов и регистр отбрасываются. Названия в
                # файле пишутся так, чтобы их было удобно читать менеджерам, и
                # переформатирование не должно означать нового клиента.
                #
                # Сокращение против полного написания («Приангарский ЛПК» и
                # «Приангарский Лесоперерабатывающий Комплекс») формулой не
                # ловится — для этого список альтернативных написаний, который
                # заводится с подтверждения человека.
                counterparty = connection.execute(
                    """
                    select id
                    from counterparties
                    where normalize_counterparty_name(name)
                          = normalize_counterparty_name(%(name)s)
                    order by id
                    limit 1
                    """,
                    {"name": customer},
                ).fetchone()
                if counterparty is None:
                    counterparty = connection.execute(
                        """
                        select counterparty_id as id
                        from counterparty_name_aliases
                        where normalize_counterparty_name(alias_name)
                              = normalize_counterparty_name(%(name)s)
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
            deal_number = buyers_deal_number(
                customer_ids[customer],
                document_type,
                document_number,
                text(item.get("documentDate")),
                text(item.get("paymentDate")),
                amount,
            )
            seen_deal_numbers.append(deal_number)
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
                    %(planned_revenue_rub)s,
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
                    source_row = excluded.source_row,
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

        # Сделки, которых в файле больше нет: строку удалили или переписали
        # реквизиты документа. Ничего не удаляем — только показываем, чтобы
        # такие сделки не оставались в отчётах незамеченными.
        orphans = connection.execute(
            """
            select
                d.id,
                d.deal_number,
                c.name as customer,
                d.original_document_number as document_number,
                d.planned_revenue_rub,
                (
                    select count(*)
                    from payment_allocations pa
                    where pa.deal_id = d.id
                ) as linked_payments
            from deals d
            join counterparties c on c.id = d.customer_id
            where d.source = 'buyers'
              and not (d.deal_number = any(%(seen)s::text[]))
            order by d.planned_revenue_rub desc
            """,
            {"seen": seen_deal_numbers},
        ).fetchall()

        # «Оплачено» и «остаток» считаются по привязанным платежам, а не по
        # выгрузке: её цифры остаются в deals.source_payload. Триггер держит их
        # в порядке при изменении привязок, здесь пересчёт нужен на случай, если
        # у сделки изменилась сумма.
        connection.execute(
            """
            update deals d
            set paid_amount_rub = t.paid,
                balance_rub = d.planned_revenue_rub - t.paid,
                updated_at = now()
            from (
                select d2.id,
                       coalesce((
                           select sum(pa.allocated_amount_rub)
                           from payment_allocations pa
                           where pa.deal_id = d2.id
                       ), 0) as paid
                from deals d2
                where d2.source = 'buyers'
            ) t
            where d.id = t.id
              and (d.paid_amount_rub, d.balance_rub) is distinct from
                  (t.paid, d.planned_revenue_rub - t.paid)
            """
        )

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
                "excluded_rows": skipped_excluded,
                "customers": len(customer_ids),
                "managers": len(
                    [value for value in manager_ids.values() if value]
                ),
                "financial_status": status_counts,
                "matched": matched,
                "unmatched_or_review": unmatched,
                "not_in_file": [
                    {
                        **dict(row),
                        "planned_revenue_rub": str(row["planned_revenue_rub"]),
                    }
                    for row in orphans
                ],
                "content_hash": content_hash,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
