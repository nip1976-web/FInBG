"""Разноска платежей по сделкам, когда плательщик сам назвал документ.

Заливка платежей ничего не разносит: 1655 платежей за 2023-2025 пришли без
привязок, и сделки прошлых лет показывали весь счёт как долг. Здесь берётся
только бесспорное, три условия разом:

* платёж пришёл от того же клиента, что стоит в сделке (сверка по названию,
  форма собственности и кавычки отбрасываются);
* в назначении платежа назван номер счёта или спецификации именно этой сделки;
* сумма всех таких платежей сходится с оплатой из файла Buyers до копейки.

Третье условие и делает привязку безопасной при отношении «многие ко многим»:
на счёт часто приходит несколько платежей, и совпадение итога — свидетельство,
что найдены именно они, а не однофамильцы по номеру.

Без --apply в базу не пишет ничего. Один платёж, попавший в две сделки,
останавливает работу: разносить такое должен человек.

Итоги сделки (paid_amount_rub, balance_rub) здесь не пересчитываются - в живой
базе триггера из 012_deal_totals_from_allocations.sql нет. После --apply нужен
пересчёт тем же запросом, что в конце import_buyers_deals.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.documents import INVOICE_RE, SPEC_RE  # noqa: E402

CENT = Decimal("0.01")


def normalize_name(value: object) -> str:
    """Повторяет normalize_name из import_buyers_deals.py."""
    normalized = str(value or "").lower()
    normalized = re.sub(r'[«»"\'.,()№]', " ", normalized)
    normalized = re.sub(r"\b(ооо|зао|оао|пао|ип|ао|филиал)\b", " ", normalized)
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def same_client(deal_key: str, payment_key: str) -> bool:
    return bool(deal_key and payment_key and (deal_key in payment_key or payment_key in deal_key))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="dbname=finbg")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        deals = connection.execute(
            """
            select d.id, c.name as customer, d.original_document_type as document_type,
                   d.original_document_number as document_number,
                   d.source_payload->>'paidAmount' as file_paid
            from deals d
            join counterparties c on c.id = d.customer_id
            where d.source = 'buyers'
              and d.match_status in ('unmatched', 'review')
            order by d.planned_revenue_rub desc
            """
        ).fetchall()

        free_payments = connection.execute(
            """
            select p.id, p.payment_date, p.amount_rub, p.raw_counterparty, p.description
            from payments p
            where p.source = 'payment_battery'
              and p.direction = 'inflow'
              and not exists (
                  select 1 from payment_allocations a where a.payment_id = p.id
              )
            """
        ).fetchall()
        for payment in free_payments:
            payment["key"] = normalize_name(payment["raw_counterparty"])
            text = payment["description"] or ""
            payment["invoices"] = {int(value) for value in INVOICE_RE.findall(text)}
            payment["specs"] = {str(value).strip() for value in SPEC_RE.findall(text)}

        plan: list[tuple[dict, list[dict]]] = []
        for deal in deals:
            deal_key = normalize_name(deal["customer"])
            theirs = [p for p in free_payments if same_client(deal_key, p["key"])]
            try:
                # в source_payload сумма лежит числом с плавающей точкой:
                # 5290216.0200000005 - без округления итог никогда не сойдётся
                file_paid = Decimal(str(deal["file_paid"] or 0)).quantize(CENT)
            except (ArithmeticError, ValueError):
                continue
            number = str(deal["document_number"] or "").strip()
            kind = (deal["document_type"] or "").lower()
            if kind.startswith("счет") and number.isdigit():
                hits = [p for p in theirs if int(number) in p["invoices"]]
            elif kind.startswith("спец") and number:
                hits = [p for p in theirs if number in p["specs"]]
            else:
                hits = []
            if file_paid > 0 and hits:
                if sum((p["amount_rub"] for p in hits), Decimal(0)) == file_paid:
                    plan.append((deal, hits))

        claimed: dict[int, int] = {}
        conflicts = []
        for deal, hits in plan:
            for payment in hits:
                if payment["id"] in claimed:
                    conflicts.append((payment["id"], claimed[payment["id"]], deal["id"]))
                claimed[payment["id"]] = deal["id"]

        total = sum(p["amount_rub"] for _, hits in plan for p in hits)
        print(f"сделок к разноске: {len(plan)}, платежей: {len(claimed)}, сумма: {total}")
        if conflicts:
            print("один платёж на две сделки, разносить должен человек:", conflicts)
            return 1
        if not args.apply:
            for deal, hits in plan:
                picks = ", ".join(f"#{p['id']} {p['payment_date']} {p['amount_rub']}" for p in hits)
                print(f"  {deal['id']} {deal['customer']} {deal['document_type']} "
                      f"{deal['document_number']}: {picks}")
            print("это показ, в базе ничего не изменено")
            return 0

        created = 0
        for deal, hits in plan:
            for payment in hits:
                connection.execute(
                    """
                    insert into payment_allocations (
                        payment_id, deal_id, allocated_amount_rub, source, match_confidence
                    ) values (%s, %s, %s, 'automatic_document_match', 'automatic')
                    on conflict (payment_id, deal_id) do nothing
                    """,
                    (payment["id"], deal["id"], payment["amount_rub"]),
                )
                created += 1
            connection.execute(
                "update deals set match_status = 'matched', updated_at = now() where id = %s",
                (deal["id"],),
            )
        print(f"создано привязок: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
