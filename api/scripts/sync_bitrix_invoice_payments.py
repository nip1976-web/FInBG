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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.documents import invoice_numbers  # noqa: E402
from app.payment_terms import stated_amounts, stated_rates  # noqa: E402


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
# Расхождение до двух единиц валюты счёта в любую сторону - округление, а не
# деньги: клиент платит рубли по курсу, округлённому до копеек, и каждый платёж
# округляется сам по себе. Такой счёт считаем закрытым, всё что больше - разбор
# человека, промежуточной зоны нет. Правило Николая от 14.09.2026; до него
# допуск был 1.00 и только на переплату, из-за чего счёт, закрытый двумя
# платежами, не сходился на 1,80 EUR и висел неоплаченным.
ROUNDING_TOLERANCE = Decimal("2.00")
# С 2026 года НДС вырос с 20% до 22%. Счёт, выставленный по ставке 20%, клиент
# оплачивает с доплатой разницы: сумма × 1,22 / 1,20, то есть ровно на
# «счёт × 2/120» больше. Это полная оплата, а не переплата.
VAT_TOPUP_SHARE = Decimal("2") / Decimal("120")
# Налог в счёте сидит внутри суммы: при ставке 20% это ровно её шестая часть.
# По этой доле и узнаём, что счёт выставлен ещё по старой ставке - дата счёта
# не годится, счёт 3065 выставлен 10.01.2026 и всё равно с НДС 20%.
VAT_20_SHARE = Decimal("1") / Decimal("6")
# Раньше этого дня доплачивать было нечего, ставка не менялась.
VAT_CHANGE_DATE = date(2026, 1, 1)
# Насколько курс, названный плательщиком, может отойти от курса ЦБ, чтобы ему
# ещё верить. Договорная надбавка и курс соседней даты укладываются в единицы
# процентов; чужое число из назначения промахивается в разы.
STATED_TERMS_TOLERANCE = Decimal("0.15")
STATED_SOURCE = "назначение платежа"
CBR_SOURCE = "ЦБ РФ на дату платежа"


def settle(invoice_total: Decimal, paid: Decimal) -> Decimal | None:
    """Остаток по счёту, или None, если оплачено заметно больше счёта."""
    difference = (invoice_total - paid).quantize(CENT, rounding=ROUND_HALF_UP)
    if difference < -ROUNDING_TOLERANCE:
        return None
    return Decimal("0.00") if difference <= ROUNDING_TOLERANCE else difference


def split_shares(payment_amount: Decimal, invoice_totals: list[Decimal]) -> list[Decimal] | None:
    """Доли платежа по счетам, если он закрывает их все разом, иначе None.

    Одной платёжкой часто закрывают два-три счёта и перечисляют их в
    назначении: «по счетам 1395, 1401». Делим только тогда, когда суммы
    названных счетов складываются в платёж, - делёж доказывает сам себя, как
    совпадение в евро доказывало спорные сделки. Не сошлось - разносит
    человек: гадать, какую часть платежа отнести к какому счёту, программа не
    должна.
    """
    if len(invoice_totals) < 2 or any(total <= 0 for total in invoice_totals):
        return None
    difference = (payment_amount - sum(invoice_totals)).quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(difference) > ROUNDING_TOLERANCE:
        return None
    return invoice_totals


def split_payment(
    connection, payment: dict, numbers: list[int], live_invoices: dict[int, dict]
) -> list[tuple[int, dict]] | None:
    """Платёж, разложенный по названным счетам, или None, если делить не по чему.

    Каждому счёту достаётся его собственная сумма - в рублях платежа, по курсу
    ЦБ на дату платежа для валютных счетов.
    """
    amount_rub = Decimal(str(payment["amount_rub"]))
    ordered = list(dict.fromkeys(numbers))
    totals: list[Decimal] = []
    for number in ordered:
        invoice = live_invoices.get(number)
        if invoice is None:
            return None
        currency = str(invoice.get("currencyId") or "").strip()
        rate = rate_for(connection, currency, payment["payment_date"])
        if rate is None:
            return None
        total = Decimal(str(invoice.get("opportunity") or "0"))
        totals.append((total * rate).quantize(CENT, rounding=ROUND_HALF_UP))

    shares = split_shares(amount_rub, totals)
    if shares is None:
        return None

    payment_currency = str(payment["currency"]).strip()
    parts: list[tuple[int, dict]] = []
    for number, share_rub in zip(ordered, shares):
        part = dict(payment)
        part["amount_rub"] = share_rub
        part["amount"] = (
            share_rub
            if payment_currency == "RUB"
            else (Decimal(str(payment["amount"])) * share_rub / amount_rub).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
        )
        part["split_total_rub"] = amount_rub
        part["split_invoices"] = ordered
        parts.append((number, part))
    return parts


