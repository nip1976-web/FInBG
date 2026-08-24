"""Synchronize every unambiguous FinBG payment assignment to Bitrix invoices.

The default mode is a read-only preview. ``--apply`` updates payment fields and
stages and creates one idempotent timeline comment per payment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.documents import invoice_numbers  # noqa: E402


ENTITY_TYPE_ID = 31
TIMELINE_ENTITY_TYPE = "dynamic_31"
PAID_STAGE = "DT31_1:P"
PARTIAL_STAGE = "DT31_1:FINBG_PARTIAL_PAYMENT"
PAID_FIELD = "ufCrmSmartInvoiceFinbgPaidAmount"
BALANCE_FIELD = "ufCrmSmartInvoiceFinbgBalance"
COUNT_FIELD = "ufCrmSmartInvoiceFinbgPaymentCount"
DATE_FIELD = "ufCrmSmartInvoiceFinbgLastPaymentDate"
PAID_MONEY_FIELD = "ufCrmSmartInvoiceFinbgPaidMoney"
BALANCE_MONEY_FIELD = "ufCrmSmartInvoiceFinbgBalanceMoney"
BATCH_SIZE = 50
CENT = Decimal("0.01")


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def flatten(value: dict, prefix: str = "") -> list[tuple[str, object]]:
    pairs: list[tuple[str, object]] = []
    for key, item in value.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(item, dict):
            pairs.extend(flatten(item, name))
        elif isinstance(item, (list, tuple)):
            pairs.extend((f"{name}[{index}]", entry) for index, entry in enumerate(item))
        else:
            pairs.append((name, item))
    return pairs


def bitrix_call(base: str, method: str, params: dict, attempts: int = 5):
    request_data = urllib.parse.urlencode(flatten(params)).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            f"{base.rstrip('/')}/{method}.json",
            data=request_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if "error" not in payload:
                return payload.get("result", {})
            description = payload.get("error_description") or payload["error"]
            last_error = RuntimeError(f"{method}: {description}")
            if payload.get("error") not in {"QUERY_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"}:
                raise last_error
        except urllib.error.HTTPError as error:
            last_error = RuntimeError(f"{method}: HTTP {error.code}")
            if error.code not in {429, 500, 502, 503, 504}:
                raise last_error from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = RuntimeError(f"{method}: {error}")
        if attempt + 1 < attempts:
            time.sleep(1.5 * (attempt + 1))
    raise last_error or RuntimeError(f"{method}: unknown error")


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def display_money(value: Decimal, currency: str) -> str:
    formatted = f"{value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"
    formatted = formatted.replace(",", " ").replace(".", ",")
    symbol = "₽" if currency == "RUB" else "€" if currency == "EUR" else currency
    return f"{formatted} {symbol}"


def read_live_invoices(webhook: str, ids: list[int]) -> dict[int, dict]:
    invoices: dict[int, dict] = {}
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        result = bitrix_call(
            webhook,
            "crm.item.list",
            {
                "entityTypeId": ENTITY_TYPE_ID,
                "filter": {"@id": chunk},
                "select": ["id", "opportunity", "currencyId", "stageId"],
            },
        )
        for item in result.get("items", []):
            invoices[int(item["id"])] = item
    return invoices


def rate_for(connection, currency: str, payment_date: date) -> Decimal | None:
    if currency == "RUB":
        return Decimal("1")
    row = connection.execute(
        """
        select rate_to_rub
        from fx_rates
        where currency = %s and rate_date <= %s
        order by rate_date desc
        limit 1
        """,
        [currency, payment_date],
    ).fetchone()
    return Decimal(str(row["rate_to_rub"])) if row else None


def amount_in_invoice_currency(connection, payment: dict, currency: str):
    payment_currency = str(payment["currency"]).strip()
    if payment_currency == currency:
        return Decimal(str(payment["amount"])), None
    if payment_currency == "RUB":
        rate = rate_for(connection, currency, payment["payment_date"])
        if rate is None:
            return None, None
        converted = Decimal(str(payment["amount_rub"])) / rate
        return converted.quantize(CENT, rounding=ROUND_HALF_UP), rate
    return None, None


def existing_comment_markers(webhook: str, invoice_id: int) -> str:
    result = bitrix_call(
        webhook,
        "crm.timeline.comment.list",
        {
            "filter": {
                "ENTITY_ID": invoice_id,
                "ENTITY_TYPE": TIMELINE_ENTITY_TYPE,
            },
            "select": ["ID", "COMMENT"],
        },
    )
    items = result if isinstance(result, list) else result.get("items", [])
    return "\n".join(item.get("COMMENT") or "" for item in items)


def add_payment_comment(webhook: str, invoice_id: int, payment: dict, change: dict) -> int | str:
    credited = payment["credited_amount"]
    currency = change["currency"]
    lines = [
        f"Платёж FinBG #{payment['id']}",
        f"Счёт: {invoice_id}",
        f"Плательщик: {payment['raw_counterparty'] or '—'}",
        f"Дата оплаты: {payment['payment_date'].strftime('%d.%m.%Y')}",
        f"Зачтено в счёт: {display_money(credited, currency)}",
    ]
    if currency != "RUB":
        lines.append(f"Поступило: {display_money(Decimal(str(payment['amount_rub'])), 'RUB')}")
        if payment["rate"] is not None:
            lines.append(f"Курс ЦБ РФ на дату платежа: {payment['rate']:.4f} ₽")
    lines.extend(
        [
            f"Назначение: {payment['description'] or '—'}",
            f"Итого оплачено по счёту: {display_money(change['paid'], currency)}",
            f"Остаток: {display_money(change['balance'], currency)}",
            "Статус: " + ("оплачен полностью" if change["balance"] == 0 else "частично оплачен"),
        ]
    )
    return bitrix_call(
        webhook,
        "crm.timeline.comment.add",
        {
            "fields": {
                "ENTITY_ID": invoice_id,
                "ENTITY_TYPE": TIMELINE_ENTITY_TYPE,
                "COMMENT": "\n".join(lines),
            }
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--env-file", default="/opt/finbg/.secrets/bitrix.env")
    parser.add_argument("--invoice-id", type=int, action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_env(args.env_file)
    webhook = os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook:
        parser.error("BITRIX_WEBHOOK_URL is not set")

    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        params: list[object] = []
        invoice_filter = ""
        if args.invoice_id:
            invoice_filter = "and assignment.bitrix_invoice_id = any(%s)"
            params.append(sorted(set(args.invoice_id)))
        payments = connection.execute(
            f"""
            select
                payment.id,
                payment.payment_date,
                payment.amount,
                payment.currency,
                payment.amount_rub,
                payment.raw_counterparty,
                payment.description,
                assignment.bitrix_invoice_id
            from payment_manager_assignments assignment
            join payments payment on payment.id = assignment.payment_id
            where payment.source = 'payment_battery'
              and payment.direction = 'inflow'
              and payment.status = 'posted'
              and not payment.is_internal_transfer
              {invoice_filter}
            order by assignment.bitrix_invoice_id, payment.payment_date, payment.id
            """,
            params,
        ).fetchall()
        known_invoice_ids = {
            int(row["bitrix_invoice_id"])
            for row in connection.execute("select bitrix_invoice_id from bitrix_invoices")
        }

        grouped: dict[int, list[dict]] = defaultdict(list)
        ambiguous_payment_ids: list[int] = []
        for payment in payments:
            referenced = [
                number
                for number in invoice_numbers(payment["description"])
                if number in known_invoice_ids
            ]
            if len(set(referenced)) > 1:
                ambiguous_payment_ids.append(payment["id"])
                continue
            grouped[int(payment["bitrix_invoice_id"])].append(payment)

        invoice_ids = sorted(grouped)
        live_invoices = read_live_invoices(webhook, invoice_ids)
        changes: list[dict] = []
        skipped: list[dict] = []

        for invoice_id in invoice_ids:
            invoice = live_invoices.get(invoice_id)
            if invoice is None:
                skipped.append({"invoice_id": invoice_id, "reason": "not_found_in_bitrix"})
                continue
            currency = str(invoice.get("currencyId") or "").strip()
            if currency not in {"RUB", "EUR"}:
                skipped.append({"invoice_id": invoice_id, "reason": f"unsupported_currency:{currency}"})
                continue
            invoice_total = Decimal(str(invoice.get("opportunity") or "0"))
            credited_payments: list[dict] = []
            conversion_failed = False
            for payment in grouped[invoice_id]:
                credited, rate = amount_in_invoice_currency(connection, payment, currency)
                if credited is None:
                    conversion_failed = True
                    break
                enriched = dict(payment)
                enriched["credited_amount"] = credited
                enriched["rate"] = rate
                credited_payments.append(enriched)
            if conversion_failed:
                skipped.append({"invoice_id": invoice_id, "reason": "missing_rate_or_currency_conversion"})
                continue
            paid = sum((row["credited_amount"] for row in credited_payments), Decimal("0"))
            paid = paid.quantize(CENT, rounding=ROUND_HALF_UP)
            difference = (invoice_total - paid).quantize(CENT, rounding=ROUND_HALF_UP)
            if difference < 0:
                skipped.append(
                    {
                        "invoice_id": invoice_id,
                        "reason": "overpayment_requires_allocation",
                        "invoice_total": money(invoice_total),
                        "paid": money(paid),
                    }
                )
                continue
            balance = Decimal("0.00") if abs(difference) <= CENT else difference
            changes.append(
                {
                    "invoice_id": invoice_id,
                    "currency": currency,
                    "invoice_total": invoice_total,
                    "paid": paid,
                    "balance": balance,
                    "payment_count": len(credited_payments),
                    "last_payment_date": max(row["payment_date"] for row in credited_payments),
                    "stage_id": PAID_STAGE if balance == 0 else PARTIAL_STAGE,
                    "payments": credited_payments,
                }
            )

        report = {
            "mode": "apply" if args.apply else "dry-run",
            "assigned_payments": len(payments),
            "ambiguous_payments": ambiguous_payment_ids,
            "invoices_ready": len(changes),
            "fully_paid": sum(change["balance"] == 0 for change in changes),
            "partially_paid": sum(change["balance"] > 0 for change in changes),
            "skipped": skipped,
            "preview": [
                {
                    "invoice_id": change["invoice_id"],
                    "currency": change["currency"],
                    "paid": money(change["paid"]),
                    "balance": money(change["balance"]),
                    "payment_count": change["payment_count"],
                    "stage_id": change["stage_id"],
                }
                for change in changes
            ],
        }

        if not args.apply:
            print(json.dumps(report, ensure_ascii=False))
            return 0

        comments_added = 0
        comments_existing = 0
        for change in changes:
            invoice_id = change["invoice_id"]
            fields = {
                PAID_FIELD: money(change["paid"]),
                BALANCE_FIELD: money(change["balance"]),
                PAID_MONEY_FIELD: f"{money(change['paid'])}|{change['currency']}",
                BALANCE_MONEY_FIELD: f"{money(change['balance'])}|{change['currency']}",
                COUNT_FIELD: change["payment_count"],
                DATE_FIELD: change["last_payment_date"].isoformat(),
                "stageId": change["stage_id"],
            }
            bitrix_call(
                webhook,
                "crm.item.update",
                {"entityTypeId": ENTITY_TYPE_ID, "id": invoice_id, "fields": fields},
            )
            existing_comments = existing_comment_markers(webhook, invoice_id)
            for payment in change["payments"]:
                marker = f"Платёж FinBG #{payment['id']}"
                if marker in existing_comments:
                    comments_existing += 1
                    continue
                add_payment_comment(webhook, invoice_id, payment, change)
                comments_added += 1

        report["comments_added"] = comments_added
        report["comments_existing"] = comments_existing
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
