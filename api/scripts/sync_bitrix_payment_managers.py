"""Assign incoming payments to managers through referenced Bitrix invoices."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from psycopg.rows import dict_row


INVOICE_RE = re.compile(
    r"\b(?:сч[её]т(?:у|а|ом)?|сч\.?)(?!\s*-\s*ф)"
    r"\s*(?:№+\s*:?\s*)?(\d{1,10})\b",
    re.IGNORECASE,
)

# Bitrix throttles webhooks to ~2 req/s; batch invoice lookups so a run over
# hundreds of payments doesn't turn into one request per invoice number.
INVOICE_BATCH_SIZE = 50


class BitrixNotFound(Exception):
    """The requested entity does not exist (or the ID is invalid) in Bitrix."""


class BitrixCallError(Exception):
    """A real failure talking to Bitrix (network, 5xx, throttling, bad payload)."""


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def bitrix_call(base: str, method: str, params: dict | None = None) -> dict:
    payload = urllib.parse.urlencode(params or {}, doseq=True).encode()
    request = urllib.request.Request(base + method + ".json", data=payload)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 400:
            # Bitrix answers unknown/deleted IDs with a plain 400, not a JSON body.
            raise BitrixNotFound(f"{method}: HTTP 400 for params {params}") from error
        raise BitrixCallError(f"{method}: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BitrixCallError(f"{method}: {error}") from error
    if "error" in result:
        description = result.get("error_description") or result.get("error")
        raise BitrixCallError(f"{method}: {description}")
    return result.get("result", {})


def fetch_invoice_managers(
    webhook: str, numbers: list[int]
) -> tuple[dict[int, int], list[int]]:
    """Return {invoice_id: assigned_by_id} for a batch of invoice numbers.

    Numbers Bitrix doesn't recognise are silently absent from the result
    (that's a legitimate "no such invoice", not an error). Real failures
    (network, throttling, 5xx) are collected and returned so the caller can
    tell them apart from "not found" instead of treating both the same way.
    """
    assigned_by: dict[int, int] = {}
    failed_batches: list[int] = []
    unique = sorted(set(numbers))
    for start in range(0, len(unique), INVOICE_BATCH_SIZE):
        chunk = unique[start : start + INVOICE_BATCH_SIZE]
        try:
            result = bitrix_call(
                webhook,
                "crm.item.list",
                {
                    "entityTypeId": 31,
                    "filter": {"@id": chunk},
                    "select": ["id", "assignedById"],
                },
            )
        except BitrixNotFound:
            continue
        except BitrixCallError as error:
            print(f"bitrix invoice batch failed: {error}", file=sys.stderr)
            failed_batches.extend(chunk)
            continue
        for item in result.get("items", []):
            if item.get("assignedById"):
                assigned_by[int(item["id"])] = int(item["assignedById"])
    return assigned_by, failed_batches


def invoice_numbers(description: str | None) -> list[int]:
    if not description:
        return []
    return list(dict.fromkeys(int(value) for value in INVOICE_RE.findall(description)))


def user_name(user: dict) -> str:
    parts = [user.get("LAST_NAME"), user.get("NAME"), user.get("SECOND_NAME")]
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--env-file", default="/opt/finbg/.secrets/bitrix.env")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_env(args.env_file)
    webhook = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/") + "/"

    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        payments = connection.execute(
            """
            select p.id, p.description
            from payments p
            where p.source = 'payment_battery'
              and p.direction = 'inflow'
              and not p.is_internal_transfer
              and not exists (
                  select 1
                  from payment_manager_assignments assignment
                  where assignment.payment_id = p.id
              )
            order by p.id
            """
        ).fetchall()

        payment_numbers = {
            payment["id"]: invoice_numbers(payment["description"])
            for payment in payments
        }
        all_numbers = [n for numbers in payment_numbers.values() for n in numbers]
        assigned_by, failed_numbers = fetch_invoice_managers(webhook, all_numbers)
        failed_set = set(failed_numbers)

        matched: list[tuple[dict, int, int, dict]] = []
        not_found = 0
        ambiguous = 0
        lookup_errors = 0
        user_cache: dict[int, dict] = {}
        for payment in payments:
            numbers = payment_numbers[payment["id"]]
            if any(number in failed_set for number in numbers):
                lookup_errors += 1
                continue
            candidates = [
                (number, assigned_by[number]) for number in numbers if number in assigned_by
            ]
            managers = {manager_id for _, manager_id in candidates}
            if len(managers) == 1 and candidates:
                number, bitrix_user_id = candidates[0]
                if bitrix_user_id not in user_cache:
                    try:
                        users = bitrix_call(webhook, "user.get", {"ID": bitrix_user_id})
                        user_cache[bitrix_user_id] = users[0] if users else {}
                    except BitrixCallError as error:
                        print(f"bitrix user.get failed for {bitrix_user_id}: {error}", file=sys.stderr)
                        lookup_errors += 1
                        continue
                matched.append((payment, number, bitrix_user_id, user_cache[bitrix_user_id]))
            elif len(managers) > 1:
                ambiguous += 1
            elif numbers:
                not_found += 1

        if args.apply:
            employee_cache: dict[int, dict | None] = {}
            for payment, number, bitrix_user_id, user in matched:
                full_name = user_name(user) or f"Bitrix user {bitrix_user_id}"

                if bitrix_user_id not in employee_cache:
                    employee = connection.execute(
                        "select id, full_name from employees where bitrix_user_id = %s",
                        [bitrix_user_id],
                    ).fetchone()
                    if employee is None and full_name:
                        # First time we see this Bitrix user: link them to the
                        # employee record with the same name, if any, so future
                        # runs (and the deal-side manager column) agree on one
                        # canonical spelling instead of two independent copies.
                        employee = connection.execute(
                            """
                            update employees
                            set bitrix_user_id = %(bitrix_user_id)s
                            where lower(full_name) = lower(%(full_name)s)
                              and bitrix_user_id is null
                            returning id, full_name
                            """,
                            {"bitrix_user_id": bitrix_user_id, "full_name": full_name},
                        ).fetchone()
                    employee_cache[bitrix_user_id] = employee

                employee = employee_cache[bitrix_user_id]
                manager_name = employee["full_name"] if employee else full_name

                connection.execute(
                    """
                    insert into payment_manager_assignments (
                        payment_id, manager_name, document_number,
                        bitrix_invoice_id, bitrix_user_id, manager_id
                    )
                    values (
                        %(payment_id)s,
                        %(manager_name)s,
                        %(document_number)s,
                        %(bitrix_invoice_id)s,
                        %(bitrix_user_id)s,
                        %(manager_id)s
                    )
                    on conflict (payment_id) do update
                    set manager_name = excluded.manager_name,
                        document_number = excluded.document_number,
                        bitrix_invoice_id = excluded.bitrix_invoice_id,
                        bitrix_user_id = excluded.bitrix_user_id,
                        manager_id = excluded.manager_id,
                        matched_at = now()
                    """,
                    {
                        "payment_id": payment["id"],
                        "manager_name": manager_name,
                        "document_number": str(number),
                        "bitrix_invoice_id": number,
                        "bitrix_user_id": bitrix_user_id,
                        "manager_id": employee["id"] if employee else None,
                    },
                )

        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "payments_checked": len(payments),
                    "matched": len(matched),
                    "document_not_found": not_found,
                    "ambiguous_manager": ambiguous,
                    "lookup_errors": lookup_errors,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