def invoice_matching_amount(
    connection, payment: dict, numbers: list[int], live_invoices: dict[int, dict]
) -> int | None:
    """Единственный из названных счетов, чья сумма равна платежу, иначе None.

    «Оплата по счету №2955 ..., счет №2957 ...» - клиент назвал оба счёта в
    обеих платёжках, но одна равна первому счёту, вторая второму. Совпадение
    до копейки и решает, где чьи деньги.
    """
    amount_rub = Decimal(str(payment["amount_rub"]))
    exact: list[int] = []
    for number in dict.fromkeys(numbers):
        invoice = live_invoices.get(number)
        if invoice is None:
            continue
        rate = rate_for(connection, str(invoice.get("currencyId") or "").strip(), payment["payment_date"])
        if rate is None:
            continue
        total = (Decimal(str(invoice.get("opportunity") or "0")) * rate).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        if total > 0 and abs(amount_rub - total) <= ROUNDING_TOLERANCE:
            exact.append(number)
    return exact[0] if len(exact) == 1 else None


def tax_value_of(invoice: dict) -> Decimal | None:
    """Сумма налога из карточки счёта. Пусто и мусор читаем как «неизвестно»."""
    raw = invoice.get("taxValue")
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def vat_topup(invoice_total: Decimal, tax_value: Decimal | None) -> Decimal | None:
    """Доплата НДС по счёту, выставленному по ставке 20%, или None."""
    if tax_value is None or invoice_total <= 0:
        return None
    expected_tax = (invoice_total * VAT_20_SHARE).quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(tax_value - expected_tax) > CENT:
        return None
    return (invoice_total * VAT_TOPUP_SHARE).quantize(CENT, rounding=ROUND_HALF_UP)


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
                "select": ["id", "opportunity", "currencyId", "stageId", "taxValue"],
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


def credited_by_statement(
    description: str | None, amount_rub: Decimal, cbr_rate: Decimal
) -> tuple[Decimal, Decimal] | None:
    """Сколько валюты плательщик назвал сам: (сумма, курс) или None.

    Сумму в евро предпочитаем курсу - это прямая цифра клиента, а не наш
    пересчёт. Каждого кандидата сверяем с курсом ЦБ: договорная надбавка и
    курс другой даты дают единицы процентов, а случайное число из назначения
    (сумма другой спецификации, обрывок даты) - разы.
    """
    if cbr_rate <= 0 or amount_rub <= 0:
        return None
    for amount in stated_amounts(description):
        implied = amount_rub / amount
        if abs(implied / cbr_rate - 1) <= STATED_TERMS_TOLERANCE:
            return amount.quantize(CENT, rounding=ROUND_HALF_UP), implied
    for rate in stated_rates(description):
        if abs(rate / cbr_rate - 1) <= STATED_TERMS_TOLERANCE:
            return (amount_rub / rate).quantize(CENT, rounding=ROUND_HALF_UP), rate
    return None


