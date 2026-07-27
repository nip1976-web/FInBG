"""Assign incoming payments to managers through referenced Bitrix invoices."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request

import psycopg
from psycopg.rows import dict_row


INVOICE_RE = re.compile(
    r"\b(?:сч[её]т(?:у|а|ом)?|сч\.?)(?!\s*-\s*ф)"
    r"\s*(?:№+\s*:?\s*)?(\d{1,10})\b",
    re.IGNORECASE,
)


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
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(f"{method}: {result['error_description']}")
    return result.get("result", {})


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

        matched: list[tuple[dict, int, int, dict]] = []
        not_found = 0
        ambiguous = 0
        user_cache: dict[int, dict] = {}
        for payment in payments:
            numbers = invoice_numbers(payment["description"])
            candidates: list[tuple[int, dict]] = []
            for number in numbers:
                try:
                    invoice = bitrix_call(
                        webhook,
                        "crm.item.get",
                        {"entityTypeId": 31, "id": number},
                    ).get("item", {})
                except Exception:
                    continue
                if invoice and int(invoice.get("id", 0)) == number and invoice.get("assignedById"):
                    candidates.append((number, invoice))
            managers = {int(item["assignedById"]) for _, item in candidates}
            if len(managers) == 1 and candidates:
                number, invoice = candidates[0]
                bitrix_user_id = int(invoice["assignedById"])
                if bitrix_user_id not in user_cache:
                    users = bitrix_call(webhook, "user.get", {"ID": bitrix_user_id})
                    user_cache[bitrix_user_id] = users[0] if users else {}
                matched.append((payment, number, bitrix_user_id, user_cache[bitrix_user_id]))
            elif len(managers) > 1:
                ambiguous += 1
            elif numbers:
                not_found += 1

        if args.apply:
            for payment, number, bitrix_user_id, user in matched:
                full_name = user_name(user) or f"Bitrix user {bitrix_user_id}"
                connection.execute(
                    """
                    insert into payment_manager_assignments (
                        payment_id, manager_name, document_number, bitrix_invoice_id
                    )
                    values (
                        %(payment_id)s,
                        %(manager_name)s,
                        %(document_number)s,
                        %(bitrix_invoice_id)s
                    )
                    on conflict (payment_id) do update
                    set manager_name = excluded.manager_name,
                        document_number = excluded.document_number,
                        bitrix_invoice_id = excluded.bitrix_invoice_id,
                        matched_at = now()
                    """,
                    {
                        "payment_id": payment["id"],
                        "manager_name": full_name,
                        "document_number": str(number),
                        "bitrix_invoice_id": number,
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
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
