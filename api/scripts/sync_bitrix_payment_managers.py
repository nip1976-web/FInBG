"""Assign incoming payments to managers through referenced Bitrix invoices."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Run as a plain script (systemd calls it by path), so the package root that
# holds `app` isn't on sys.path yet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.counterparties import same_company  # noqa: E402
from app.documents import as_invoice_number, invoice_numbers  # noqa: E402

# Bitrix throttles webhooks to ~2 req/s; batch invoice lookups so a run over
# hundreds of payments doesn't turn into one request per invoice number.
INVOICE_BATCH_SIZE = 50


class BitrixCallError(Exception):
    """A failure talking to Bitrix (network, bad request, throttling, 5xx).

    Invoice ids Bitrix doesn't know are *not* an error: crm.item.list simply
    omits them from the response.
    """


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
    """Expand nested dicts/lists into Bitrix's `filter[@id][0]=...` notation.

    urlencode alone can't do this: handed {"filter": {"@id": [1, 2]}} it
    iterates the inner dict's keys and sends `filter=@id`, which Bitrix
    rejects with a bare HTTP 400.
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


def fetch_company_names(webhook: str, company_ids: list[int]) -> dict[int, str]:
    """Resolve Bitrix company ids to titles, so an invoice can be checked
    against the payer instead of being trusted on its number alone."""
    names: dict[int, str] = {}
    unique = sorted({cid for cid in company_ids if cid})
    for start in range(0, len(unique), INVOICE_BATCH_SIZE):
        chunk = unique[start : start + INVOICE_BATCH_SIZE]
        try:
            result = bitrix_call(
                webhook,
                "crm.company.list",
                {"filter": {"@ID": chunk}, "select": ["ID", "TITLE"]},
            )
        except BitrixCallError as error:
            print(f"bitrix company batch failed: {error}", file=sys.stderr)
            continue
        for item in result if isinstance(result, list) else result.get("items", []):
            names[int(item["ID"])] = item.get("TITLE") or ""
    return names


def fetch_invoice_managers(
    webhook: str, numbers: list[int]
) -> tuple[dict[int, dict], list[int]]:
    """Return {invoice_id: {assigned_by, company_id}} for a batch of numbers.

    Numbers Bitrix doesn't recognise are silently absent from the result
    (that's a legitimate "no such invoice", not an error). Real failures
    (network, throttling, 5xx) are collected and returned so the caller can
    tell them apart from "not found" instead of treating both the same way.
    """
    assigned_by: dict[int, dict] = {}
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
                    "select": ["id", "assignedById", "companyId"],
                },
            )
        except BitrixCallError as error:
            print(f"bitrix invoice batch failed: {error}", file=sys.stderr)
            failed_batches.extend(chunk)
            continue
        for item in result.get("items", []):
            if item.get("assignedById"):
                assigned_by[int(item["id"])] = {
                    "assigned_by": int(item["assignedById"]),
                    "company_id": int(item["companyId"]) if item.get("companyId") else None,
                }
    return assigned_by, failed_batches


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
            select
                p.id,
                p.description,
                p.raw_counterparty,
                override.document_kind as override_kind,
                override.document_number as override_number
            from payments p
            left join payment_document_overrides override
              on override.payment_id = p.id
            where p.source = 'payment_battery'
              and p.direction = 'inflow'
              and not p.is_internal_transfer
              and not exists (
                  select 1
                  from payment_manager_assignments assignment
                  where assignment.payment_id = p.id
              )
              -- Marketplaces and similar channels quote their own order id,
              -- never our Bitrix invoice; looking those numbers up would
              -- either find nothing or, worse, hit an unrelated invoice.
              and not exists (
                  select 1
                  from bitrix_match_exclusions ex
                  where strpos(
                      lower(coalesce(p.raw_counterparty, '')),
                      lower(ex.counterparty_pattern)
                  ) > 0
              )
              -- Individually marked as "no such invoice in Bitrix" (issued by
              -- hand, predates Bitrix, ...).
              and not exists (
                  select 1
                  from payment_bitrix_skips skip
                  where skip.payment_id = p.id
              )
            order by p.id
            """
        ).fetchall()

        excluded_payments = connection.execute(
            """
            select count(*) as total
            from payments p
            where p.source = 'payment_battery'
              and p.direction = 'inflow'
              and not p.is_internal_transfer
              and exists (
                  select 1
                  from bitrix_match_exclusions ex
                  where strpos(
                      lower(coalesce(p.raw_counterparty, '')),
                      lower(ex.counterparty_pattern)
                  ) > 0
              )
            """
        ).fetchone()["total"]

        def numbers_for(payment: dict) -> list[int]:
            # A manual correction replaces what the description says: it is
            # there precisely because the parsed number was wrong or missing.
            corrected = as_invoice_number(
                payment["override_kind"], payment["override_number"]
            )
            if corrected is not None:
                return [corrected]
            if payment["override_kind"] is not None:
                # Marked as a спецификация by hand — not a Bitrix invoice.
                return []
            return invoice_numbers(payment["description"])

        payment_numbers = {payment["id"]: numbers_for(payment) for payment in payments}
        all_numbers = [n for numbers in payment_numbers.values() for n in numbers]
        assigned_by, failed_numbers = fetch_invoice_managers(webhook, all_numbers)
        failed_set = set(failed_numbers)
        company_names = fetch_company_names(
            webhook, [meta["company_id"] for meta in assigned_by.values()]
        )
        # Pairings a human already vouched for, where the two spellings are too
        # far apart for any automatic comparison.
        confirmed_links = {
            (row["payer_name"].strip().lower(), row["bitrix_company_id"])
            for row in connection.execute(
                "select payer_name, bitrix_company_id from bitrix_company_links"
            ).fetchall()
        }

        def payer_matches(payment: dict, meta: dict) -> bool:
            payer = (payment["raw_counterparty"] or "").strip()
            if (payer.lower(), meta["company_id"]) in confirmed_links:
                return True
            return same_company(payer, company_names.get(meta["company_id"] or -1, ""))

        matched: list[tuple[dict, int, int, dict]] = []
        not_found = 0
        ambiguous = 0
        lookup_errors = 0
        wrong_counterparty = 0
        user_cache: dict[int, dict] = {}
        for payment in payments:
            numbers = payment_numbers[payment["id"]]
            if any(number in failed_set for number in numbers):
                lookup_errors += 1
                continue
            found = [(number, assigned_by[number]) for number in numbers if number in assigned_by]
            # An invoice number alone proves nothing: anyone along the chain can
            # mistype it, and the number that results belongs to whichever
            # client happens to own it. Only trust it when Bitrix says that
            # invoice was issued to the company that actually paid.
            candidates = [(number, meta) for number, meta in found if payer_matches(payment, meta)]
            if found and not candidates:
                wrong_counterparty += 1
                continue
            managers = {meta["assigned_by"] for _, meta in candidates}
            if len(managers) == 1 and candidates:
                number, meta = candidates[0]
                bitrix_user_id = meta["assigned_by"]
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
                    "wrong_counterparty": wrong_counterparty,
                    "excluded_counterparties": excluded_payments,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