def amount_in_invoice_currency(connection, payment: dict, currency: str):
    """(зачтено в валюте счёта, курс, чей курс) - или (None, None, None)."""
    payment_currency = str(payment["currency"]).strip()
    if payment_currency == currency:
        return Decimal(str(payment["amount"])), None, None
    if payment_currency == "RUB":
        rate = rate_for(connection, currency, payment["payment_date"])
        if rate is None:
            return None, None, None
        amount_rub = Decimal(str(payment["amount_rub"]))
        stated = credited_by_statement(payment["description"], amount_rub, rate)
        if stated is not None:
            credited, used = stated
            return credited, used, STATED_SOURCE
        return (amount_rub / rate).quantize(CENT, rounding=ROUND_HALF_UP), rate, CBR_SOURCE
    return None, None, None


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
    if payment.get("split_total_rub") is not None:
        others = ", ".join(
            str(number) for number in payment["split_invoices"] if number != invoice_id
        )
        lines.append(
            f"Часть платежа: всего поступило "
            f"{display_money(payment['split_total_rub'], 'RUB')}, "
            f"этой платёжкой закрыты также счета {others}"
        )
    if currency != "RUB":
        lines.append(f"Поступило: {display_money(Decimal(str(payment['amount_rub'])), 'RUB')}")
        if payment["rate"] is not None:
            source = payment.get("rate_source") or CBR_SOURCE
            label = (
                "Курс из назначения платежа"
                if source == STATED_SOURCE
                else "Курс ЦБ РФ на дату платежа"
            )
            lines.append(f"{label}: {payment['rate']:.4f} ₽")
    lines.extend(
        [
            f"Назначение: {payment['description'] or '—'}",
            f"Итого оплачено по счёту: {display_money(change['paid'], currency)}",
            f"Остаток: {display_money(change['balance'], currency)}",
            "Статус: " + ("оплачен полностью" if change["balance"] == 0 else "частично оплачен"),
        ]
    )
    if change.get("accepted_overpayment", Decimal("0")) > 0:
        lines.append(
            "Переплата "
            + display_money(change["accepted_overpayment"], currency)
            + " принята решением: счёт закрыт"
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
        # Переплаты, принятые человеком по одному счёту: клиент посчитал рубли
        # по своему курсу и дал на несколько евро больше, а счёт закрыт.
        accepted_overpayments = {
            int(row["bitrix_invoice_id"]): Decimal(str(row["accepted_amount"]))
            for row in connection.execute(
                "select bitrix_invoice_id, accepted_amount from bitrix_accepted_overpayments"
            )
        }

        # Номера из назначения спрашиваем у самого Bitrix, а не у местной
        # таблицы счетов: она держит не все - счета 1053 и 1051, которые
        # закрыты той же платёжкой, что и 1057, в ней отсутствуют, и платёж
        # молча оставался неделимым. Несуществующий номер просто не вернётся.
        parsed_by_payment = {
            payment["id"]: invoice_numbers(payment["description"]) for payment in payments
        }
        live_invoices = read_live_invoices(
            webhook,
            sorted(
                {int(payment["bitrix_invoice_id"]) for payment in payments}
                | {number for numbers in parsed_by_payment.values() for number in numbers}
            ),
        )
        referenced_by_payment = {
            payment_id: [number for number in numbers if number in live_invoices]
            for payment_id, numbers in parsed_by_payment.items()
        }

        grouped: dict[int, list[dict]] = defaultdict(list)
        unsplit_payments: list[dict] = []
        splits: list[dict] = []
        for payment in payments:
            referenced = referenced_by_payment[payment["id"]]
            if len(set(referenced)) > 1:
                parts = split_payment(connection, payment, referenced, live_invoices)
                if parts is None:
                    # Поделить не вышло. Тогда - счёт, которому платёж равен до
                    # копейки, а если и такого нет, остаёмся при том счёте, что
                    # проставил менеджер: это прежнее поведение, и деньги хотя
                    # бы не пропадают из выгрузки. Платёж попадает в отчёт,
                    # чтобы человек посмотрел.
                    single = invoice_matching_amount(
                        connection, payment, referenced, live_invoices
                    )
                    unsplit_payments.append(
                        {
                            "payment_id": payment["id"],
                            "amount": money(Decimal(str(payment["amount_rub"]))),
                            "named_invoices": sorted(set(referenced)),
                            "credited_to": single or int(payment["bitrix_invoice_id"]),
                            "reason": "сумма счёта совпала" if single else "зачтён по привязке менеджера",
                        }
                    )
                    grouped[single or int(payment["bitrix_invoice_id"])].append(payment)
                    continue
                splits.append(
                    {
                        "payment_id": payment["id"],
                        "amount": money(Decimal(str(payment["amount_rub"]))),
                        "invoices": [
                            {"invoice_id": invoice_id, "share": money(share["amount_rub"])}
                            for invoice_id, share in parts
                        ],
                    }
                )
                for invoice_id, share in parts:
                    grouped[invoice_id].append(share)
                continue
            grouped[int(payment["bitrix_invoice_id"])].append(payment)

        invoice_ids = sorted(grouped)
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
                credited, rate, rate_source = amount_in_invoice_currency(
                    connection, payment, currency
                )
                if credited is None:
                    conversion_failed = True
                    break
                enriched = dict(payment)
                enriched["credited_amount"] = credited
                enriched["rate"] = rate
                enriched["rate_source"] = rate_source
                credited_payments.append(enriched)
            if conversion_failed:
                skipped.append({"invoice_id": invoice_id, "reason": "missing_rate_or_currency_conversion"})
                continue
            paid = sum((row["credited_amount"] for row in credited_payments), Decimal("0"))
            paid = paid.quantize(CENT, rounding=ROUND_HALF_UP)
            paid_total = invoice_total
            topup = Decimal("0.00")
            accepted_excess = Decimal("0.00")
            balance = settle(invoice_total, paid)
            if balance is None:
                # Может быть, лишнее - это доплата НДС, а не переплата. Считаем
                # так, только если она закрывает счёт ровно: частичную доплату
                # признавать нельзя, иначе остаток придётся выдумывать.
                excess = (paid - invoice_total).quantize(CENT, rounding=ROUND_HALF_UP)
                expected = vat_topup(invoice_total, tax_value_of(invoice))
                paid_after_change = any(
                    row["payment_date"] >= VAT_CHANGE_DATE for row in credited_payments
                )
                if expected is not None and paid_after_change and abs(excess - expected) <= ROUNDING_TOLERANCE:
                    topup = expected
                    paid_total = invoice_total + topup
                    balance = Decimal("0.00")
                else:
                    # Переплата, принятая человеком по этому счёту. Сверяем с
                    # суммой решения: пришли новые деньги сверх неё - счёт
                    # снова на разбор, решение касалось прежних.
                    accepted = accepted_overpayments.get(invoice_id)
                    if accepted is not None and excess <= accepted + ROUNDING_TOLERANCE:
                        accepted_excess = excess
                        paid_total = paid
                        balance = Decimal("0.00")
            if balance is None:
                skipped.append(
                    {
                        "invoice_id": invoice_id,
                        "reason": "overpayment_requires_allocation",
                        "invoice_total": money(invoice_total),
                        "paid": money(paid),
                        "overpayment": money(
                            (paid - invoice_total).quantize(CENT, rounding=ROUND_HALF_UP)
                        ),
                    }
                )
                continue
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
                    # сколько списано на округление: плюс - клиент недодал,
                    # минус - переплатил. Ноль значит сошлось само, без допуска.
                    # Считаем от суммы, которую клиент должен был заплатить: со
                    # счётом по старой ставке это счёт плюс доплата НДС
                    "rounded_off": (paid_total - paid).quantize(CENT, rounding=ROUND_HALF_UP)
                    if balance == 0
                    else Decimal("0.00"),
                    "vat_topup": topup,
                    "accepted_overpayment": accepted_excess,
                }
            )

        report = {
            "mode": "apply" if args.apply else "dry-run",
            "assigned_payments": len(payments),
            "unsplit_payments": unsplit_payments,
            "splits": splits,
            "invoices_ready": len(changes),
            "fully_paid": sum(change["balance"] == 0 for change in changes),
            "partially_paid": sum(change["balance"] > 0 for change in changes),
            "vat_topups": [
                {
                    "invoice_id": change["invoice_id"],
                    "invoice_total": money(change["invoice_total"]),
                    "topup": money(change["vat_topup"]),
                    "paid": money(change["paid"]),
                }
                for change in changes
                if change["vat_topup"] > 0
            ],
            "skipped": skipped,
            "preview": [
                {
                    "invoice_id": change["invoice_id"],
                    "currency": change["currency"],
                    "paid": money(change["paid"]),
                    "balance": money(change["balance"]),
                    "payment_count": change["payment_count"],
                    "stage_id": change["stage_id"],
                    "rounded_off": money(change["rounded_off"]),
                    "vat_topup": money(change["vat_topup"]),
                    "accepted_overpayment": money(change["accepted_overpayment"]),
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
