"""Забирает из Bitrix валюту и сумму счетов, на которые ссылаются наши сделки.

Зачем: счёт может быть выставлен в рублях, а может в евро, и от этого зависит
вся арифметика. Рублёвый счёт считается в рублях. Валютный — в своей валюте:
каждый платёж пересчитывается в неё по курсу ЦБ на дату платежа, и уже там
видно, закрыт счёт или осталась недоплата. В рублях такой счёт не сходится
никогда, потому что части оплачены по разным курсам.

Номер счёта в наших сделках совпадает с кодом записи в Bitrix — проверено на
217 счетах из 218. Внешний номер вида «2026/05/21/3643» сохраняем справочно.

Запускается по расписанию рядом с синхронизацией менеджеров. Ничего не удаляет:
пропавший из выдачи счёт остаётся в таблице, потому что сделки на него ссылаются.

Использование:
    python sync_bitrix_invoices.py            # показать, что изменится
    python sync_bitrix_invoices.py --apply    # записать в базу
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

# Bitrix ограничивает выдачу пятьюдесятью записями за запрос и притормаживает
# частые обращения — поэтому спрашиваем пачками, а не по одному счёту
BATCH_SIZE = 50


class BitrixCallError(Exception):
    """Сбой обращения к Bitrix. Отсутствие счёта в выдаче сбоем не считается:
    crm.item.list просто не возвращает неизвестные ему коды."""


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def flatten_params(params: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Раскрывает вложенные словари и списки в запись вида `filter[@id][0]=...`.

    Сам по себе urlencode так не умеет: получив {"filter": {"@id": [1, 2]}}, он
    пройдёт по ключам внутреннего словаря и отправит `filter=@id`, на что Bitrix
    ответит голым HTTP 400.
    """
    encoded: list[tuple[str, object]] = []
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            encoded.extend(flatten_params(value, full_key))
        elif isinstance(value, (list, tuple)):
            for index, entry in enumerate(value):
                encoded.append((f"{full_key}[{index}]", entry))
        else:
            encoded.append((full_key, value))
    return encoded


def bitrix_call(base: str, method: str, params: dict | None = None) -> dict:
    payload = urllib.parse.urlencode(flatten_params(params or {})).encode()
    request = urllib.request.Request(base + method + ".json", data=payload)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise BitrixCallError(f"{method}: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BitrixCallError(f"{method}: {error}") from error
    if "error" in result:
        description = result.get("error_description") or result.get("error")
        raise BitrixCallError(f"{method}: {description}")
    return result.get("result", {})


def wanted_invoice_numbers(connection) -> list[int]:
    """Номера счетов, на которые ссылаются сделки. Спецификации пропускаем:
    в Bitrix они пока не заводятся, счёт для них появится позже."""
    rows = connection.execute(
        """
        select distinct original_document_number::bigint as number
        from deals
        where original_document_type = 'счет'
          and original_document_number ~ '^[0-9]+$'
        order by 1
        """
    ).fetchall()
    return [row["number"] for row in rows]


def fetch_invoices(webhook: str, numbers: list[int]) -> tuple[list[dict], list[int]]:
    found: list[dict] = []
    failed: list[int] = []
    for start in range(0, len(numbers), BATCH_SIZE):
        chunk = numbers[start : start + BATCH_SIZE]
        try:
            result = bitrix_call(
                webhook,
                "crm.item.list",
                {
                    "entityTypeId": 31,
                    "filter": {"@id": chunk},
                    "select": [
                        "id",
                        "accountNumber",
                        "currencyId",
                        "opportunity",
                        "companyId",
                    ],
                },
            )
        except BitrixCallError as error:
            # сбой пачки — не повод считать её счета отсутствующими,
            # поэтому разделяем «не нашлось» и «не смогли спросить»
            print(f"пачка счетов не запросилась: {error}", file=sys.stderr)
            failed.extend(chunk)
            continue
        found.extend(result.get("items", []))
    return found, failed


def store(connection, items: list[dict]) -> int:
    rows = []
    for item in items:
        currency = str(item.get("currencyId") or "").strip().upper()
        if len(currency) != 3:
            # без валюты запись бессмысленна: именно она решает, как считать
            print(f"счёт {item.get('id')} без валюты — пропущен", file=sys.stderr)
            continue
        rows.append(
            (
                int(item["id"]),
                str(item.get("accountNumber") or "") or None,
                currency,
                Decimal(str(item.get("opportunity") or "0")),
                int(item["companyId"]) if item.get("companyId") else None,
            )
        )
    if not rows:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into bitrix_invoices (
                bitrix_invoice_id, account_number, currency, amount, company_id
            ) values (%s, %s, %s, %s, %s)
            on conflict (bitrix_invoice_id) do update set
                account_number = excluded.account_number,
                currency = excluded.currency,
                amount = excluded.amount,
                company_id = excluded.company_id,
                fetched_at = now()
            """,
            rows,
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--env-file", default="/opt/finbg/.secrets/bitrix.env")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    load_env(arguments.env_file)
    webhook_url = os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook_url:
        print("BITRIX_WEBHOOK_URL не задан", file=sys.stderr)
        return 2
    webhook = webhook_url.rstrip("/") + "/"

    with psycopg.connect(arguments.database_url, row_factory=dict_row) as connection:
        numbers = wanted_invoice_numbers(connection)
        print(f"счетов в сделках: {len(numbers)}")

        items, failed = fetch_invoices(webhook, numbers)
        by_currency: dict[str, int] = {}
        for item in items:
            code = str(item.get("currencyId") or "(нет)")
            by_currency[code] = by_currency.get(code, 0) + 1
        print(f"нашлось в Bitrix: {len(items)}, по валютам: {by_currency}")
        if failed:
            print(f"не удалось запросить: {len(failed)}", file=sys.stderr)

        if not arguments.apply:
            print("пробный прогон, в базу ничего не записано (нужен --apply)")
            return 1 if failed else 0

        stored = store(connection, items)
        connection.commit()
        print(f"записано счетов: {stored}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
